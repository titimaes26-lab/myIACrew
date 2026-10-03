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
from typing import List, NamedTuple, Optional
from sqlalchemy import update as sql_update
from sqlmodel import Session, func, select

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
    agent_run_view, build_agent_run_rows, flush_events, sort_pipeline, summarize, step_for_role,
)
from auth import get_current_user, close_http_client
import validation
from orphans import HEARTBEAT_RETRY_SECONDS, HEARTBEAT_SECONDS, sweep_stale_executions
from errors import (
    AppError, ErrorCode, classify_exception, code_for_status, error_body, http_status_for, is_retryable_status,
)
from github_tools import (
    verify_github_delivery, get_branch_head_sha, describe_partial_delivery, GitHubVerificationUnavailable,
    check_github_access, GitHubAccessProblem,
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

# 2. ÉVÉNEMENT DE DÉMARRAGE (Création des tables BDD)
@app.on_event("startup")
def on_startup():
    create_db_and_tables()
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
    structured = {} if isinstance(exc.detail, str) else {"errors": exc.detail}
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
            raw = int(f.read().strip())
            # cgroup v1 représente "illimité" par une très grande valeur (pas un mot-clé explicite
            # comme "max" en v2) : un seuil large mais arbitraire écarte ce cas plutôt que
            # d'afficher une "limite" de plusieurs exaoctets, dénuée de sens pratique.
            if 0 < raw < (1 << 62):
                return _MemoryLimit(raw / (1024 * 1024), True)
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
    """Lance le crew et persiste son issue (succès ou échec) en base — voir execute_workflow, qui
    ne fait plus qu'enregistrer db_entry (statut "running") puis lancer _execute_crew_and_persist
    en tâche de fond (asyncio.create_task) avant de répondre immédiatement au client, au lieu
    d'attendre l'exécution complète (potentiellement plusieurs minutes) avant de répondre.

    Sans ce découplage, verrouiller l'écran du téléphone ou fermer l'onglet pendant l'attente
    suffisait à couper la connexion HTTP sous-jacente (comportement standard des navigateurs
    mobiles sur un onglet mis en arrière-plan) — non pas que l'exécution s'arrêtait vraiment côté
    serveur (rien, avant comme après ce changement, n'annule cette tâche juste parce que le client
    se déconnecte), mais l'utilisateur n'avait alors plus AUCUN moyen de voir le résultat final
    sans recharger manuellement la conversation depuis l'Historique. Désormais, le sondage de
    progression déjà existant côté frontend (useConversation.ts) prend le relais dès que
    l'exécution quitte "running", sans dépendre de cette connexion HTTP d'origine.

    Ouvre sa PROPRE Session (comme _persist_current_step un peu plus haut, pour la même raison) :
    la Session injectée dans execute_workflow via Depends(get_session) est fermée par FastAPI une
    fois SA réponse envoyée, bien avant que cette tâche de fond n'ait eu la moindre chance de
    s'exécuter.
    """
    # Initialisé avant tout (lu par le rapport d'échec ci-dessous) : None tant que le repère GitHub
    # d'avant exécution n'a pas été capturé.
    repo_branch_sha_before = None
    try:
        with Session(engine) as session:
            db_entry = session.get(ExecutionHistory, db_entry_id)
            conversation = session.get(Conversation, conversation_id)
            if db_entry is None or conversation is None:
                # Ne devrait jamais arriver (db_entry_id/conversation_id viennent d'un enregistrement
                # tout juste commité par execute_workflow) : print plutôt qu'une exception qui
                # remonterait sans jamais être retrouvée (rien n'attend le résultat de cette tâche de
                # fond) — voir la note sur le garbage collector des Task, _background_tasks plus haut.
                print(
                    f"AVERTISSEMENT : db_entry={db_entry_id} ou conversation={conversation_id} "
                    "introuvable au lancement de la tâche de fond, exécution abandonnée.",
                    flush=True,
                )
                return

            # Une instance FRAÎCHE par exécution, pas le crew_instance partagé utilisé par
            # /api/qualify : les méthodes décorées @task/@agent de AppDevelopmentCrew (design_task(),
            # architecture_task()...) sont mémoïsées par CrewAI sur (nom de méthode, id(self)) — voir
            # crewai/project/utils.py, `_make_hashable` traite `self` comme `("__instance__", id(self))`.
            # Avec le singleton crew_instance (même `self` pour toutes les requêtes, pour toujours),
            # deux exécutions qui partagent un rôle de tâche (ex: deux BUGFIX simultanés dans DEUX
            # conversations différentes — le contrôle de concurrence dans execute_workflow n'empêche
            # qu'une même conversation d'avoir deux exécutions en vol, pas deux conversations
            # différentes en parallèle) recevraient LE MÊME objet Task pour ce rôle, et donc
            # partageraient sa `.callback` et son `.output` en cours d'exécution : bien plus grave
            # qu'un simple souci d'affichage de progression, cela mélangerait de vrais résultats
            # d'agents entre deux exécutions concurrentes sans rapport. Une instance locale à CETTE
            # tâche de fond (vivante le temps de l'await ci-dessous, donc jamais partagée avec une
            # autre exécution en vol) donne un id(self) distinct et donc des objets Task/Agent
            # distincts pour toute la durée de cette exécution.
            #
            # Compromis assumé : le cache de mémoïsation de CrewAI (crewai.project.utils.cache, un
            # dict module-level SANS éviction native) grossirait alors d'une poignée d'entrées à CHAQUE
            # exécution au lieu de rester borné à la taille du singleton précédent — une lente fuite
            # mémoire, bornée par le nombre total d'exécutions depuis le démarrage du process. Depuis,
            # purgé activement par run_dynamic_crew (crewquestion.py, voir _evict_memoized_cache_entries
            # dans son propre finally) plutôt que simplement accepté : pas de moyen PUBLIC d'éviction
            # côté CrewAI à ce jour, d'où un nettoyage best-effort de ce cache interne, avec un
            # redémarrage périodique du service comme filet de sécurité résiduel si ce nettoyage
            # venait à échouer.
            #
            try:
                # await asyncio.to_thread(...) et non un appel direct : AppDevelopmentCrew() (la
                # métaclasse @CrewBase de crewai, voir crewai/project/crew_base.py) relit et reparse
                # agentsquestion.yaml/tasksquestion.yaml depuis le disque et résout la config de TOUS
                # les agents qui y sont définis à chaque construction — un appel direct bloquerait la
                # boucle asyncio (donc toutes les autres requêtes concurrentes, y compris le sondage de
                # progression d'autres conversations) le temps de ce travail, à CHAQUE exécution.
                #
                # À L'INTÉRIEUR de ce try (pas juste avant) : une erreur ici (ex: agentsquestion.yaml
                # temporairement illisible) doit être rattrapée par le except plus bas comme tout autre
                # échec d'exécution (db_entry marqué "failed", pas laissé bloqué pour toujours sur
                # "running" — voir le contrôle de concurrence dans execute_workflow, et /api/history
                # qui refuse désormais de supprimer une ligne "running").
                async def _repo_branch_sha_before() -> str | None:
                    # Capturé AVANT le lancement du crew (jamais après) : c'est le repère utilisé plus
                    # bas par verify_github_delivery pour distinguer "cette exécution a réellement
                    # poussé un nouveau commit" de "une branche/PR d'un tour précédent existe toujours"
                    # sur un work_branch réutilisé entre tours d'une même conversation. None (branche
                    # pas encore créée : cas normal au tout premier tour) reste distinct d'une erreur
                    # transitoire de l'API GitHub (GitHubVerificationUnavailable) : les deux sont
                    # volontairement traités pareil ici (repère indisponible, best-effort) plutôt que
                    # de laisser un simple souci réseau empêcher le lancement du crew lui-même.
                    if not should_verify_github_delivery:
                        return None
                    try:
                        return await asyncio.to_thread(get_branch_head_sha, data.repo_owner, data.repo_name, work_branch)
                    except GitHubVerificationUnavailable:
                        return None

                # asyncio.gather (pas deux `await` séquentiels) : AppDevelopmentCrew() (parsing des
                # YAML, thread séparé) et la capture du SHA de référence (aller-retour réseau vers
                # l'API GitHub) sont deux opérations indépendantes qui ne dépendent l'une de l'autre en
                # rien, autant les laisser se chevaucher plutôt que d'ajouter inutilement leurs
                # latences bout à bout.
                crew_for_this_execution, repo_branch_sha_before = await asyncio.gather(
                    asyncio.to_thread(AppDevelopmentCrew),
                    _repo_branch_sha_before(),
                )
                _log_memory(f"execution_id={db_entry.id}, crew instancié, avant kickoff")

                # Tâche de fond dédiée : voir _periodic_memory_logger pour le raisonnement (les points
                # [MEM] existants n'ont qu'une granularité par CHANGEMENT DE TÂCHE, insuffisante pour
                # repérer une dérive mémoire PENDANT une tâche unique). Toujours annulée dans le finally
                # ci-dessous, que le kickoff réussisse, lève une CrewStepError, ou toute autre exception —
                # sans quoi cette tâche continuerait à s'exécuter (et donc à logger) indéfiniment après la
                # fin de CETTE exécution, une fuite de tâche asyncio à chaque exécution.
                memory_ticker = asyncio.create_task(
                    _periodic_memory_logger(f"execution_id={db_entry.id}, sondage périodique")
                )
                try:
                    with track_execution_metrics() as run_metrics:
                        result = await crew_for_this_execution.run_dynamic_crew(
                            inputs={
                                'user_request': final_prompt,
                                'conversation_context': conversation_context,
                                'repo_owner': data.repo_owner or '',
                                'repo_name': data.repo_name or '',
                                # `or 'main'` : nécessaire ici (contrairement à db_entry.base_branch
                                # plus haut) car normalized_base_branch est None sans repository cible,
                                # et les tâches interpolent toujours {base_branch} même dans ce cas.
                                'base_branch': normalized_base_branch or 'main',
                                'work_branch': work_branch,
                                # Isole l'espace de travail LOCAL de chaque conversation (mode sans
                                # repository cible, où work_branch est vide) — voir _reset_execution_state.
                                'conversation_id': str(conversation_id),
                                'repo_instructions': (
                                    f"Repository GitHub cible : {data.repo_owner}/{data.repo_name}\n"
                                    f"Branche de base : {normalized_base_branch}\n"
                                    f"Branche de travail à créer et utiliser pour toute écriture : {work_branch}\n"
                                    + (
                                        # Signal FIABLE pour diagnostic_task (sur quelle branche lire AVANT
                                        # que development_task ne crée {work_branch}, voir tasksquestion.yaml,
                                        # diagnostic_task) : repo_branch_sha_before vient d'un appel API
                                        # GitHub LIVE (get_branch_head_sha, juste au-dessus), pas d'une
                                        # déduction depuis le statut DB d'un tour précédent — un tour marqué
                                        # "failed" alors que la branche ET ses commits étaient réels (ex:
                                        # seule l'ouverture de la PR a échoué, voir verify_github_delivery)
                                        # aurait fait dire à tort à une déduction DB que la branche n'existe
                                        # pas encore. None ici couvre aussi bien "branche confirmée absente"
                                        # que "vérification indisponible" (souci transitoire) : dans les deux
                                        # cas, lire {base_branch} en attendant reste le choix le moins risqué
                                        # (même compromis "best-effort" que verify_github_delivery accepte
                                        # déjà pour ce même repère, voir sa docstring).
                                        f"Cette branche de travail EXISTE DÉJÀ sur GitHub (réutilisée d'un "
                                        f"tour précédent de cette conversation) : pour toute lecture, lis-la "
                                        f"directement avec branch={work_branch}."
                                        if repo_branch_sha_before is not None
                                        else f"Cette branche de travail N'EXISTE PAS ENCORE sur GitHub (sera "
                                        f"créée par la tâche de commit qui suit) : pour toute lecture, lis "
                                        f"sur branch={normalized_base_branch} en attendant."
                                    )
                                    if has_repo_target
                                    else (
                                        "Aucun repository GitHub cible fourni : travaille uniquement dans "
                                        "l'espace de travail local de cette conversation (chemins de fichiers "
                                        "relatifs, lus avec read_a_files_content). Cet espace est VIDE au premier "
                                        "tour d'une conversation : ne présume jamais qu'un fichier non livré par "
                                        "un tour précédent de cette même conversation existe déjà. N'utilise "
                                        "aucun outil github_* SAUF github_commit_analyst_files et "
                                        "qa_verify_delivered_files, qui agissent alors sur cet espace de travail."
                                    )
                                ),
                            },
                            request_type=data.target_workflow,
                            on_step_change=lambda step_key: _persist_current_step(db_entry.id, step_key),
                            on_task_output_complete=lambda agent_name, output, duration: _persist_completed_agent(db_entry.id, agent_name, output, duration),
                            resume_outputs=resume_outputs,
                        )
                finally:
                    # Les handlers d'événements CrewAI tournent dans un pool de threads : sans cette
                    # attente, les derniers appels LLM pourraient manquer aux métriques lues plus bas.
                    await asyncio.to_thread(flush_events)
                    memory_ticker.cancel()
                    try:
                        await memory_ticker
                    except asyncio.CancelledError:
                        # Ne PAS avaler aveuglément : si CETTE tâche de fond (celle créée par
                        # asyncio.create_task dans execute_workflow, voir _execute_crew_and_persist)
                        # recevait un jour sa PROPRE annulation pendant qu'elle est suspendue ici sur
                        # `await memory_ticker`, la CancelledError résultante serait indiscernable de
                        # celle de memory_ticker — sans cette vérification (Task.cancelling(), Python
                        # 3.11+), une telle annulation serait silencieusement perdue, laissant
                        # l'exécution se poursuivre normalement (raw_result, commit "success"...) alors
                        # qu'elle aurait dû s'arrêter. Inatteignable aujourd'hui en pratique (rien
                        # n'appelle .cancel() sur cette tâche de fond — voir on_shutdown, qui se
                        # contente d'un asyncio.wait avec timeout, JAMAIS une annulation, précisément
                        # pour laisser une chance à une exécution en cours de se terminer) : gardé
                        # malgré tout en défense, au cas où un futur mécanisme d'annulation serait
                        # ajouté (ex: un endpoint d'arrêt explicite d'une exécution) sans repasser ici.
                        current = asyncio.current_task()
                        if current is not None and current.cancelling() > 0:
                            raise
                raw_result = str(result.raw) if hasattr(result, 'raw') else str(result)

                # Un rapport d'agent "réussi" ne prouve rien de ce qui s'est réellement passé sur GitHub
                # (voir qa_task dans tasksquestion.yaml : la QA elle-même n'a aucun outil pour confirmer
                # qu'une PR a été ouverte) : un échec silencieux d'un outil github_* (erreur renvoyée
                # comme simple texte à l'agent, jamais une exception qui ferait échouer ce try) ou un
                # agent qui n'appelle tout simplement jamais ces outils produirait quand même ce même
                # statut "success" sans qu'aucune modification n'ait été poussée sur GitHub. Vérifié
                # uniquement quand un développement a effectivement eu lieu (ANALYSE_ONLY ne comprend que
                # design_task/architecture_task, en lecture seule — voir run_dynamic_crew) : sans cela,
                # cette vérification échouerait toujours à tort sur ce workflow, qui n'a jamais eu
                # l'intention de créer de branche ou de PR.
                # None tant que should_verify_github_delivery est False (ANALYSE_ONLY, ou pas de
                # repository cible) : rien à ajouter au résumé dans ce cas, voir plus bas.
                delivered_pr = None
                if should_verify_github_delivery:
                    # await asyncio.to_thread(...) : verify_github_delivery fait des appels HTTP
                    # bloquants (PyGithub) — comme pour crew_for_this_execution plus haut, un appel
                    # direct bloquerait la boucle asyncio, donc toutes les autres requêtes concurrentes,
                    # le temps de l'aller-retour réseau vers l'API GitHub.
                    delivered_pr, delivery_issue = await asyncio.to_thread(
                        verify_github_delivery, data.repo_owner, data.repo_name, work_branch,
                        normalized_base_branch, repo_branch_sha_before,
                    )
                    if delivery_issue:
                        # likely_access_problem (champ structuré de DeliveryIssue, pas un texte à parser
                        # par préfixe) distingue les cas où recommander de vérifier GITHUB_TOKEN est un
                        # diagnostic juste (branche introuvable, API injoignable) de ceux où la branche ET
                        # ses nouveaux commits sont confirmés mais où il manque une PR à jour : l'écriture
                        # a alors bien fonctionné, le problème est que l'agent développeur n'a pas terminé
                        # sa procédure (ex: budget d'appels d'outils épuisé avant l'ouverture de la Pull
                        # Request) — un conseil GITHUB_TOKEN y serait un diagnostic faux.
                        if delivery_issue.likely_access_problem:
                            remediation = (
                                "Vérifie la configuration GITHUB_TOKEN du backend (présence, permissions "
                                "d'écriture sur ce repository) puis relance."
                            )
                        else:
                            # Deux causes possibles, indiscernables depuis ce seul constat (une lecture
                            # GitHub a réussi, mais rien de neuf n'a été confirmé en écriture) : soit
                            # GITHUB_TOKEN peut lire ce repository mais n'a pas les permissions d'écriture
                            # nécessaires, soit le Développeur n'a pas terminé sa procédure GitHub (budget
                            # d'appels d'outils épuisé, étape non atteinte). Volontairement pas plus
                            # précis sur l'étape en cause (ex: "avant d'ouvrir la Pull Request") : ce même
                            # DeliveryIssue est aussi renvoyé quand AUCUN commit n'a été poussé du tout,
                            # pas seulement quand il ne manque que la Pull Request finale.
                            remediation = (
                                "Cela peut venir d'un manque de permissions d'écriture du GITHUB_TOKEN "
                                "configuré sur ce repository, ou du Développeur qui n'a pas terminé sa "
                                "procédure GitHub : vérifie les deux, puis relance."
                            )
                        # raw_result (plan et fichiers annoncés par le Développeur, rapport de la QA) est
                        # inclus tel quel plutôt que perdu : bien qu'invérifié côté GitHub, il reste utile
                        # à l'utilisateur pour comprendre ce que l'agent a effectivement tenté avant que
                        # cette vérification n'échoue, notamment pour juger s'il faut relancer tel quel ou
                        # reformuler la demande.
                        raise RuntimeError(
                            "Un repository GitHub cible était configuré mais la vérification après coup "
                            f"a échoué : {delivery_issue.message} {remediation}\n\n"
                            "--- Rapport de l'agent (non vérifié sur GitHub) ---\n"
                            f"{raw_result[:3000]}"
                        )

                # Ajouté à la SUITE de raw_result (déjà terminé par la section "## Résumé", voir
                # crewquestion.py/run_dynamic_crew et SUMMARY_SENTINEL) sans nouveau séparateur
                # "\n\n---\n\n## " : parseCrewResult.ts (frontend) ne découpe que sur cette frontière
                # précise, donc cette ligne reste rattachée à cette DERNIÈRE section — celle ouverte
                # par défaut dans l'interface — plutôt que de finir dans une section à part qu'il
                # faudrait déplier. URL/statut de fusion réellement observés sur GitHub par
                # verify_github_delivery ci-dessus, pas une affirmation non vérifiée du Développeur
                # (voir tasksquestion.yaml, qa_task : la QA elle-même n'a aucun outil pour ça).
                if delivered_pr is not None:
                    pr_line = "fusionnée" if delivered_pr.merged else "ouverte"
                    raw_result = f"{raw_result}\n\n**Pull Request {pr_line} :** {delivered_pr.html_url}"

                _safe_refresh(session, db_entry, "succès")

                db_entry.result = raw_result
                db_entry.status = "success"
                # Plus rien à afficher une fois l'exécution terminée avec succès (voir current_step sur
                # ExecutionHistory) : remis à None plutôt que laissé sur la dernière étape annoncée. Le
                # cas de l'échec (except plus bas) n'a pas besoin du même traitement ici : run_dynamic_crew
                # (crewquestion.py) l'a déjà fait lui-même, dès l'échec, via ce même on_step_change(None) —
                # avant même que cette tâche ne sache si retry_on_rate_limit_async va la rejouer ou non.
                db_entry.current_step = None
                db_entry.api_calls_count = run_metrics.api_calls_count
                db_entry.rate_limit_hits = run_metrics.rate_limit_hits
                db_entry.total_wait_time_seconds = run_metrics.total_wait_time
                _persist_agent_runs(session, db_entry, run_metrics)
                db_entry.updated_at = datetime.now(timezone.utc)
                conversation.updated_at = datetime.now(timezone.utc)
                session.add(db_entry)
                session.add(conversation)
                session.commit()
                print(f"execution_id={db_entry.id} : terminée avec succès (tâche de fond).", flush=True)
                # Après le commit du succès, dans sa propre Session et au mieux : purger une ressource
                # optionnelle ne doit jamais faire échouer (ni être validé par) le chemin d'une exécution réussie.
                await asyncio.to_thread(_delete_checkpoints_for, db_entry.id)
                _cleanup_persisted_agents(db_entry.id)
            except Exception as e:
                _log_memory(f"execution_id={db_entry.id}, exception attrapée")
                print("--- ERREUR CREWAI EXECUTION DETECTEE ---", flush=True)
                print(traceback.format_exc(), flush=True)

                info = classify_exception(e)

                # Seconde tentative AUTOMATIQUE (une seule) après un échec transitoire (quota, modèle
                # indisponible, délai) survenu AVANT l'écriture du code (design, architecture ou diagnostic) :
                # rien n'a encore été poussé sur GitHub, et les étapes déjà réussies sont reprises (points de
                # reprise). Un échec plus tard (développement, QA, vérification de livraison) n'est PAS relancé
                # tout seul : il aurait fallu réécrire sur GitHub et repayer ces étapes — l'utilisateur
                # relance explicitement. La ligne reste « running » ; l'attente se fait dans l'appelant
                # (_execute_crew_and_persist), hors sémaphore et hors Session. Les mesures de cette tentative
                # avortée ne sont pas enregistrées : la tentative suivante écrit les siennes.
                if auto_retry_allowed and info.retryable and _failed_before_development(e, data.target_workflow):
                    saved = await asyncio.to_thread(_load_checkpoints_for, db_entry.id)
                    prefix = resumable_prefix(workflow_step_keys(data.target_workflow), saved)
                    # Sans le point de reprise de CHAQUE étape déjà réussie (sauvegarde ratée), relancer
                    # repayerait ces étapes pour rien : échec définitif, l'utilisateur décide.
                    if len(prefix) >= e.step_index - 1:
                        print(
                            f"execution_id={db_entry.id} : échec transitoire ({info.code}), nouvelle tentative "
                            f"automatique dans {AUTO_RETRY_DELAY_S}s.", flush=True,
                        )
                        return {key: saved[key] for key in prefix}

                # Message lisible pour une cause reconnue ; sinon le texte d'origine (déjà ce qu'affichait
                # l'historique avant le typage des erreurs).
                # Le texte technique d'origine est conservé (tronqué) : sans lui, ni l'utilisateur ni la
                # base ne diraient POURQUOI (ex: ce que le garde-fou reproche, délai « retry after »).
                technical = str(e).strip()[:500]
                reason = f"{info.message} (détail : {technical})" if info.message and technical else (info.message or technical)
                if isinstance(e, CrewStepError):
                    detail = f"Échec à l'étape {e.step_index}/{e.total_steps} ({e.agent_role}) : {reason}"
                else:
                    detail = reason

                # Écritures GitHub partielles : l'échec peut survenir après qu'une branche, des commits ou
                # une PR ont DÉJÀ été créés. On le constate via l'API (jamais d'après le texte d'un agent)
                # et on l'ajoute au message, pour que l'utilisateur sache quoi reprendre ou nettoyer.
                if should_verify_github_delivery and data.repo_owner and data.repo_name and work_branch:
                    detail += "\n\n" + await _partial_delivery_block(
                        data.repo_owner, data.repo_name, work_branch, normalized_base_branch or "main",
                        repo_branch_sha_before,
                    )

                # Couvre le cas (rare) où l'échec survient APRÈS un kickoff_async par ailleurs réussi
                # (ex: _format_crew_result/_generate_summary, appelés dans crewquestion.py hors du
                # try/except qui entoure kickoff_async) : run_dynamic_crew n'a alors PAS pu faire son
                # propre nettoyage via on_step_change(None) (voir son except, qui ne couvre que
                # kickoff_async), laissant current_step sur la dernière étape connue malgré status
                # devenant "failed" ici.
                _safe_refresh(session, db_entry, "échec")
                db_entry.current_step = None

                db_entry.status = "failed"
                db_entry.result = detail
                db_entry.error_code = info.code
                db_entry.error_retryable = info.retryable
                if 'run_metrics' in locals():
                    db_entry.api_calls_count = run_metrics.api_calls_count
                    db_entry.rate_limit_hits = run_metrics.rate_limit_hits
                    db_entry.total_wait_time_seconds = run_metrics.total_wait_time
                    _persist_agent_runs(session, db_entry, run_metrics)
                db_entry.updated_at = datetime.now(timezone.utc)
                conversation.updated_at = datetime.now(timezone.utc)
                session.add(db_entry)
                session.add(conversation)
                session.commit()
                # Pas de HTTPException ici : cette fonction tourne en tâche de fond, sans requête HTTP
                # à qui répondre (execute_workflow a déjà répondu "running" avant même que cette tâche
                # ne démarre). L'échec est entièrement porté par db_entry.status="failed" ci-dessus,
                # que le sondage de progression côté frontend (useConversation.ts) ira lire.
                _cleanup_persisted_agents(db_entry.id)
    except Exception as e:
        # Ce except EXTERNE ne couvre que l'ouverture de la Session elle-même et les deux
        # session.get() qui suivent (ex : pool de connexions épuisé, coupure réseau vers la DB
        # au moment précis où cette tâche de fond démarre) : tout le reste (exécution du crew,
        # échecs applicatifs) est déjà couvert par le except interne ci-dessus, qui persiste
        # lui-même l'échec avec la Session déjà ouverte. Sans ce filet supplémentaire, une
        # telle erreur laisserait db_entry bloqué sur "running" pour toujours (la même limite
        # déjà documentée dans execute_workflow plus bas, ici atteinte sans même un crash
        # serveur) — best-effort seulement : si la DB est elle-même injoignable, cette tentative
        # de marquage "failed" échouera aussi, et rien ne peut alors être fait de mieux depuis
        # ce process.
        print(
            f"AVERTISSEMENT : échec du démarrage de la tâche de fond pour db_entry={db_entry_id} : {e}",
            flush=True,
        )
        try:
            with Session(engine) as session:
                db_entry = session.get(ExecutionHistory, db_entry_id)
                if db_entry is not None and db_entry.status == "running":
                    db_entry.status = "failed"
                    db_entry.result = f"Erreur interne au démarrage de l'exécution en tâche de fond : {e}"
                    db_entry.error_code = ErrorCode.INTERNAL_ERROR
                    db_entry.error_retryable = False
                    db_entry.current_step = None
                    db_entry.updated_at = datetime.now(timezone.utc)
                    session.add(db_entry)
                    session.commit()
        except Exception:
            pass
        finally:
            # Nettoyer le tracker d'agents même en cas d'erreur sévère
            _cleanup_persisted_agents(db_entry_id)

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
            .order_by(ExecutionHistory.created_at.desc())
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
        .order_by(ExecutionHistory.created_at.asc())
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
                .order_by(ExecutionHistory.created_at.asc())
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
        .order_by(Conversation.updated_at.desc())
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
        .order_by(ExecutionHistory.created_at.asc())
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
        .where(ExecutionHistory.repo_owner.is_not(None))
        .where(ExecutionHistory.repo_name.is_not(None))
        .order_by(ExecutionHistory.created_at.desc())
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
_METRICS_EXECUTION_LIMIT = 1000

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
    # Borne AVEC fuseau : SQLModel refuse de lier un datetime naïf à ces colonnes.
    since = datetime.now(timezone.utc) - timedelta(days=days)
    uid = user.get("id")
    statement = (
        select(
            ExecutionHistory.id, ExecutionHistory.status, ExecutionHistory.workflow,
            ExecutionHistory.created_at, ExecutionHistory.updated_at,
            ExecutionHistory.rate_limit_hits, ExecutionHistory.total_wait_time_seconds,
            ExecutionHistory.error_code,
        )
        .where(ExecutionHistory.user_id == uid)
        .where(ExecutionHistory.created_at >= since)
        .where(ExecutionHistory.status.in_(("success", "failed")))
        .order_by(ExecutionHistory.created_at.desc())
        .limit(_METRICS_EXECUTION_LIMIT)
    )
    if workflow:
        statement = statement.where(ExecutionHistory.workflow == workflow)
    executions = [
        {
            "id": r[0], "status": r[1], "workflow": r[2], "created_at": r[3], "updated_at": r[4],
            "rate_limit_hits": r[5], "total_wait_time_seconds": r[6], "error_code": r[7],
        }
        for r in session.exec(statement).all()
    ]
    # Par paquets : une liste IN de ~1000 identifiants dépasse la limite de paramètres des anciennes
    # versions de SQLite (999) ; sans effet notable sous Postgres.
    runs = []
    ids = [e["id"] for e in executions]
    for start in range(0, len(ids), _IN_CLAUSE_CHUNK):
        runs.extend(
            row.model_dump()
            for row in session.exec(
                select(AgentRun)
                .where(AgentRun.user_id == uid)
                .where(AgentRun.execution_id.in_(ids[start:start + _IN_CLAUSE_CHUNK]))
            ).all()
        )
    result = summarize(runs, executions, days, workflow, tz_offset)
    # Tout le tableau de bord (dont les échecs par cause) est calculé sur cet échantillon : on le dit quand
    # la limite est atteinte plutôt que de laisser croire que la période entière est couverte.
    result["truncated"] = len(executions) >= _METRICS_EXECUTION_LIMIT
    return result

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
        .order_by(ExecutionHistory.created_at.desc())
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
    for agent_run in session.exec(select(AgentRun).where(AgentRun.execution_id.in_(ids))).all():
        session.delete(agent_run)
    for checkpoint in session.exec(select(ExecutionCheckpoint).where(ExecutionCheckpoint.execution_id.in_(ids))).all():
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
                ExecutionHistory.id.in_(valid_ids), ExecutionHistory.user_id == user.get("id")
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
