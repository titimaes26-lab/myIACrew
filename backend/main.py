import asyncio
import os
import traceback
import uuid
import re
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from dataclasses import dataclass
from typing import Any, List, Literal, NamedTuple, Optional
from sqlalchemy import case, update as sql_update
from sqlmodel import Session, col, func, select

from crewquestion import (
    AppDevelopmentCrew, CrewStepError, MAX_PRIOR_TURNS_IN_CONTEXT, QualificationResult,
    build_conversation_context, track_execution_metrics, workflow_step_keys, resumable_prefix, RESUMABLE_STEPS,
    FINALIZATION_ROLE,
    AGENT_SECTION_SEPARATOR, AGENT_SECTION_REGEX_PATTERN, MAX_AGENT_OUTPUT_SIZE, MAX_AGENT_NAME_LENGTH,
)
from database import (
    create_db_and_tables, get_session, engine, Conversation, ExecutionHistory, AgentRun, ExecutionCheckpoint,
)
from agent_metrics import (
    ExecutionMetrics, agent_run_view, list_executions, split_by_period, build_agent_run_rows, flush_events, sort_pipeline, summarize, step_for_role,
)
from auth import get_current_user, close_http_client
import validation
from orphans import HEARTBEAT_RETRY_SECONDS, HEARTBEAT_SECONDS, sweep_stale_executions
from qa_report import final_verdict
from errors import (
    AppError, ErrorCode, ErrorInfo, classify_exception, code_for_status, error_body, http_status_for, is_retryable_status,
)
from github_tools import (
    verify_github_delivery, get_branch_head_sha, describe_partial_delivery, GitHubVerificationUnavailable,
    check_github_access, GitHubAccessProblem, DeliveredPullRequest, DeliveryIssue,
)
from delivery import render_partial_delivery_block

# 1. INSTANCIATION DE FASTAPI (Obligatoire au tout début !)
app = FastAPI(title="CrewAI App Development API")

# Références fortes vers les exécutions de crew en tâche de fond (voir _execute_crew_and_persist,
# lancée via asyncio.create_task dans execute_workflow) : un Task asyncio sans référence conservée
# ailleurs peut être ramassé par le garbage collector AVANT sa fin (piège classique documenté dans
# la doc asyncio elle-même) puisque asyncio.create_task ne retient qu'une référence FAIBLE en
# interne — une exécution de plusieurs minutes s'interromprait alors silencieusement dès le
# prochain passage du GC. task.add_done_callback(_background_tasks.discard) retire l'entrée une
# fois la tâche terminée, pour que cet ensemble ne grossisse pas indéfiniment sur la durée de vie
# du process.
_background_tasks: set[asyncio.Task] = set()
# Identifiants des exécutions réellement en cours dans CE process : le balayage des orphelines
# (orphans.py) ne les touche jamais, même après un long silence.
_active_execution_ids: set[int] = set()

# Limite le nombre d'exécutions de crew simultanées, TOUTES conversations confondues (le
# garde-fou par conversation dans execute_workflow n'empêche qu'UNE MÊME conversation d'avoir
# deux exécutions en vol, jamais plusieurs conversations différentes en parallèle). Nécessaire
# depuis que /api/execute ne bloque plus pour toute la durée d'une exécution (voir plus bas) :
# un client qui enchaîne des demandes sur plusieurs conversations différentes ne se heurte plus
# naturellement à la limite qu'imposait le nombre de connexions HTTP lentes qu'il pouvait
# maintenir ouvertes simultanément. Valeur volontairement basse : chaque exécution instancie son
# propre AppDevelopmentCrew (voir _execute_crew_and_persist), coûteux en mémoire sur ce service à
# ressources limitées (voir _CONTAINER_MEMORY_LIMIT_MB plus bas).
# Portée : UN SEUL process (un asyncio.Semaphore n'est jamais partagé entre workers/instances).
# Suffisant tant que ce service tourne en un seul worker Uvicorn sur une seule instance Render
# (le cas aujourd'hui) — passer à plusieurs workers ou à plusieurs instances romprait cette
# limite globale sans avertissement (chaque process aurait alors sa PROPRE limite de 2, portant
# le vrai plafond à 2 * nombre de process).
_MAX_CONCURRENT_EXECUTIONS = 2
_execution_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_EXECUTIONS)

# Délai (best-effort, voir on_shutdown) accordé aux exécutions de crew encore en tâche de fond
# pour se terminer avant que le process ne s'arrête. Constante de module (comme
# _MAX_CONCURRENT_EXECUTIONS ci-dessus), pas locale à on_shutdown, pour rester visible/réutilisable
# sans dupliquer sa valeur (ex: un futur endpoint de santé qui voudrait l'exposer).
_SHUTDOWN_DRAIN_TIMEOUT_S = 20

_BACKFILL_BATCH = 200

def _backfill_qa_verdicts() -> None:
    """Rattrape `qa_verdict` des exécutions réussies d'avant la colonne, depuis le texte de leur résultat, pour que
    la qualité dans le temps couvre l'historique. Une exécution sans verdict (aucun QA) reste à NULL et est
    relue à chaque démarrage : le coût est borné par lots. Best-effort."""
    try:
        with Session(engine) as backfill:
            last_id = 0
            while True:
                rows = backfill.exec(
                    select(ExecutionHistory.id, ExecutionHistory.result)
                    .where(ExecutionHistory.status == "success")
                    .where(col(ExecutionHistory.qa_verdict).is_(None))
                    .where(ExecutionHistory.id > last_id)
                    .order_by(col(ExecutionHistory.id))
                    .limit(_BACKFILL_BATCH)
                ).all()
                if not rows:
                    break
                last_id = rows[-1][0] or last_id
                for execution_id, result in rows:
                    verdict = final_verdict(result or "")
                    if verdict:
                        backfill.exec(
                            sql_update(ExecutionHistory).where(ExecutionHistory.id == execution_id).values(qa_verdict=verdict)
                        )
                backfill.commit()
    except Exception as e:
        print(f"AVERTISSEMENT : rattrapage des verdicts QA ignoré : {type(e).__name__}: {e}", flush=True)

# 2. ÉVÉNEMENT DE DÉMARRAGE (Création des tables BDD)
@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    _backfill_qa_verdicts()
    # Imprimé une seule fois, au démarrage : rend le plafond mémoire du conteneur visible dans les
    # logs Render dès le boot, sans attendre qu'une exécution déclenche la première ligne [MEM]
    # (_log_memory) — utile pour juger d'emblée si le plan actuel a une marge suffisante pour ce
    # type de charge (CrewAI + plusieurs agents Gemini), avant même de lancer quoi que ce soit.
    _log_memory("démarrage du service")
    # Libère les conversations bloquées par une exécution morte avec l'ancien process (crash, OOM,
    # redéploiement). Best-effort : ne doit jamais empêcher le service de démarrer.
    try:
        with Session(engine) as session:
            swept = sweep_stale_executions(session, active_ids=_active_execution_ids)
        if swept:
            print(f"Démarrage : {len(swept)} exécution(s) orpheline(s) marquée(s) interrompue(s) : {swept}", flush=True)
    except Exception as e:
        print(f"AVERTISSEMENT : balayage des exécutions orphelines impossible : {type(e).__name__}: {e}", flush=True)

# Ferme proprement le client HTTP partagé de auth.py (voir sa docstring) plutôt que de
# laisser ses connexions ouvertes à l'arrêt du process.
@app.on_event("shutdown")
async def on_shutdown():
    # Best-effort, PAS une garantie : une exécution de crew tourne désormais en tâche de fond
    # asyncio (_background_tasks), invisible pour le mécanisme de "graceful shutdown" d'Uvicorn —
    # celui-ci ne suit et n'attend que les requêtes HTTP en vol, jamais des Task créées par
    # l'application elle-même. Sans cette attente explicite ici, un redéploiement Render (SIGTERM)
    # pendant une exécution en cours laisserait sa ligne bloquée sur "running" pour toujours (voir
    # la limite déjà documentée dans execute_workflow). _SHUTDOWN_DRAIN_TIMEOUT_S est un compromis
    # : le délai réel accordé par Render entre SIGTERM et un SIGKILL forcé n'est pas connu
    # précisément d'ici, et une exécution DESIGN_AND_DEV/FEATURE peut de toute façon durer
    # plusieurs minutes — largement au-delà de ce que quelque délai raisonnable que ce soit ici
    # pourrait couvrir. Chaque seconde accordée reste néanmoins strictement plus utile que zéro :
    # une exécution sur le point de se terminer a ainsi une vraie chance de persister son résultat
    # avant l'arrêt, plutôt qu'aucune.
    if _background_tasks:
        print(
            f"Arrêt du service : attente (best-effort, jusqu'à {_SHUTDOWN_DRAIN_TIMEOUT_S}s) de "
            f"{len(_background_tasks)} exécution(s) de crew encore en tâche de fond...",
            flush=True,
        )
        await asyncio.wait(list(_background_tasks), timeout=_SHUTDOWN_DRAIN_TIMEOUT_S)
    await close_http_client()

# 3. CONFIGURATION CORS
# Constantes (pas juste inline dans add_middleware) : relues par _log_unhandled_exception plus
# bas, qui doit reproduire la même décision d'autorisation d'origine/identifiants que
# CORSMiddleware — une seule source de vérité, pour qu'un futur changement de l'une de ces deux
# valeurs ne puisse pas être oublié dans l'un des deux endroits qui en dépendent.
CORS_ALLOW_ORIGINS = ["*"]
CORS_ALLOW_CREDENTIALS = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Format d'erreur UNIQUE de l'API (voir errors.error_body) : {"detail", "code", "retryable"}.
# `detail` reste la clé historique lue par le frontend ; `code` permet d'agir selon la cause.
@app.exception_handler(AppError)
async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(exc.message, exc.code, exc.retryable),
    )

@app.exception_handler(StarletteHTTPException)
async def _handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    # Couvre aussi les HTTPException levées par FastAPI/Starlette elles-mêmes (404 de route,
    # 405...) et par auth.py : même format partout, en-têtes d'origine conservés (ex: WWW-Authenticate).
    structured: dict[str, Any] = {} if isinstance(exc.detail, str) else {"errors": exc.detail}
    message = exc.detail if isinstance(exc.detail, str) else "Requête refusée."
    return JSONResponse(
        status_code=exc.status_code,
        content=error_body(message, code_for_status(exc.status_code), is_retryable_status(exc.status_code), **structured),
        headers=getattr(exc, "headers", None),
    )

@app.exception_handler(RequestValidationError)
async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    # FastAPI renvoie par défaut `detail` = liste d'objets : illisible tel quel dans l'interface
    # (qui attend une chaîne). On le ramène à une phrase, la liste détaillée restant dans `errors`.
    def _readable(message: str) -> str:
        return message.removeprefix("Value error, ")

    problems = [
        {"field": ".".join(str(part) for part in err.get("loc", ()) if part != "body"), "message": _readable(str(err.get("msg", "")))}
        for err in exc.errors()
    ]
    summary = "; ".join(f"{p['field']} : {p['message']}" if p["field"] else p["message"] for p in problems[:3])
    return JSONResponse(
        status_code=422,
        content=error_body(f"Requête invalide — {summary}" if summary else "Requête invalide.", ErrorCode.VALIDATION_ERROR, errors=problems),
    )

# Filet de sécurité global : ne remplace PAS les try/except explicites des endpoints (ex:
# execute_workflow imprime déjà sa propre trace détaillée et marque db_entry "failed" avant de
# lever HTTPException — Starlette route un HTTPException vers le handler dédié que FastAPI
# enregistre lui-même, plus spécifique que celui-ci, donc ce handler-ci ne l'intercepte jamais).
# Couvre uniquement le cas où une exception échapperait à TOUT try/except existant (ex: bug dans
# une dépendance, erreur avant le try d'un endpoint) : sans ce filet, Starlette renvoie quand même
# un 500 par défaut, mais rien ne garantit que sa trace complète soit toujours visible en clair
# dans les logs applicatifs — exactement le genre de "500 sans aucune trace exploitable" observé
# sur une exécution réelle, qu'il ne faut plus jamais laisser invisible.
#
# Un handler enregistré sur la classe Exception NUE (comme ici) est extrait par Starlette dans
# ServerErrorMiddleware, qui enveloppe TOUT le reste — y compris CORSMiddleware ci-dessus — et non
# l'inverse : sa réponse ne passe donc JAMAIS par l'injection d'en-têtes CORS de CORSMiddleware.
# Sans les ajouter nous-mêmes ici, le navigateur d'un frontend cross-origin (allow_origins=["*"])
# rejetterait cette réponse 500 comme une erreur CORS opaque ("Failed to fetch" côté JS), cachant
# le vrai statut/contenu à l'utilisateur malgré un vrai 500 bien envoyé sur le fil — exactement le
# symptôme observé (log Render confirmant un 500, mais l'interface n'affiche qu'une erreur
# générique). allow_credentials=True interdit le littéral "*" : il faut réfléchir l'Origin exacte
# de la requête, comme le ferait CORSMiddleware lui-même.
@app.exception_handler(Exception)
async def _log_unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
    # Ce diagnostic (print/lecture mémoire) ne doit JAMAIS empêcher de renvoyer une réponse :
    # print() lui-même peut échouer (pipe de logs saturé/coupé, disque plein — plausible pile
    # dans les conditions de pression mémoire que ce diagnostic vise à détecter). Une exception
    # ICI, dans ce handler global lui-même, ne serait rattrapée par personne (ServerErrorMiddleware
    # ne retombe sur son propre filet de sécurité QUE quand aucun handler custom n'est enregistré,
    # ce qui n'est plus le cas dès qu'on en définit un) : la connexion serait alors abandonnée
    # sans aucune réponse, exactement le symptôme opaque ("Failed to fetch") que ce handler existe
    # pour éliminer.
    try:
        _log_memory(f"exception non gérée sur {request.url.path}")
        # flush=True : sys.stdout est bufferisé par bloc (pas par ligne) dès qu'il n'est pas
        # attaché à un terminal — le cas normal une fois redirigé vers les logs Render. Sans
        # vidage explicite, ce print() pourrait rester en mémoire tampon, jamais écrit, si le
        # process se termine brutalement juste après (ex: OOM kill, SIGKILL — qui ne laisse
        # aucune chance de vider ce tampon en sortie normale) : exactement le genre de trace
        # perdue que ce handler existe pour éviter.
        print(f"--- EXCEPTION NON GEREE SUR {request.method} {request.url.path} ---", flush=True)
        print(traceback.format_exc(), flush=True)
    except Exception:
        pass

    # Message générique (pas str(exc)) : contrairement aux deux endpoints qui choisissent
    # délibérément d'exposer le détail d'une erreur CrewAI connue, ce filet attrape N'IMPORTE
    # QUELLE exception non anticipée sur N'IMPORTE QUEL endpoint — le détail réel reste
    # disponible dans les logs Render (print ci-dessus), jamais renvoyé tel quel au client.
    response = JSONResponse(
        status_code=500,
        content=error_body("Erreur interne du serveur.", ErrorCode.INTERNAL_ERROR),
    )

    # Reproduit la décision d'autorisation d'origine de CORSMiddleware (voir CORS_ALLOW_ORIGINS) :
    # un handler enregistré sur la classe Exception nue est extrait par Starlette dans
    # ServerErrorMiddleware, qui enveloppe TOUT le reste — y compris CORSMiddleware — et non
    # l'inverse, donc cette réponse ne passe JAMAIS par l'injection d'en-têtes CORS habituelle.
    # Sans ça, le navigateur d'un frontend cross-origin rejetterait ce 500 comme une erreur CORS
    # opaque ("Failed to fetch" côté JS), cachant le vrai statut/contenu à l'utilisateur malgré un
    # vrai 500 bien envoyé sur le fil. CORS_ALLOW_CREDENTIALS=True interdit le littéral "*" : il
    # faut réfléchir l'Origin exacte de la requête (uniquement si elle est bien autorisée par
    # CORS_ALLOW_ORIGINS), plus Vary: Origin — comme le fait CORSMiddleware lui-même — pour qu'un
    # éventuel cache intermédiaire ne serve jamais la réponse d'une origine à une autre. Ne couvre
    # que allow_origins/allow_credentials (les seules options CORS utilisées ci-dessus) : un futur
    # allow_origin_regex sur CORSMiddleware devrait être répercuté ici aussi.
    origin = request.headers.get("origin")
    if origin and ("*" in CORS_ALLOW_ORIGINS or origin in CORS_ALLOW_ORIGINS):
        response.headers["Access-Control-Allow-Origin"] = origin
        if CORS_ALLOW_CREDENTIALS:
            response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Vary"] = "Origin"
    return response

crew_instance = AppDevelopmentCrew()

class UserRequestInput(BaseModel):
    user_request: str
    conversation_id: Optional[int] = None
    has_repo_target: bool = False

    _check_request = field_validator("user_request")(validation.validate_user_request)

class WorkflowExecutionInput(BaseModel):
    user_request: str
    target_workflow: str
    clarifications: Optional[str] = ""
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    base_branch: Optional[str] = "main"
    conversation_id: Optional[int] = None
    # Reprise : id d'une exécution en échec de CETTE conversation dont les étapes déjà réussies
    # (design, architecture, diagnostic) sont réutilisées au lieu d'être recalculées. Ignoré, sans
    # erreur, si cette exécution n'est pas reprenable (voir _resumable_outputs).
    resume_from_execution_id: Optional[int] = None

    # Refus immédiat (422, un message par champ) plutôt qu'une erreur découverte en pleine exécution.
    _check_request = field_validator("user_request")(validation.validate_user_request)
    _check_workflow = field_validator("target_workflow")(validation.validate_workflow)
    _check_clarifications = field_validator("clarifications")(validation.validate_clarifications)
    _check_owner = field_validator("repo_owner")(validation.validate_repo_owner)
    _check_repo = field_validator("repo_name")(validation.validate_repo_name)
    _check_branch = field_validator("base_branch")(validation.validate_branch_name)

class ConversationCreateInput(BaseModel):
    title: Optional[str] = None

# kind de GitHubAccessProblem -> (statut HTTP, code, réessayable)
_GITHUB_ACCESS_ERRORS = {
    "not_found": (404, ErrorCode.NOT_FOUND, False),
    "forbidden": (403, ErrorCode.FORBIDDEN, False),
    "invalid_token": (502, ErrorCode.GITHUB_UNAVAILABLE, False),
    "unavailable": (503, ErrorCode.GITHUB_UNAVAILABLE, True),
    "rate_limited": (503, ErrorCode.GITHUB_UNAVAILABLE, True),
    "missing_token": (500, ErrorCode.INTERNAL_ERROR, False),
}

_GITHUB_PRECHECK_TIMEOUT_S = 15
# Seconde tentative automatique (une seule) après un échec transitoire : délai avant de relancer.
AUTO_RETRY_DELAY_S = float(os.getenv("AUTO_RETRY_DELAY_S", "90"))
BULK_DELETE_MAX = 100
_SQL_INT_MAX = 2**31 - 1

class BulkDeleteInput(BaseModel):
    ids: List[int]

def _current_memory_mb() -> Optional[float]:
    """RSS (mémoire physique réellement utilisée par ce process) en Mo, lue depuis
    /proc/self/status (Linux uniquement — couvre tout environnement de déploiement réaliste ici :
    Render, Docker...). Best-effort : None si indisponible (OS différent, fichier absent) plutôt
    qu'une exception — un simple diagnostic ne doit jamais faire échouer une exécution par
    ailleurs saine.

    Ajouté pour diagnostiquer les cas où le process backend semble mourir sans laisser aucune
    trace applicative (voir _log_memory, appelé à chaque changement d'étape d'exécution) : sur le
    plan gratuit de Render, ni l'onglet "Events" (qui indiquerait un OOM kill explicitement) ni le
    graphique mémoire des "Metrics" ne sont accessibles, ce print() dans les logs applicatifs est
    donc le seul moyen de voir la tendance mémoire avant une éventuelle coupure brutale — un OOM
    kill (SIGKILL) tue le process instantanément, sans qu'aucune exception Python ne soit jamais
    levée ni journalisée : seules ces lectures PÉRIODIQUES avant le crash peuvent le suggérer
    (une dernière valeur déjà élevée juste avant l'arrêt net des logs), jamais une preuve directe.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024  # kB -> Mo
    except Exception:
        pass
    return None

class _MemoryLimit(NamedTuple):
    """`mb` : la valeur en Mo. `is_container_limit` : True si lue depuis un cgroup (le plafond
    RÉEL de ce conteneur), False si repli sur /proc/meminfo (MemTotal de la machine HÔTE,
    potentiellement partagée entre plusieurs services — une valeur sans rapport avec ce qui est
    réellement alloué ici, à ne jamais confondre avec un vrai plafond dans les logs)."""
    mb: float
    is_container_limit: bool

def _container_memory_limit_mb() -> Optional[_MemoryLimit]:
    """Plafond mémoire réellement appliqué à CE conteneur — lu depuis les cgroups Linux, le
    mécanisme que Docker/Render utilisent pour appliquer cette limite. Essaie cgroup v2
    (memory.max) puis v1 (memory.limit_in_bytes) ; ne retombe sur /proc/meminfo (MemTotal, la RAM
    de la machine hôte) que si aucun des deux n'est accessible ou n'indique de limite explicite —
    voir _MemoryLimit.is_container_limit, qui distingue ce cas pour que son appelant ne l'affiche
    jamais comme un vrai plafond de service.

    Ne renvoie que des valeurs STRICTEMENT positives (jamais 0 ni négatif) : un cgroup mal
    configuré ou lu en pleine transition d'arrêt du conteneur pourrait théoriquement exposer une
    limite de 0, qui diviserait par zéro chez l'appelant plutôt que de simplement dégrader vers
    "indisponible" comme n'importe quelle autre lecture ratée.

    Calculé une seule fois au démarrage (_CONTAINER_MEMORY_LIMIT_MB ci-dessous), jamais à chaque
    appel de _log_memory : ce plafond ne peut pas changer pendant la vie du process, inutile de
    rouvrir ces fichiers à chaque changement d'étape d'une exécution.
    """
    try:
        with open("/sys/fs/cgroup/memory.max") as f:
            raw = f.read().strip()
            if raw != "max":
                mb = int(raw) / (1024 * 1024)
                if mb > 0:
                    return _MemoryLimit(mb, True)
    except Exception:
        pass
    try:
        with open("/sys/fs/cgroup/memory/memory.limit_in_bytes") as f:
            raw_v1 = int(f.read().strip())
            # cgroup v1 représente "illimité" par une très grande valeur (pas un mot-clé explicite
            # comme "max" en v2) : un seuil large mais arbitraire écarte ce cas plutôt que
            # d'afficher une "limite" de plusieurs exaoctets, dénuée de sens pratique.
            if 0 < raw_v1 < (1 << 62):
                return _MemoryLimit(raw_v1 / (1024 * 1024), True)
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mb = int(line.split()[1]) / 1024
                    if mb > 0:
                        return _MemoryLimit(mb, False)
    except Exception:
        pass
    return None

# Calculé une seule fois à l'import (voir la docstring de _container_memory_limit_mb) : imprimé
# explicitement au démarrage (on_startup plus bas) pour que ce plafond soit visible même sans
# faire défiler les logs jusqu'à une exécution, et réutilisé par chaque ligne [MEM] (_log_memory)
# pour situer la RSS courante par rapport à ce plafond sans avoir à les rapprocher manuellement.
_CONTAINER_MEMORY_LIMIT_MB = _container_memory_limit_mb()

def _log_memory(context: str) -> None:
    # Best-effort, y compris print() lui-même : appelée depuis des points qui doivent absolument
    # ne jamais lever (le except d'execute_workflow avant que db_entry ne soit marqué "failed", et
    # _persist_current_step avant la classification CrewStepError — voir leurs docstrings). print()
    # peut échouer (pipe de logs saturé/coupé, disque plein) précisément dans les conditions de
    # pression mémoire que ce diagnostic vise à observer ; sans cette garde, l'échec d'un simple
    # print() de diagnostic ferait dérailler l'exécution qu'il essaie seulement d'observer.
    try:
        mem_mb = _current_memory_mb()
        if mem_mb is not None:
            if _CONTAINER_MEMORY_LIMIT_MB is not None:
                limit = _CONTAINER_MEMORY_LIMIT_MB
                pct = 100 * mem_mb / limit.mb
                # "plafond conteneur" seulement si RÉELLEMENT lu depuis un cgroup — sinon
                # "RAM machine hôte, PAS le plafond réel de ce service" : les deux ont des
                # implications opposées (3% d'un plafond conteneur de 512 Mo est alarmant tout
                # près de la limite, 3% d'une RAM hôte de 16 Go ne veut rien dire du tout).
                label = "plafond conteneur" if limit.is_container_limit else "RAM machine hôte, PAS le plafond réel de ce service"
                # flush=True : sys.stdout est bufferisé par bloc (pas par ligne) une fois
                # redirigé vers les logs Render (pas un terminal) — sans vidage explicite, cette
                # ligne pourrait rester en mémoire tampon et disparaître si le process se termine
                # brutalement juste après (OOM kill notamment, qui ne laisse aucune chance de
                # vider ce tampon), précisément la dernière lecture la plus utile à voir.
                print(f"[MEM] {context} : {mem_mb:.0f} Mo / {limit.mb:.0f} Mo ({pct:.0f}%) (RSS / {label})", flush=True)
            else:
                print(f"[MEM] {context} : {mem_mb:.0f} Mo (RSS) — plafond du conteneur indisponible", flush=True)
    except Exception:
        pass

_MEMORY_TICK_SECONDS = 2.0

async def _periodic_memory_logger(context: str) -> None:
    """Répète _log_memory(context) toutes les _MEMORY_TICK_SECONDS secondes, indéfiniment, jusqu'à
    ce que cette tâche asyncio soit annulée (task.cancel()) — voir son appelant, qui la lance en
    tâche de fond juste avant un kickoff_async potentiellement long, puis l'annule dès qu'il se
    termine (succès ou échec).

    Une granularité plus fine que les points [MEM] existants (uniquement au démarrage de la
    requête et à chaque CHANGEMENT DE TÂCHE du crew, voir _persist_current_step) est nécessaire
    pour repérer la tendance mémoire PENDANT une tâche unique, pas seulement entre deux tâches :
    le crash observé en pratique (voir PR #36) est survenu en plein streaming de la toute première
    tâche (design_task), avant qu'aucun changement d'étape n'ait eu l'occasion de déclencher la
    moindre ligne [MEM] existante — la dernière lecture disponible (au tout début de la requête)
    était alors déjà bien trop ancienne pour être utile.
    """
    while True:
        _log_memory(context)
        await asyncio.sleep(_MEMORY_TICK_SECONDS)

def _safe_refresh(session: Session, db_entry: ExecutionHistory, context: str) -> None:
    """Recharge db_entry depuis la base avant d'y réassigner des champs — voir ses appelants.

    Nécessaire avant de réassigner current_step à None (succès ou échec) : sans ce refresh, la
    Session de CETTE requête ignore les écritures faites entretemps par _persist_current_step via
    SA PROPRE Session (voir plus bas), et SQLAlchemy — comparant à sa valeur en mémoire périmée
    (None depuis la création de db_entry, jamais relue depuis) plutôt qu'à la valeur réellement en
    base (ex: 'qa') — omettrait purement et simplement cette colonne de l'UPDATE suivant.

    Un pépin ici (ex: connexion DB coupée par le pooler Postgres de Supabase après une longue
    exécution FEATURE/DESIGN_AND_DEV pendant laquelle cette Session est restée inactive) NE DOIT
    PAS empêcher d'enregistrer l'issue réelle de l'exécution : rollback() remet la Session dans un
    état utilisable pour le commit() qui suit (sans lui, celui-ci échouerait à son tour avec une
    erreur DIFFÉRENTE — sur la transaction invalidée, pas la connexion), et l'erreur n'est que
    loggée ; les assignations faites par l'appelant juste après restent valables dans tous les cas.
    """
    try:
        session.refresh(db_entry)
    except Exception as e:
        session.rollback()
        print(f"AVERTISSEMENT : échec du refresh de db_entry avant finalisation ({context}, id={db_entry.id}) : {type(e).__name__}: {e}", flush=True)

# --- TRACKER D'AGENTS PERSISTÉS POUR IDEMPOTENCE ---
# Structure: {execution_id: set(agent_names_persisted)}
# Évite les doublons si on_task_output_complete est appelé plusieurs fois pour le même agent
_persisted_agents: dict[int, set[str]] = {}

def _validate_agent_data(
    agent_name: str, agent_output: str, duration_seconds: Optional[float] = None
) -> tuple[str, str, Optional[float]]:
    """Valide et nettoie le nom d'agent, la sortie et la durée avant persistance.

    Returns:
        (cleaned_agent_name, cleaned_agent_output, cleaned_duration_seconds): données
        validées et nettoyées. cleaned_duration_seconds est None si la valeur reçue
        n'est pas un nombre fini et positif (durée manquante, NaN, infini, négative).
    """
    # Valider et nettoyer le nom d'agent
    if not agent_name:
        agent_name = "Agent"
    agent_name = agent_name.strip()
    # Retirer les newlines/caractères qui cassent le parsing
    agent_name = agent_name.replace('\n', ' ').replace('\r', ' ').replace('\t', ' ')
    # Limiter la longueur
    if len(agent_name) > MAX_AGENT_NAME_LENGTH:
        agent_name = agent_name[:MAX_AGENT_NAME_LENGTH]
    if not agent_name:
        agent_name = "Agent"

    # Valider et limiter la taille de l'output
    if len(agent_output) > MAX_AGENT_OUTPUT_SIZE:
        agent_output = agent_output[:MAX_AGENT_OUTPUT_SIZE] + f"\n\n**[Résultat tronqué - taille maximale atteinte ({MAX_AGENT_OUTPUT_SIZE} bytes)]**"

    # Valider la durée : uniquement un nombre fini >= 0, sinon considérée absente plutôt
    # que persistée telle quelle (ex: NaN/infini improbables mais pas impossibles selon
    # l'implémentation de execution_duration côté CrewAI).
    if not isinstance(duration_seconds, (int, float)) or isinstance(duration_seconds, bool):
        duration_seconds = None
    elif not (duration_seconds == duration_seconds) or duration_seconds in (float("inf"), float("-inf")) or duration_seconds < 0:
        duration_seconds = None

    return agent_name, agent_output, duration_seconds

def _persist_current_step(execution_id: int, step_key: Optional[str]) -> None:
    """Invoqué par run_dynamic_crew (voir crewquestion.py) à chaque changement d'étape.

    step_key=None efface la progression affichée (fin d'exécution, ou pause avant une nouvelle
    tentative suite à une erreur de quota — voir l'appel correspondant dans crewquestion.py).

    Ouvre sa propre Session plutôt que de réutiliser celle de la requête HTTP en cours : ce
    callback est appelé par CrewAI depuis le thread d'arrière-plan de kickoff_async (pas le
    thread de la requête FastAPI qui, lui, est simplement suspendu sur l'await), et une Session
    SQLAlchemy n'est pas conçue pour être partagée entre threads même sans accès concurrent réel.

    Best-effort, volontairement : appelé à la fois AVANT le try/except de run_dynamic_crew (pour
    annoncer la toute première étape) et depuis task_callback pendant kickoff_async, donc une
    exception ici non rattrapée remonterait soit sans passer par ce try/except du tout, soit
    empoisonnerait CrewStepError en désignant à tort l'agent en cours comme responsable de
    l'échec — dans les deux cas, une simple panne d'affichage de la progression ferait échouer
    (ou mal diagnostiquer) une exécution par ailleurs saine.
    """
    # Appelé à CHAQUE changement d'étape (donc plusieurs fois par exécution) : le point le plus
    # régulier disponible pour observer la tendance mémoire pendant une exécution DESIGN_AND_DEV/
    # FEATURE, qui peut enchaîner 4 tâches sur plusieurs minutes. Voir _current_memory_mb.
    _log_memory(f"execution_id={execution_id}, étape={step_key!r}")
    try:
        with Session(engine) as step_session:
            entry = step_session.get(ExecutionHistory, execution_id)
            if entry is not None:
                entry.current_step = step_key
                # Signe de vie : c'est ce qui distingue une exécution lente d'une exécution orpheline
                # (voir orphans.sweep_stale_executions).
                entry.updated_at = datetime.now(timezone.utc)
                step_session.add(entry)
                step_session.commit()
    except Exception as e:
        print(f"AVERTISSEMENT : échec de la mise à jour de la progression (execution_id={execution_id}, step={step_key!r}) : {type(e).__name__}: {e}", flush=True)

async def _partial_delivery_block(owner: str, repo: str, branch: str, base_branch: str, sha_before) -> str:
    """Bloc « Travail déjà présent sur GitHub » d'un échec. Ne lève jamais et reste borné dans le
    temps : constater l'état de GitHub ne doit ni masquer l'échec d'origine ni le retarder."""
    try:
        partial = await asyncio.wait_for(
            asyncio.to_thread(describe_partial_delivery, owner, repo, branch, base_branch, sha_before), timeout=20,
        )
        return render_partial_delivery_block(owner, repo, branch, base_branch, partial)
    except Exception as e:
        reason = "délai dépassé" if isinstance(e, (asyncio.TimeoutError, TimeoutError)) else (str(e) or type(e).__name__)
        return render_partial_delivery_block(owner, repo, branch, base_branch, None, reason)

def _set_attempts(execution_id: int, attempts: int) -> None:
    """Nombre de tentatives d'une exécution (2 dès qu'une relance automatique est décidée). Best-effort."""
    try:
        with Session(engine) as attempts_session:
            attempts_session.exec(
                sql_update(ExecutionHistory).where(ExecutionHistory.id == execution_id).values(attempts=attempts)
            )
            attempts_session.commit()
    except Exception as e:
        print(f"AVERTISSEMENT : nombre de tentatives non enregistré (execution_id={execution_id}) : {type(e).__name__}: {e}", flush=True)

def _touch_execution(execution_id: int) -> bool:
    """Signe de vie (updated_at) d'une exécution en cours. Best-effort ; True si écrit sans erreur.
    Un seul UPDATE conditionnel (status='running') : un battement tardif ne peut pas écraser le
    updated_at final d'une exécution déjà terminée (la durée affichée s'en déduit)."""
    try:
        with Session(engine) as heartbeat_session:
            heartbeat_session.exec(
                sql_update(ExecutionHistory)
                .where(ExecutionHistory.id == execution_id, ExecutionHistory.status == "running")
                .values(updated_at=datetime.now(timezone.utc))
            )
            heartbeat_session.commit()
        return True
    except Exception as e:
        print(f"AVERTISSEMENT : battement de l'exécution {execution_id} impossible : {type(e).__name__}: {e}", flush=True)
        return False

async def _heartbeat(execution_id: int) -> None:
    """Écrit un signe de vie toutes les HEARTBEAT_SECONDS tant que l'exécution tourne, même pendant
    une longue étape ou une pause de quota (sinon une instance voisine la croirait morte)."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if not await asyncio.to_thread(_touch_execution, execution_id):
            # Échec (base occupée, coupure brève) : un nouvel essai rapide plutôt que d'attendre un
            # battement entier, pour ne pas laisser updated_at vieillir jusqu'au seuil des orphelines.
            await asyncio.sleep(HEARTBEAT_RETRY_SECONDS)
            await asyncio.to_thread(_touch_execution, execution_id)

def _track_execution_task(task: asyncio.Task, execution_id: int) -> None:
    """Un seul endroit pour tout le suivi d'une tâche de fond : référence forte (anti-GC, voir
    _background_tasks), identifiant actif (jamais balayé comme orphelin) et battement de cœur."""
    heartbeat = asyncio.create_task(_heartbeat(execution_id))
    _background_tasks.add(task)
    _active_execution_ids.add(execution_id)

    def _done(_task: asyncio.Task) -> None:
        heartbeat.cancel()
        _background_tasks.discard(_task)
        _active_execution_ids.discard(execution_id)

    task.add_done_callback(_done)

def _persist_agent_runs(session: Session, db_entry: ExecutionHistory, run_metrics) -> None:
    """Une ligne AgentRun par agent mesuré (voir agent_metrics). Best-effort : une mesure qui ne
    s'enregistre pas ne doit jamais faire échouer ni masquer le résultat de l'exécution."""
    try:
        rows = build_agent_run_rows(
            run_metrics, execution_id=db_entry.id, conversation_id=db_entry.conversation_id,
            user_id=db_entry.user_id, workflow=db_entry.workflow,
        )
        session.add_all([AgentRun(**row) for row in rows])
    except Exception as e:
        print(f"AVERTISSEMENT : métriques par agent non enregistrées pour execution_id={db_entry.id} : {type(e).__name__}: {e}", flush=True)


def _persist_completed_agent(
    execution_id: int, agent_name: str, agent_output: str, duration_seconds: Optional[float] = None
) -> None:
    """Invoqué quand un agent complète sa tâche, pour accumuler les résultats progressifs.

    Formate la sortie de l'agent en markdown et l'ajoute au champ result existant via UPDATE SQL atomique,
    permettant au sondage /progress de retourner les agents complétés jusqu'à présent même pendant l'exécution.

    duration_seconds (temps d'exécution de l'agent, voir Task.execution_duration dans
    crewquestion.py) est encodé, quand connu, via le même marqueur HTML que celui écrit
    par _format_crew_result pour le résultat final — une seule logique d'extraction côté
    frontend suffit alors, que la section vienne du polling progressif ou du résultat final.

    Utilise un tracker d'idempotence pour éviter les doublons si retry_on_rate_limit_async relance le crew.
    Similaire à _persist_current_step : ouvre sa propre Session thread-safe et best-effort.
    """
    # Valider et nettoyer les données
    agent_name, agent_output, duration_seconds = _validate_agent_data(agent_name, agent_output, duration_seconds)

    # IDEMPOTENCE: Vérifier si cet agent a déjà été persisté pour cette exécution
    if execution_id not in _persisted_agents:
        _persisted_agents[execution_id] = set()

    if agent_name in _persisted_agents[execution_id]:
        print(f"execution_id={execution_id}: agent '{agent_name}' déjà persisté, skip (idempotence).", flush=True)
        return

    try:
        with Session(engine) as agent_session:
            entry = agent_session.get(ExecutionHistory, execution_id)
            if entry is not None:
                # Format identique à _format_crew_result dans crewquestion.py : sections séparées par AGENT_SECTION_SEPARATOR
                if duration_seconds is not None:
                    agent_section = f"## {agent_name}\n<!--agent-duration:{duration_seconds:.2f}-->\n\n{agent_output}"
                else:
                    agent_section = f"## {agent_name}\n\n{agent_output}"
                output_size = len(agent_output)

                # UPDATE SQL atomique au lieu de read-modify-write en Python
                # Cela évite les race conditions avec des écritures concurrentes
                if entry.result:
                    # Append avec le séparateur standard
                    new_result = entry.result + AGENT_SECTION_SEPARATOR + agent_section
                else:
                    # Première section : pas de séparateur au début
                    new_result = agent_section

                entry.result = new_result
                agent_session.add(entry)
                # Point de reprise : la sortie des étapes reprenables survit à un échec de l'exécution
                # (entry.result, lui, est remplacé par le message d'échec).
                step = step_for_role(agent_name)
                if step in RESUMABLE_STEPS:
                    agent_session.add(ExecutionCheckpoint(execution_id=execution_id, step=step, raw=agent_output))
                agent_session.commit()

                # Marquer l'agent comme persisté pour l'idempotence
                _persisted_agents[execution_id].add(agent_name)

                print(f"execution_id={execution_id}: agent '{agent_name}' persisté ({output_size} bytes).", flush=True)
    except Exception as e:
        print(f"AVERTISSEMENT : échec de la persistance de l'agent complété (execution_id={execution_id}, agent={agent_name!r}) : {type(e).__name__}: {e}", flush=True)

def _load_checkpoints(session: Session, execution_id: int) -> dict[str, str]:
    """{étape: sortie} sauvegardées pour une exécution (la plus récente gagne en cas de doublon)."""
    rows = session.exec(
        select(ExecutionCheckpoint).where(ExecutionCheckpoint.execution_id == execution_id).order_by(ExecutionCheckpoint.id)
    ).all()
    return {row.step: row.raw for row in rows}

def _load_checkpoints_for(execution_id: int) -> dict[str, str]:
    """_load_checkpoints avec sa propre Session (appelable depuis un thread, hors boucle asyncio)."""
    with Session(engine) as checkpoint_session:
        return _load_checkpoints(checkpoint_session, execution_id)

def _delete_checkpoints_for(execution_id: int) -> None:
    """Les points de reprise ne servent qu'à une exécution en échec : inutiles (et volumineux, le code
    complet de l'Analyste y figure) une fois celle-ci réussie. Best-effort."""
    try:
        with Session(engine) as cleanup_session:
            for checkpoint in cleanup_session.exec(
                select(ExecutionCheckpoint).where(ExecutionCheckpoint.execution_id == execution_id)
            ).all():
                cleanup_session.delete(checkpoint)
            cleanup_session.commit()
    except Exception as e:
        print(f"AVERTISSEMENT : purge des points de reprise impossible (execution_id={execution_id}) : {type(e).__name__}: {e}", flush=True)

def _fail_execution(execution_id: int, message: str) -> None:
    """Marque une exécution « failed » (interne) quand plus aucun autre chemin ne peut le faire. Best-effort."""
    try:
        with Session(engine) as fail_session:
            entry = fail_session.get(ExecutionHistory, execution_id)
            if entry is not None and entry.status == "running":
                entry.status = "failed"
                entry.result = message
                entry.current_step = None
                entry.error_code = ErrorCode.INTERNAL_ERROR
                entry.error_retryable = False
                entry.updated_at = datetime.now(timezone.utc)
                fail_session.add(entry)
                fail_session.commit()
    except Exception:
        pass

def _failed_before_development(exc: BaseException, request_type: str) -> bool:
    """Vrai si l'échec est celui d'une étape reprenable (design, architecture, diagnostic) : seul cas où
    une relance automatique ne réécrit rien sur GitHub. Faux pour tout échec hors étape (création du crew,
    vérification de livraison) ou à partir du développement."""
    if not isinstance(exc, CrewStepError):
        return False
    keys = workflow_step_keys(request_type)
    return 1 <= exc.step_index <= len(keys) and keys[exc.step_index - 1] in RESUMABLE_STEPS and exc.agent_role != FINALIZATION_ROLE

def _resumable_outputs(session: Session, data: "WorkflowExecutionInput", user_id, conversation_id: int) -> dict[str, str]:
    """Sorties réutilisables pour `data.resume_from_execution_id`, ou {} si cette exécution n'est pas
    reprenable : elle doit être en échec, de CETTE conversation et de CET utilisateur, avec le même
    workflow et le même repository cible (sinon ses étapes ne correspondent pas à celles de la nouvelle)."""
    if data.resume_from_execution_id is None:
        return {}
    previous = session.get(ExecutionHistory, data.resume_from_execution_id)
    if (
        previous is None or previous.user_id != user_id or previous.conversation_id != conversation_id
        or previous.status != "failed" or previous.workflow != data.target_workflow
        # Même demande : réutiliser design/architecture/code d'une AUTRE demande ferait committer du code pour
        # la mauvaise demande.
        or (previous.user_request or "").strip() != data.user_request.strip()
        or (previous.clarifications or "").strip() != (data.clarifications or "").strip()
        or (previous.repo_owner or None) != data.repo_owner or (previous.repo_name or None) != data.repo_name
        or (previous.base_branch or None) != ((data.base_branch or "main") if data.repo_owner and data.repo_name else None)
    ):
        return {}
    saved = _load_checkpoints(session, previous.id)
    prefix = resumable_prefix(workflow_step_keys(data.target_workflow), saved)
    return {key: saved[key] for key in prefix}

def _cleanup_persisted_agents(execution_id: int) -> None:
    """Nettoie le tracker d'agents persistés après que l'exécution soit terminée.

    Appelé après succès ou échec pour libérer la mémoire.
    """
    _persisted_agents.pop(execution_id, None)

async def _execute_crew_and_persist(
    db_entry_id: int,
    conversation_id: int,
    data: WorkflowExecutionInput,
    has_repo_target: bool,
    should_verify_github_delivery: bool,
    work_branch: str,
    normalized_base_branch: Optional[str],
    final_prompt: str,
    conversation_context: str,
    resume_outputs: Optional[dict[str, str]] = None,
) -> None:
    """Acquiert _execution_semaphore (voir sa définition : borne le nombre d'exécutions de crew
    simultanées, TOUTES conversations confondues) avant de lancer _run_crew_and_persist, qui porte
    toute la logique réelle (voir sa propre docstring) — séparée dans sa propre fonction plutôt que
    de tout indenter d'un niveau ici, pour un diff plus lisible que le simple ajout de ce garde-fou
    de concurrence globale ne justifierait pas autrement.

    "queued" persisté AVANT d'acquérir le sémaphore (jamais après) : si les 2 emplacements sont
    déjà occupés par d'autres exécutions, potentiellement longues de plusieurs minutes (voir
    _MAX_CONCURRENT_EXECUTIONS), cette exécution-ci peut rester bloquée ici un bon moment AVANT que
    le crew ne soit même instancié — current_step resterait alors None tout ce temps, ce que
    StepIndicator (frontend) interprète comme "aucun signal réel encore reçu" et comblerait par une
    estimation basée sur le temps écoulé, faisant défiler puis "terminer" toutes les étapes du
    workflow en quelques dizaines de secondes alors que rien n'a commencé. "queued" est une clé
    dédiée (jamais une clé réelle de WORKFLOW_STEPS côté frontend) : le tout premier appel à
    on_step_change une fois le crew réellement lancé (voir _run_crew_and_persist plus bas) l'écrase
    naturellement avec la vraie première étape, sans action supplémentaire ici.
    """
    await asyncio.to_thread(_persist_current_step, db_entry_id, "queued")
    async with _execution_semaphore:
        retry_outputs = await _run_crew_and_persist(
            db_entry_id, conversation_id, data, has_repo_target, should_verify_github_delivery,
            work_branch, normalized_base_branch, final_prompt, conversation_context,
            resume_outputs=resume_outputs,
        )
    if retry_outputs is not None:
        # Seconde tentative automatique : l'attente se fait HORS du sémaphore (et hors de toute Session
        # de base) pour ne bloquer ni un emplacement d'exécution ni une connexion pendant 90 s. « queued »
        # (et non None) pendant l'attente : None ferait simuler une progression par StepIndicator.
        try:
            await asyncio.to_thread(_persist_current_step, db_entry_id, "queued")
            await asyncio.to_thread(_set_attempts, db_entry_id, 2)
            await asyncio.sleep(AUTO_RETRY_DELAY_S)
            async with _execution_semaphore:
                await _run_crew_and_persist(
                    db_entry_id, conversation_id, data, has_repo_target, should_verify_github_delivery,
                    work_branch, normalized_base_branch, final_prompt, conversation_context,
                    resume_outputs=retry_outputs, auto_retry_allowed=False,
                )
        except BaseException as e:
            # Annulation pendant l'attente (arrêt du service) ou erreur imprévue avant la 2e tentative :
            # la ligne ne doit pas rester « running » et le suivi d'idempotence ne doit pas fuir.
            _fail_execution(db_entry_id, f"Exécution interrompue pendant l'attente de la nouvelle tentative : {type(e).__name__}")
            _cleanup_persisted_agents(db_entry_id)
            raise

@dataclass
class _RunState:
    """État d'UNE tentative d'exécution, lu par le chemin d'échec même quand le crew a planté en route :
    SHA de la branche de travail AVANT le crew (None tant que non capturé) et métriques de la tentative."""
    sha_before: Optional[str] = None
    metrics: Optional[ExecutionMetrics] = None


def _repo_instructions(
    has_repo_target: bool, data: "WorkflowExecutionInput", work_branch: str,
    base_branch: Optional[str], branch_exists: bool,
) -> str:
    """Consignes de cible données aux agents (repository, branches, ou espace de travail local)."""
    if not has_repo_target:
        return (
            "Aucun repository GitHub cible fourni : travaille uniquement dans l'espace de travail local de "
            "cette conversation (chemins de fichiers relatifs, lus avec read_a_files_content). Cet espace est "
            "VIDE au premier tour d'une conversation : ne présume jamais qu'un fichier non livré par un tour "
            "précédent de cette même conversation existe déjà. N'utilise aucun outil github_* SAUF "
            "github_commit_analyst_files et qa_verify_delivered_files, qui agissent alors sur cet espace."
        )
    header = (
        f"Repository GitHub cible : {data.repo_owner}/{data.repo_name}\n"
        f"Branche de base : {base_branch}\n"
        f"Branche de travail à créer et utiliser pour toute écriture : {work_branch}\n"
    )
    # branch_exists vient d'un appel GitHub LIVE fait avant le crew, pas du statut d'un tour précédent (un
    # tour « failed » peut avoir réellement poussé des commits). « Inconnu » est traité comme « n'existe
    # pas » : lire la branche de base en attendant est le choix le moins risqué.
    if branch_exists:
        return header + (
            "Cette branche de travail EXISTE DÉJÀ sur GitHub (réutilisée d'un tour précédent de cette "
            f"conversation) : pour toute lecture, lis-la directement avec branch={work_branch}."
        )
    return header + (
        "Cette branche de travail N'EXISTE PAS ENCORE sur GitHub (sera créée par la tâche de commit qui "
        f"suit) : pour toute lecture, lis sur branch={base_branch} en attendant."
    )


def _crew_inputs(
    data: "WorkflowExecutionInput", conversation_id: int, final_prompt: str, conversation_context: str,
    work_branch: str, base_branch: Optional[str], has_repo_target: bool, branch_exists: bool,
) -> dict:
    return {
        'user_request': final_prompt,
        'conversation_context': conversation_context,
        'repo_owner': data.repo_owner or '',
        'repo_name': data.repo_name or '',
        # `or 'main'` : base_branch est None sans repository cible, et les tâches interpolent toujours {base_branch}.
        'base_branch': base_branch or 'main',
        'work_branch': work_branch,
        # Isole l'espace de travail LOCAL de chaque conversation (mode sans repository cible).
        'conversation_id': str(conversation_id),
        'repo_instructions': _repo_instructions(has_repo_target, data, work_branch, base_branch, branch_exists),
    }


async def _capture_branch_sha(data: "WorkflowExecutionInput", work_branch: str, has_repo_target: bool) -> Optional[str]:
    """SHA de la branche de travail AVANT le crew : repère de verify_github_delivery pour distinguer « cette
    exécution a poussé un commit » de « une branche/PR d'un tour précédent existe toujours ». None = branche
    absente OU vérification indisponible : traité pareil (best-effort), une panne réseau n'empêche pas le crew."""
    # Pour TOUT run avec repository cible (pas seulement ceux qui vérifient la livraison) : les consignes
    # données aux agents dépendent de l'existence de la branche, ANALYSE_ONLY compris.
    if not has_repo_target:
        return None
    try:
        return await asyncio.to_thread(get_branch_head_sha, data.repo_owner, data.repo_name, work_branch)
    except GitHubVerificationUnavailable:
        return None


async def _run_crew(
    crew: AppDevelopmentCrew, state: _RunState, execution_id: int, request_type: str, inputs: dict,
    resume_outputs: Optional[dict[str, str]],
) -> Any:
    """Lance le crew avec ses métriques et son sondage mémoire périodique (toujours annulé, succès ou non)."""
    memory_ticker = asyncio.create_task(_periodic_memory_logger(f"execution_id={execution_id}, sondage périodique"))
    try:
        with track_execution_metrics() as run_metrics:
            state.metrics = run_metrics
            return await crew.run_dynamic_crew(
                inputs=inputs,
                request_type=request_type,
                on_step_change=lambda step_key: _persist_current_step(execution_id, step_key),
                on_task_output_complete=lambda agent_name, output, duration: _persist_completed_agent(
                    execution_id, agent_name, output, duration),
                resume_outputs=resume_outputs,
            )
    finally:
        # Les handlers d'événements CrewAI tournent dans un pool de threads : sans cette attente, les derniers
        # appels LLM pourraient manquer aux métriques lues ensuite.
        await asyncio.to_thread(flush_events)
        memory_ticker.cancel()
        try:
            await memory_ticker
        except asyncio.CancelledError:
            # Ne pas avaler l'annulation de CETTE tâche (indiscernable de celle du ticker sans cette
            # vérification) : l'exécution devrait alors s'arrêter, pas poursuivre vers « success ».
            current = asyncio.current_task()
            if current is not None and current.cancelling() > 0:
                raise


def _delivery_failure_message(issue: DeliveryIssue, raw_result: str) -> str:
    """Message d'une livraison non confirmée sur GitHub. `likely_access_problem` (champ structuré, pas un
    texte à parser) distingue « branche introuvable / API injoignable » (vérifier GITHUB_TOKEN est juste) du
    cas « branche et commits confirmés mais PR manquante » (l'agent n'a pas terminé : conseil de jeton faux).
    Le rapport de l'agent est joint tel quel, non vérifié, pour juger s'il faut relancer ou reformuler."""
    if issue.likely_access_problem:
        remediation = (
            "Vérifie la configuration GITHUB_TOKEN du backend (présence, permissions d'écriture sur ce "
            "repository) puis relance."
        )
    else:
        remediation = (
            "Cela peut venir d'un manque de permissions d'écriture du GITHUB_TOKEN configuré sur ce "
            "repository, ou du Développeur qui n'a pas terminé sa procédure GitHub : vérifie les deux, puis relance."
        )
    return (
        "Un repository GitHub cible était configuré mais la vérification après coup "
        f"a échoué : {issue.message} {remediation}\n\n"
        "--- Rapport de l'agent (non vérifié sur GitHub) ---\n"
        f"{raw_result[:3000]}"
    )


async def _verify_delivery(
    data: "WorkflowExecutionInput", work_branch: str, base_branch: Optional[str], sha_before: Optional[str],
    raw_result: str,
) -> Optional[DeliveredPullRequest]:
    """Vérifie via l'API GitHub (jamais d'après le texte d'un agent : la QA n'a aucun outil pour ça) qu'une
    branche et une PR à jour existent. Renvoie la PR confirmée ; lève RuntimeError sinon. Appel bloquant :
    dans un thread."""
    delivered_pr, issue = await asyncio.to_thread(
        verify_github_delivery, data.repo_owner, data.repo_name, work_branch, base_branch, sha_before,
    )
    if issue:
        raise RuntimeError(_delivery_failure_message(issue, raw_result))
    return delivered_pr


def _with_pull_request_line(raw_result: str, delivered_pr: Optional[DeliveredPullRequest]) -> str:
    """Ajoute l'URL de la PR réellement observée à la SUITE du résultat, sans nouveau séparateur de section :
    le frontend ne découpe que sur « \\n\\n---\\n\\n## », donc la ligne reste dans la dernière section."""
    if delivered_pr is None:
        return raw_result
    state_label = "fusionnée" if delivered_pr.merged else "ouverte"
    return f"{raw_result}\n\n**Pull Request {state_label} :** {delivered_pr.html_url}"


def _record_run_metrics(session: Session, db_entry: ExecutionHistory, state: _RunState) -> None:
    if state.metrics is None:
        return
    db_entry.api_calls_count = state.metrics.api_calls_count
    db_entry.rate_limit_hits = state.metrics.rate_limit_hits
    db_entry.total_wait_time_seconds = state.metrics.total_wait_time
    _persist_agent_runs(session, db_entry, state.metrics)


def _commit_outcome(session: Session, db_entry: ExecutionHistory, conversation: Conversation) -> None:
    now = datetime.now(timezone.utc)
    db_entry.updated_at = now
    conversation.updated_at = now
    session.add(db_entry)
    session.add(conversation)
    session.commit()


async def _persist_success(
    session: Session, db_entry: ExecutionHistory, conversation: Conversation, raw_result: str, state: _RunState,
) -> None:
    _safe_refresh(session, db_entry, "succès")
    db_entry.result = raw_result
    db_entry.status = "success"
    db_entry.qa_verdict = final_verdict(raw_result)
    db_entry.current_step = None
    _record_run_metrics(session, db_entry, state)
    _commit_outcome(session, db_entry, conversation)
    print(f"execution_id={db_entry.id} : terminée avec succès (tâche de fond).", flush=True)
    # Après le commit du succès, dans sa propre Session et au mieux : purger une ressource optionnelle ne
    # doit jamais faire échouer (ni être validée par) le chemin d'une exécution réussie.
    await asyncio.to_thread(_delete_checkpoints_for, db_entry.id)
    _cleanup_persisted_agents(db_entry.id)


async def _persist_success_safely(
    db_entry_id: int, conversation_id: int, session: Session, db_entry: ExecutionHistory,
    conversation: Conversation, raw_result: str, state: _RunState,
) -> None:
    """_persist_success, avec UNE reprise sur une Session neuve si la première tentative échoue (connexion
    périmée après une longue exécution). Si la reprise échoue aussi, l'erreur est journalisée et la ligne
    reste « running » : le balayage des orphelines la libérera, plutôt que de la déclarer en échec à tort."""
    try:
        await _persist_success(session, db_entry, conversation, raw_result, state)
        return
    except Exception as first_error:
        print(f"AVERTISSEMENT : validation du succès impossible (execution_id={db_entry_id}), nouvel essai : "
              f"{type(first_error).__name__}: {first_error}", flush=True)
    try:
        with Session(engine) as fresh:
            fresh_entry = fresh.get(ExecutionHistory, db_entry_id)
            fresh_conversation = fresh.get(Conversation, conversation_id)
            if fresh_entry is None or fresh_conversation is None:
                return
            await _persist_success(fresh, fresh_entry, fresh_conversation, raw_result, state)
    except Exception as second_error:
        print(f"ERREUR : succès non enregistré (execution_id={db_entry_id}) : {type(second_error).__name__}: "
              f"{second_error}", flush=True)
        _cleanup_persisted_agents(db_entry_id)


def _failure_detail(exc: BaseException, info: ErrorInfo) -> str:
    """Message d'échec : cause lisible (sinon texte d'origine) suivie du détail technique tronqué — sans lui,
    ni l'utilisateur ni la base ne diraient POURQUOI (ce que reproche un garde-fou, délai « retry after »)."""
    technical = str(exc).strip()[:500]
    reason = f"{info.message} (détail : {technical})" if info.message and technical else (info.message or technical)
    if isinstance(exc, CrewStepError):
        return f"Échec à l'étape {exc.step_index}/{exc.total_steps} ({exc.agent_role}) : {reason}"
    return reason


async def _persist_failure(
    session: Session, db_entry: ExecutionHistory, conversation: Conversation, exc: BaseException, info: ErrorInfo,
    data: "WorkflowExecutionInput", work_branch: str, base_branch: Optional[str], should_verify: bool,
    state: _RunState,
) -> None:
    detail = _failure_detail(exc, info)
    # Écritures GitHub partielles : l'échec peut venir après qu'une branche, des commits ou une PR ont DÉJÀ
    # été créés. Constaté via l'API (jamais d'après un agent) pour que l'utilisateur sache quoi reprendre.
    if should_verify and data.repo_owner and data.repo_name and work_branch:
        detail += "\n\n" + await _partial_delivery_block(
            data.repo_owner, data.repo_name, work_branch, base_branch or "main", state.sha_before,
        )
    _safe_refresh(session, db_entry, "échec")
    # current_step : run_dynamic_crew l'efface sur l'échec de kickoff, mais pas sur un échec APRÈS lui
    # (mise en forme, résumé) — d'où cet effacement ici.
    db_entry.current_step = None
    db_entry.status = "failed"
    db_entry.result = detail
    db_entry.error_code = info.code
    db_entry.error_retryable = info.retryable
    _record_run_metrics(session, db_entry, state)
    _commit_outcome(session, db_entry, conversation)
    _cleanup_persisted_agents(db_entry.id)


async def _retry_outputs_if_transient(
    exc: BaseException, info: ErrorInfo, db_entry_id: int, data: "WorkflowExecutionInput", auto_retry_allowed: bool,
) -> Optional[dict[str, str]]:
    """Sorties à réutiliser pour la seconde tentative AUTOMATIQUE (une seule), ou None (échec définitif).
    Seulement après un échec transitoire survenu AVANT l'écriture du code : rien n'a été poussé sur GitHub et
    les étapes réussies sont reprises. Plus tard (développement, QA, vérification), l'utilisateur relance :
    il faudrait réécrire sur GitHub et repayer ces étapes. Sans le point de reprise de CHAQUE étape déjà
    réussie, relancer les repayerait pour rien. L'attente se fait chez l'appelant, hors sémaphore."""
    # isinstance (en plus de _failed_before_development) : `exc.step_index` ci-dessous ne dépend ainsi pas
    # d'un couplage implicite entre ces deux conditions.
    if not isinstance(exc, CrewStepError):
        return None
    if not (auto_retry_allowed and info.retryable and _failed_before_development(exc, data.target_workflow)):
        return None
    saved = await asyncio.to_thread(_load_checkpoints_for, db_entry_id)
    prefix = resumable_prefix(workflow_step_keys(data.target_workflow), saved)
    if len(prefix) < exc.step_index - 1:
        return None
    print(
        f"execution_id={db_entry_id} : échec transitoire ({info.code}), nouvelle tentative "
        f"automatique dans {AUTO_RETRY_DELAY_S}s.", flush=True,
    )
    return {key: saved[key] for key in prefix}


def _mark_startup_failure(db_entry_id: int, exc: BaseException) -> None:
    """Dernier filet : l'ouverture de la Session ou les `get` initiaux ont échoué (pool épuisé, coupure base).
    Sans lui la ligne resterait « running » pour toujours. Best-effort : si la base est injoignable, rien de
    mieux n'est possible depuis ce process."""
    print(f"AVERTISSEMENT : échec du démarrage de la tâche de fond pour db_entry={db_entry_id} : {exc}", flush=True)
    try:
        with Session(engine) as session:
            db_entry = session.get(ExecutionHistory, db_entry_id)
            if db_entry is not None and db_entry.status == "running":
                db_entry.status = "failed"
                db_entry.result = f"Erreur interne au démarrage de l'exécution en tâche de fond : {exc}"
                db_entry.error_code = ErrorCode.INTERNAL_ERROR
                db_entry.error_retryable = False
                db_entry.current_step = None
                db_entry.updated_at = datetime.now(timezone.utc)
                session.add(db_entry)
                session.commit()
    except Exception:
        pass
    finally:
        _cleanup_persisted_agents(db_entry_id)


async def _run_crew_and_persist(
    db_entry_id: int,
    conversation_id: int,
    data: WorkflowExecutionInput,
    has_repo_target: bool,
    should_verify_github_delivery: bool,
    work_branch: str,
    normalized_base_branch: Optional[str],
    final_prompt: str,
    conversation_context: str,
    resume_outputs: Optional[dict[str, str]] = None,
    auto_retry_allowed: bool = True,
) -> Optional[dict[str, str]]:
    """Lance le crew et persiste son issue (succès ou échec) en base ; tourne en tâche de fond (voir
    execute_workflow, qui répond « running » tout de suite) avec sa PROPRE Session — celle de la requête est
    fermée bien avant la fin d'une exécution de plusieurs minutes. Renvoie les sorties à reprendre quand une
    seconde tentative automatique est demandée (voir _retry_outputs_if_transient), sinon None.

    Étapes : capturer le SHA de référence et instancier le crew → lancer (_run_crew) → vérifier la livraison
    GitHub → persister le succès ; toute exception passe par le chemin d'échec (_persist_failure).
    """
    state = _RunState()
    try:
        with Session(engine) as session:
            db_entry = session.get(ExecutionHistory, db_entry_id)
            conversation = session.get(Conversation, conversation_id)
            if db_entry is None or conversation is None:
                # Ne devrait jamais arriver (enregistrements tout juste commités par execute_workflow) : un
                # print plutôt qu'une exception que personne ne retrouverait (rien n'attend cette tâche).
                print(
                    f"AVERTISSEMENT : db_entry={db_entry_id} ou conversation={conversation_id} "
                    "introuvable au lancement de la tâche de fond, exécution abandonnée.",
                    flush=True,
                )
                return None

            try:
                # Instance FRAÎCHE par exécution (jamais le crew_instance partagé de /api/qualify) : CrewAI
                # mémoïse les tâches par id(self), un singleton ferait partager `.callback` et `.output` entre
                # deux exécutions concurrentes. Construite dans un thread (relecture des YAML, bloquante), en
                # parallèle de la capture du SHA ; DANS le try pour qu'une erreur ici marque l'exécution
                # « failed » au lieu de la laisser « running ».
                crew, state.sha_before = await asyncio.gather(
                    asyncio.to_thread(AppDevelopmentCrew),
                    _capture_branch_sha(data, work_branch, has_repo_target),
                )
                _log_memory(f"execution_id={db_entry.id}, crew instancié, avant kickoff")
                inputs = _crew_inputs(
                    data, conversation_id, final_prompt, conversation_context, work_branch,
                    normalized_base_branch, has_repo_target, state.sha_before is not None,
                )
                result = await _run_crew(crew, state, db_entry.id, data.target_workflow, inputs, resume_outputs)
                raw_result = str(result.raw) if hasattr(result, 'raw') else str(result)

                # Un rapport « réussi » ne prouve rien sur GitHub (un outil github_* en échec renvoie du texte à
                # l'agent, jamais une exception). Vérifié seulement quand du code a été écrit (pas ANALYSE_ONLY).
                delivered_pr = None
                if should_verify_github_delivery:
                    delivered_pr = await _verify_delivery(
                        data, work_branch, normalized_base_branch, state.sha_before, raw_result)
                raw_result = _with_pull_request_line(raw_result, delivered_pr)
            except Exception as e:
                _log_memory(f"execution_id={db_entry.id}, exception attrapée")
                print("--- ERREUR CREWAI EXECUTION DETECTEE ---", flush=True)
                print(traceback.format_exc(), flush=True)
                info = classify_exception(e)
                retry_outputs = await _retry_outputs_if_transient(e, info, db_entry.id, data, auto_retry_allowed)
                if retry_outputs is not None:
                    return retry_outputs
                await _persist_failure(
                    session, db_entry, conversation, e, info, data, work_branch, normalized_base_branch,
                    should_verify_github_delivery, state,
                )
            else:
                # Hors du try du crew : une erreur APRÈS le succès (connexion coupée par le pooler en pleine
                # validation) ne doit jamais faire passer en échec une exécution dont la PR est déjà ouverte
                # et vérifiée — elle proposerait un nouvel essai, donc du travail en double.
                await _persist_success_safely(db_entry_id, conversation_id, session, db_entry, conversation, raw_result, state)
        return None
    except Exception as e:
        _mark_startup_failure(db_entry_id, e)
        return None


# 4. ENDPOINTS API
@app.get("/")
def read_root():
    return {"status": "API CrewAI opérationnelle"}

def _load_qualification_context(conversation_id: int, user_id) -> str | None:
    """Rappel des tours précédents pour /api/qualify, ou None si la conversation est introuvable
    ou n'appartient pas à cet utilisateur. Synchrone (accès DB bloquant) : à appeler via
    asyncio.to_thread, avec sa propre Session, pour ne pas bloquer la boucle asyncio."""
    with Session(engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if not conversation or conversation.user_id != user_id:
            return None
        # Seuls les MAX_PRIOR_TURNS_IN_CONTEXT derniers tours servent au contexte : inutile de
        # relire tous les résultats (souvent volumineux) d'une longue conversation à chaque
        # qualification — le nombre total suffit pour signaler les tours omis.
        total = session.exec(
            select(func.count()).select_from(ExecutionHistory)
            .where(ExecutionHistory.conversation_id == conversation.id)
        ).one()
        recent = session.exec(
            select(ExecutionHistory)
            .where(ExecutionHistory.conversation_id == conversation.id)
            .order_by(col(ExecutionHistory.created_at).desc())
            .limit(MAX_PRIOR_TURNS_IN_CONTEXT)
        ).all()
        return build_conversation_context(list(reversed(recent)), total_count=total)

@app.post("/api/qualify", response_model=QualificationResult)
async def qualify_request(data: UserRequestInput, user: dict = Depends(get_current_user)):
    """Étape 1 : Qualification du besoin"""
    # Tours précédents de la conversation : sans eux, un message de suivi ("corrige ça",
    # "ajoute aussi Y") est qualifié hors contexte, souvent en DESIGN_AND_DEV par défaut.
    conversation_context = ""
    if data.conversation_id is not None:
        loaded = await asyncio.to_thread(_load_qualification_context, data.conversation_id, user.get("id"))
        if loaded is None:
            raise HTTPException(status_code=404, detail="Conversation introuvable.")
        conversation_context = loaded
    try:
        report = await crew_instance.analyze_user_request(data.user_request, conversation_context, data.has_repo_target)
        crew_instance.save_analysis_report(report, data.user_request)
        return report
    except Exception as e:
        print("--- ERREUR CREWAI DETECTEE ---", flush=True)
        print(traceback.format_exc(), flush=True)
        # Pas de str(e) dans la réponse : le détail réel reste dans les logs ci-dessus.
        info = classify_exception(e)
        raise AppError(
            http_status_for(info.code), info.code,
            info.message or "La qualification de la demande a échoué.", info.retryable,
        )

@app.post("/api/execute")
async def execute_workflow(
    data: WorkflowExecutionInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Étape 2 : Lancement dynamique des agents & enregistrement BDD"""
    _log_memory("début /api/execute")
    final_prompt = (
        f"Demande initiale : {data.user_request}\n"
        f"Type d'exécution : {data.target_workflow}\n"
        f"Précisions apportées : {data.clarifications if data.clarifications else 'Aucune.'}"
    )

    has_repo_target = bool(data.repo_owner and data.repo_name)
    # ANALYSE_ONLY ne comprend que design_task/architecture_task, en lecture seule (voir
    # run_dynamic_crew dans crewquestion.py) : jamais de branche/commit/PR à vérifier pour ce
    # workflow. Calculé une seule fois et réutilisé aux deux points qui en ont besoin plus bas
    # (capture du SHA de référence avant le crew, vérification après coup) plutôt que dupliqué,
    # pour qu'ils ne puissent pas diverger silencieusement si l'un est modifié sans l'autre.
    should_verify_github_delivery = has_repo_target and data.target_workflow != "ANALYSE_ONLY"

    conversation = None
    if data.conversation_id is not None:
        conversation = session.get(Conversation, data.conversation_id)
        if not conversation or conversation.user_id != user.get("id"):
            raise HTTPException(status_code=404, detail="Conversation introuvable.")

    # Contrôle préalable GitHub : une faute (repository, droits, branche de base) se découvre ICI, avant
    # toute ligne en base et tout appel LLM, au lieu de la fin d'une exécution de plusieurs minutes.
    if should_verify_github_delivery:
        try:
            # Borné dans le temps : PyGithub peut sinon attendre longtemps (délai par défaut et
            # nouvelles tentatives) pendant que la requête de lancement reste suspendue.
            await asyncio.wait_for(
                asyncio.to_thread(check_github_access, data.repo_owner, data.repo_name, data.base_branch or "main"),
                timeout=_GITHUB_PRECHECK_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            raise AppError(
                503, ErrorCode.GITHUB_UNAVAILABLE,
                "GitHub met trop de temps à répondre : réessayez dans quelques instants.", True,
            )
        except GitHubAccessProblem as problem:
            status_code, code, retryable = _GITHUB_ACCESS_ERRORS.get(
                problem.kind, (500, ErrorCode.INTERNAL_ERROR, False)
            )
            raise AppError(status_code, code, problem.message, retryable)

    if conversation is None:
        conversation = Conversation(
            user_id=user.get("id"),
            title=data.user_request.strip()[:80] or "Nouvelle conversation",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

    # Tours précédents de cette conversation : donnent aux agents un rappel de ce qui a
    # déjà été demandé/livré, et permettent de continuer sur la même branche de travail
    # plutôt que d'en ouvrir une nouvelle déconnectée à chaque message (voir plus bas).
    prior_entries = session.exec(
        select(ExecutionHistory)
        .where(ExecutionHistory.conversation_id == conversation.id)
        .order_by(col(ExecutionHistory.created_at).asc())
    ).all()
    conversation_context = build_conversation_context(prior_entries)

    # Empêche deux exécutions concurrentes sur la même conversation. Nécessaire depuis la
    # réutilisation du work_branch entre tours (voir plus bas) : sans ce garde-fou, deux
    # requêtes lancées en parallèle sur la même conversation (ex: double clic, deux onglets)
    # écriraient toutes les deux sur la même branche via github_write_file/github_edit_file,
    # qui se basent sur le SHA du fichier pour détecter les conflits (optimistic concurrency) —
    # l'une des deux échouerait alors avec un SHA obsolète au lieu d'une erreur claire.
    # Un tour resté bloqué à "running" (crash serveur en cours d'exécution, qui saute le bloc except)
    # est balayé ci-dessous (orphans.sweep_stale_executions) s'il n'a plus donné signe de vie.
    if any(entry.status == "running" for entry in prior_entries):
        # Une exécution morte (crash, redéploiement) ne doit pas bloquer la conversation pour toujours.
        if sweep_stale_executions(session, active_ids=_active_execution_ids, conversation_id=conversation.id):
            session.expire_all()
            prior_entries = session.exec(
                select(ExecutionHistory)
                .where(ExecutionHistory.conversation_id == conversation.id)
                .order_by(col(ExecutionHistory.created_at).asc())
            ).all()
            conversation_context = build_conversation_context(prior_entries)
    if any(entry.status == "running" for entry in prior_entries):
        raise HTTPException(
            status_code=409,
            detail="Une exécution est déjà en cours pour cette conversation. Attends qu'elle se termine avant d'envoyer un nouveau message.",
        )

    # Reprise d'une exécution en échec (étapes déjà réussies réutilisées) ; {} si rien n'est reprenable.
    resume_outputs = _resumable_outputs(session, data, user.get("id"), conversation.id)

    # Normalisé une seule fois : utilisé à la fois pour comparer aux tours précédents et
    # pour ce qui est stocké sur ce tour, afin que les deux restent cohérents (sinon un
    # base_branch vide explicitement envoyé empêcherait à tort la réutilisation de
    # branche au tour suivant, qui compare toujours à la valeur normalisée).
    normalized_base_branch = (data.base_branch or "main") if has_repo_target else None

    work_branch = ""
    if has_repo_target:
        # Réutilise le NOM de branche d'un tour précédent quel que soit son statut (y compris
        # "failed") : c'est ce qui permet à un "Recommence l'implémentation" après un échec de
        # continuer sur la MÊME branche/PR plutôt que d'en ouvrir une nouvelle à chaque tentative.
        # Savoir si cette branche existe RÉELLEMENT sur GitHub à cet instant (utile à
        # diagnostic_task, voir tasksquestion.yaml) n'est PAS déduit ici du statut DB de ce tour
        # précédent (un tour "failed" peut avoir réellement poussé des commits, ex: si seule
        # l'ouverture de la PR a échoué — voir verify_github_delivery) : _run_crew_and_persist
        # interroge directement l'API GitHub (repo_branch_sha_before, live) pour ce signal, plus
        # fiable qu'une heuristique basée sur ce champ.
        for entry in reversed(prior_entries):
            if (
                entry.work_branch
                and entry.repo_owner == data.repo_owner
                and entry.repo_name == data.repo_name
                and entry.base_branch == normalized_base_branch
            ):
                work_branch = entry.work_branch
                break
        if not work_branch:
            work_branch = f"crewai/{data.target_workflow.lower()}-{uuid.uuid4().hex[:8]}"

    # Enregistrement immédiat (statut "running") pour garder une trace même en cas d'échec
    db_entry = ExecutionHistory(
        user_request=data.user_request,
        workflow=data.target_workflow,
        clarifications=data.clarifications,
        status="running",
        user_id=user.get("id"),
        conversation_id=conversation.id,
        repo_owner=data.repo_owner if has_repo_target else None,
        repo_name=data.repo_name if has_repo_target else None,
        base_branch=normalized_base_branch,
        work_branch=work_branch or None,
        attempts=1,
        reused_steps=len(resume_outputs),
    )
    session.add(db_entry)
    session.commit()
    session.refresh(db_entry)

    # Compromis de mémoïsation CrewAI (cache module-level sans éviction native, purgé activement
    # par run_dynamic_crew) : voir la docstring de _execute_crew_and_persist, qui construit
    # désormais l'instance AppDevelopmentCrew dédiée à cette exécution.
    #
    # asyncio.create_task (pas `await`) : lance l'exécution réelle du crew en tâche de fond et
    # répond IMMÉDIATEMENT, plutôt que de faire attendre le client (potentiellement plusieurs
    # minutes) sur cette même connexion HTTP. Verrouiller l'écran du téléphone ou fermer l'onglet
    # pendant l'attente coupait cette connexion (comportement standard d'un navigateur mobile en
    # arrière-plan) — l'exécution continuait déjà côté serveur dans les deux cas (rien n'annule
    # cette tâche juste parce que le client se déconnecte), mais sans réponse à attendre, plus
    # aucune fenêtre où une telle coupure prive l'utilisateur de voir le résultat final : le
    # sondage de progression déjà existant côté frontend (useConversation.ts) prend le relais dès
    # que l'exécution quitte "running" en base, indépendamment de cette connexion d'origine.
    # _background_tasks (voir sa définition) retient une référence forte le temps de l'exécution,
    # pour ne pas risquer que le garbage collector ne l'interrompe en cours de route.
    task = asyncio.create_task(_execute_crew_and_persist(
        db_entry.id, conversation.id, data, has_repo_target, should_verify_github_delivery,
        work_branch, normalized_base_branch, final_prompt, conversation_context, resume_outputs,
    ))
    _track_execution_task(task, db_entry.id)

    return {
        "status": "running", "id": db_entry.id, "conversation_id": conversation.id,
        # Étapes réutilisées d'une exécution précédente (vide hors reprise) : l'interface l'indique.
        "resumed_steps": list(resume_outputs),
    }

@app.post("/api/conversations", response_model=Conversation)
async def create_conversation(
    data: ConversationCreateInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Démarre une nouvelle conversation vide."""
    conversation = Conversation(
        user_id=user.get("id"),
        title=(data.title or "Nouvelle conversation").strip()[:200] or "Nouvelle conversation",
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation

@app.get("/api/conversations", response_model=List[Conversation])
async def list_conversations(
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Conversations de l'utilisateur courant, les plus récemment actives en premier."""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    statement = (
        select(Conversation)
        .where(Conversation.user_id == user.get("id"))
        .order_by(col(Conversation.updated_at).desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(statement).all()

@app.get("/api/conversations/{conversation_id}/messages", response_model=List[ExecutionHistory])
async def get_conversation_messages(
    conversation_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Messages (échanges qualify+execute) d'une conversation, dans l'ordre chronologique."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    statement = (
        select(ExecutionHistory)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .order_by(col(ExecutionHistory.created_at).asc())
    )
    return session.exec(statement).all()


def _parse_completed_agents(result_text: str) -> dict[str, str]:
    """Découpe le résultat combiné du crew en sections par agent.

    Format attendu:
    - Sections séparées par la frontière littérale: '\n\n---\n\n## AgentName'
    - Chaque section: '## AgentName\n\n...contenu...'
    - Le résumé final (optionnel) est marqué par '<!--crew-summary-->' et n'est PAS retourné
    - Cette fonction ignore le résumé et les sections après le marqueur de résumé

    Exemple:
        Input: "## Agent1\n\n...content1...\n\n---\n\n## Agent2\n\n...content2...\n\n<!--crew-summary-->\n\n## Résumé\n\n..."
        Output: {"Agent1": "## Agent1\n\n...content1...", "Agent2": "## Agent2\n\n...content2..."}

    Args:
        result_text: String markdown du résultat du crew complet ou partiel

    Returns:
        dict[str, str]: {nom_agent: contenu_formaté_avec_heading}
    """
    if not result_text:
        return {}

    # Chercher le sentinel résumé : '<!--crew-summary-->' (marqueur du résumé final)
    agents = {}
    summary_marker = "<!--crew-summary-->"

    # Isoler la partie agents (avant le résumé)
    if summary_marker in result_text:
        agents_part = result_text[:result_text.index(summary_marker)]
    else:
        agents_part = result_text

    # Découper par frontière AGENT_SECTION_REGEX_PATTERN
    # Cette frontière contient '\n\n---\n\n## ' donc le split supprime ce texte entre sections
    sections = re.split(AGENT_SECTION_REGEX_PATTERN, agents_part)

    for section in sections:
        if not section.strip():
            continue

        lines = section.split('\n', 1)
        if len(lines) >= 2:
            agent_name = lines[0].strip()
            content = lines[1]
        else:
            agent_name = lines[0].strip()
            content = ""

        # Retirer les marqueurs ## du heading si présents (première section les conserve du split)
        if agent_name.startswith('##'):
            agent_name = agent_name[2:].strip()

        # Ne pas traiter comme agent si le heading ne ressemble pas à un rôle
        # (ex: un heading du contenu d'un agent, pas une vraie frontière)
        if agent_name and len(agent_name) > 2:
            agents[agent_name] = f"## {agent_name}\n\n{content}" if content else f"## {agent_name}"

    return agents

@app.get("/api/conversations/{conversation_id}/progress")
async def get_conversation_progress(
    conversation_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Sondage léger de la progression pendant qu'une exécution est en cours.

    Retourne aussi les sections d'agents complétés jusqu'à présent, découpe du champ result,
    pour affichage progressif des analyses d'agents au fur et à mesure de leur completion.
    """
    conversation = session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    # Libère une exécution morte : le frontend voit alors « plus rien en cours » et resynchronise le tour
    # (désormais « failed »/INTERRUPTED) au lieu de sonder indéfiniment.
    sweep_stale_executions(session, active_ids=_active_execution_ids, conversation_id=conversation_id)

    statement = (
        select(ExecutionHistory.id, ExecutionHistory.status, ExecutionHistory.current_step, ExecutionHistory.result)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .where(ExecutionHistory.status == "running")
    )
    row = session.exec(statement).first()
    if row is None:
        return {"id": None, "status": None, "current_step": None, "completed_agents": {}}

    completed_agents = {}
    if row[3]:  # if result is not None
        completed_agents = _parse_completed_agents(row[3])

    return {
        "id": row[0],
        "status": row[1],
        "current_step": row[2],
        "completed_agents": completed_agents,
    }

@app.get("/api/repo-targets")
async def list_repo_targets(
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Combinaisons owner/repo/branche déjà utilisées par l'utilisateur, les plus récentes en premier."""
    statement = (
        select(ExecutionHistory.repo_owner, ExecutionHistory.repo_name, ExecutionHistory.base_branch)
        .where(ExecutionHistory.user_id == user.get("id"))
        .where(col(ExecutionHistory.repo_owner).is_not(None))
        .where(col(ExecutionHistory.repo_name).is_not(None))
        .order_by(col(ExecutionHistory.created_at).desc())
    )
    rows = session.exec(statement).all()

    seen = set()
    targets = []
    for repo_owner, repo_name, base_branch in rows:
        key = (repo_owner, repo_name, base_branch)
        if key in seen:
            continue
        seen.add(key)
        targets.append({"repo_owner": repo_owner, "repo_name": repo_name, "base_branch": base_branch})
        if len(targets) >= 20:
            break
    return targets

_IN_CLAUSE_CHUNK = 500
REQUEST_FETCH_CHARS = 200
# Garde-fou mémoire, pas une limite de produit : au-delà, le tableau de bord signale `truncated` et ne fait
# plus de comparaison. Assez haut pour que 365 jours d'un usage normal soient calculés EXACTEMENT.
_METRICS_EXECUTION_LIMIT = 20_000


def _token_prices() -> Optional[tuple[float, float]]:
    """Tarif (entrée, sortie) par million de tokens, lu à l'appel depuis TOKEN_PRICE_INPUT_PER_MILLION et
    TOKEN_PRICE_OUTPUT_PER_MILLION ; None (coût masqué) si absent, invalide ou nul. Un tarif unique : le modèle
    n'est pas stocké par mesure."""
    try:
        prices = (
            float(os.getenv("TOKEN_PRICE_INPUT_PER_MILLION", "0")),
            float(os.getenv("TOKEN_PRICE_OUTPUT_PER_MILLION", "0")),
        )
    except ValueError:
        return None
    return prices if prices[0] > 0 or prices[1] > 0 else None

@app.get("/api/metrics/summary")
async def metrics_summary(
    days: int = 30,
    workflow: Optional[str] = None,
    tz_offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Performance par agent sur les `days` derniers jours (exécutions TERMINÉES de l'utilisateur),
    éventuellement restreinte à un workflow : durées p50/p95, appels LLM, tokens, outils, tendance.
    `tz_offset` : minutes à l'est d'UTC (celui du navigateur), pour regrouper par jour LOCAL."""
    days = max(1, min(days, 365))
    tz_offset = max(-840, min(tz_offset, 840))
    # Bornes AVEC fuseau : SQLModel refuse de lier un datetime naïf à ces colonnes. On lit les DEUX périodes
    # (courante et précédente, de même durée) en une seule requête pour la comparaison.
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    uid = user.get("id")
    statement = (
        select(  # type: ignore[misc]  # trop de colonnes pour l'inférence de mypy
            ExecutionHistory.id, ExecutionHistory.status, ExecutionHistory.workflow,
            ExecutionHistory.created_at, ExecutionHistory.updated_at,
            ExecutionHistory.rate_limit_hits, ExecutionHistory.total_wait_time_seconds,
            ExecutionHistory.error_code, ExecutionHistory.attempts, ExecutionHistory.reused_steps,
            ExecutionHistory.qa_verdict,
        )
        .where(ExecutionHistory.user_id == uid)
        .where(ExecutionHistory.created_at >= now - timedelta(days=2 * days))
        .where(col(ExecutionHistory.status).in_(("success", "failed")))
        .order_by(col(ExecutionHistory.created_at).desc())
        .limit(_METRICS_EXECUTION_LIMIT)
    )
    if workflow:
        statement = statement.where(ExecutionHistory.workflow == workflow)
    rows = [
        {
            "id": r[0], "status": r[1], "workflow": r[2], "created_at": r[3], "updated_at": r[4],
            "rate_limit_hits": r[5], "total_wait_time_seconds": r[6], "error_code": r[7],
            "attempts": r[8], "reused_steps": r[9], "qa_verdict": r[10],
        }
        for r in session.exec(statement).all()
    ]
    executions, previous_executions = split_by_period(rows, since)
    # Limite atteinte : les plus anciennes exécutions ont été coupées. La période courante n'est incomplète
    # que si la coupure l'atteint ; la période précédente, elle, l'est dès que la limite est atteinte.
    cut = len(rows) >= _METRICS_EXECUTION_LIMIT
    truncated = cut and not previous_executions
    # Comparaison abandonnée à cause de la limite alors que la période courante est complète : signalé à part.
    comparison_limited = cut and bool(previous_executions)
    # Par paquets : une liste IN de milliers d'identifiants dépasse la limite de paramètres des anciennes
    # versions de SQLite (999) ; sans effet notable sous Postgres.
    runs: list[dict[str, Any]] = []
    # Les mesures de la période précédente ne servent que si la comparaison est affichée : à la limite elle
    # est abandonnée, inutile alors de charger jusqu'à 20 000 exécutions de mesures pour les jeter.
    compare = bool(previous_executions) and not cut
    ids = [e["id"] for e in (rows if compare else executions)]
    for start in range(0, len(ids), _IN_CLAUSE_CHUNK):
        runs.extend(
            row.model_dump()
            for row in session.exec(
                select(AgentRun)
                .where(AgentRun.user_id == uid)
                .where(col(AgentRun.execution_id).in_(ids[start:start + _IN_CLAUSE_CHUNK]))
            ).all()
        )
    current_ids = {e["id"] for e in executions}
    prices = _token_prices()
    result = summarize(
        [r for r in runs if r["execution_id"] in current_ids], executions, days, workflow, tz_offset, prices,
    )
    result["currency"] = os.getenv("COST_CURRENCY", "$") if prices else None
    result["truncated"] = truncated
    result["comparison_limited"] = comparison_limited
    # Comparaison honnête seulement : sans exécution précédente, ou si la période précédente est incomplète
    # (limite atteinte), il n'y a rien à comparer — jamais un écart calculé sur un échantillon tronqué.
    if compare:
        previous_ids = {e["id"] for e in previous_executions}
        result["previous"] = summarize(
            [r for r in runs if r["execution_id"] in previous_ids], previous_executions, days, workflow, tz_offset,
            prices,
        )["executions"]
    else:
        result["previous"] = None
    return result

@app.get("/api/metrics/executions")
async def metrics_executions(
    days: int = 30,
    workflow: Optional[str] = None,
    status: Optional[Literal["success", "failed"]] = None,
    sort: Literal["created_at", "duration", "llm_calls", "tokens"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Exécutions terminées de l'utilisateur sur la période, une page à la fois : filtre par workflow et par
    statut, tri par date, durée, appels LLM ou tokens (valeurs absentes toujours en dernier)."""
    days = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    uid = user.get("id")
    statement = (
        select(  # type: ignore[misc]  # trop de colonnes pour l'inférence de mypy
            ExecutionHistory.id, ExecutionHistory.conversation_id,
            # Début de la demande seulement : une demande peut faire des milliers de caractères.
            func.substr(ExecutionHistory.user_request, 1, REQUEST_FETCH_CHARS),
            ExecutionHistory.workflow, ExecutionHistory.status, ExecutionHistory.created_at,
            ExecutionHistory.updated_at, ExecutionHistory.error_code, ExecutionHistory.qa_verdict,
            ExecutionHistory.attempts, ExecutionHistory.reused_steps, ExecutionHistory.repo_owner,
            ExecutionHistory.repo_name,
        )
        .where(ExecutionHistory.user_id == uid)
        .where(ExecutionHistory.created_at >= since)
        .where(col(ExecutionHistory.status).in_(("success", "failed")))
        .order_by(col(ExecutionHistory.created_at).desc())
        .limit(_METRICS_EXECUTION_LIMIT)
    )
    if workflow:
        statement = statement.where(ExecutionHistory.workflow == workflow)
    if status:
        statement = statement.where(ExecutionHistory.status == status)
    rows = [
        {
            "id": r[0], "conversation_id": r[1], "user_request": r[2], "workflow": r[3], "status": r[4],
            "created_at": r[5], "updated_at": r[6], "error_code": r[7], "qa_verdict": r[8], "attempts": r[9],
            "reused_steps": r[10], "repo_owner": r[11], "repo_name": r[12],
        }
        for r in session.exec(statement).all()
    ]
    # Une ligne par exécution (GROUP BY) : appels LLM et tokens (usage connu seulement ; None sinon).
    sums: dict[int, dict[str, Any]] = {}
    ids = [row["id"] for row in rows]
    known_usage = case((col(AgentRun.usage_calls) > 0, 1), else_=0)
    for start in range(0, len(ids), _IN_CLAUSE_CHUNK):
        grouped = session.exec(
            select(
                AgentRun.execution_id, func.sum(AgentRun.llm_calls),
                func.sum(case((col(AgentRun.usage_calls) > 0, AgentRun.total_tokens), else_=0)),
                func.sum(known_usage),
            )
            .where(AgentRun.user_id == uid)
            .where(col(AgentRun.execution_id).in_(ids[start:start + _IN_CLAUSE_CHUNK]))
            .group_by(AgentRun.execution_id)
        ).all()
        for execution_id, calls, tokens, known in grouped:
            sums[execution_id] = {"llm_calls": int(calls or 0), "tokens": int(tokens or 0) if known else None}
    return list_executions(
        rows, sums, status=status, sort=sort, descending=order != "asc", limit=limit, offset=offset,
    )

@app.get("/api/executions/{execution_id}/agent-runs")
async def execution_agent_runs(
    execution_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Détail par agent d'UNE exécution (durée, appels LLM, tokens, outils), dans l'ordre du pipeline."""
    entry = session.get(ExecutionHistory, execution_id)
    if not entry or entry.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Exécution introuvable.")
    rows = [
        row.model_dump()
        for row in session.exec(select(AgentRun).where(AgentRun.execution_id == execution_id)).all()
    ]
    return [agent_run_view(row) for row in sort_pipeline(rows)]

@app.get("/api/history", response_model=List[ExecutionHistory])
async def get_history(
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Historique des exécutions de l'utilisateur courant, les plus récentes en premier."""
    limit = min(max(limit, 1), 100)
    offset = max(offset, 0)
    statement = (
        select(ExecutionHistory)
        .where(ExecutionHistory.user_id == user.get("id"))
        .order_by(col(ExecutionHistory.created_at).desc())
        .offset(offset)
        .limit(limit)
    )
    return session.exec(statement).all()

def _delete_executions(session: Session, entries: List[ExecutionHistory]) -> None:
    """Supprime des exécutions et leurs mesures par agent (sans commit).
    Les mesures n'ont pas de clé étrangère : sans cette suppression explicite, elles survivraient
    aux exécutions supprimées (données orphelines, invisibles)."""
    if not entries:
        return
    ids = [entry.id for entry in entries]
    for agent_run in session.exec(select(AgentRun).where(col(AgentRun.execution_id).in_(ids))).all():
        session.delete(agent_run)
    for checkpoint in session.exec(select(ExecutionCheckpoint).where(col(ExecutionCheckpoint.execution_id).in_(ids))).all():
        session.delete(checkpoint)
    for entry in entries:
        session.delete(entry)

@app.delete("/api/history/{execution_id}")
async def delete_history_entry(
    execution_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Supprime une exécution de l'historique de l'utilisateur courant."""
    print(f"DELETE /api/history/{execution_id} appelé par {user.get('id')}", flush=True)
    # Une exécution orpheline (plus de signe de vie) devient « failed » donc supprimable.
    sweep_stale_executions(session, active_ids=_active_execution_ids, user_id=user.get("id"), ids=[execution_id])
    session.expire_all()
    entry = session.get(ExecutionHistory, execution_id)
    if not entry:
        print(f"  → Entrée {execution_id} introuvable en base", flush=True)
        raise HTTPException(status_code=404, detail="Exécution introuvable.")
    if entry.user_id != user.get("id"):
        print(f"  → Accès refusé : entry.user_id={entry.user_id}, user.id={user.get('id')}", flush=True)
        raise HTTPException(status_code=404, detail="Exécution introuvable.")

    # Bloqué sur status="running" : une exécution orpheline (plus de signe de vie, voir orphans.py)
    # vient d'être balayée ci-dessus et n'est donc plus "running" ; ce qui reste "running" est une
    # exécution vivante (ou récemment vivante). Autoriser la suppression d'une exécution vivante romprait le contrôle de concurrence d'/api/execute (qui ne
    # regarde plus que les lignes encore en base pour décider si la conversation est libre) sans
    # rien faire pour arrêter le crew qui tourne encore réellement en tâche de fond : une nouvelle
    # exécution démarrerait alors EN PARALLÈLE de celle "supprimée" sur la même conversation
    # (potentiellement la même work_branch), et le résultat de cette dernière, une fois terminé,
    # ne pourrait plus être persisté (sa ligne n'existe plus) — silencieusement perdu, PR GitHub
    # potentiellement déjà ouverte comprise.
    if entry.status == "running":
        print("  → Suppression refusée : status=running", flush=True)
        raise HTTPException(status_code=409, detail="Impossible de supprimer une exécution encore en cours.")

    print(f"  → Suppression en cours : user_request={entry.user_request[:50]}", flush=True)
    _delete_executions(session, [entry])
    session.commit()
    print("  → Suppression confirmée en base", flush=True)
    return {"status": "deleted", "id": execution_id}

@app.post("/api/history/bulk-delete")
async def bulk_delete_history(
    payload: BulkDeleteInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Supprime plusieurs exécutions en un seul commit. Une exécution en cours ou introuvable
    (inconnue OU appartenant à un autre utilisateur) est ignorée sans faire échouer le lot."""
    ids = list(dict.fromkeys(payload.ids))
    if len(ids) > BULK_DELETE_MAX:
        raise HTTPException(status_code=422, detail=f"{BULK_DELETE_MAX} exécutions au plus par suppression.")
    entries = {}
    # Hors plage d'un entier SQL : forcément inconnu (et fatal pour la requête IN sous Postgres/SQLite).
    valid_ids = [i for i in ids if 0 < i <= _SQL_INT_MAX]
    if valid_ids:
        sweep_stale_executions(session, active_ids=_active_execution_ids, user_id=user.get("id"), ids=valid_ids)
        session.expire_all()
        rows = session.exec(
            select(ExecutionHistory).where(
                col(ExecutionHistory.id).in_(valid_ids), ExecutionHistory.user_id == user.get("id")
            )
        ).all()
        entries = {row.id: row for row in rows}
    deleted: List[int] = []
    skipped = []
    doomed: List[ExecutionHistory] = []
    for execution_id in ids:
        entry = entries.get(execution_id)
        if entry is None:
            skipped.append({"id": execution_id, "reason": "not_found"})
        elif entry.status == "running":
            # Même raison que pour la suppression unitaire (voir delete_history_entry).
            skipped.append({"id": execution_id, "reason": "running"})
        else:
            doomed.append(entry)
            deleted.append(execution_id)
    _delete_executions(session, doomed)
    session.commit()
    return {"deleted": deleted, "skipped": skipped}
