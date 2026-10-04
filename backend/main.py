import asyncio
import threading
import os
import uuid
import re
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from dataclasses import dataclass
from contextlib import nullcontext
from typing import Any, List, Optional
from sqlalchemy import update as sql_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import defer
from sqlmodel import Session, col, func, select

from crewquestion import (
    AppDevelopmentCrew, CrewStepError, MAX_PRIOR_TURNS_IN_CONTEXT, QualificationResult,
    build_conversation_context, track_execution_metrics, workflow_step_keys, resumable_prefix, RESUMABLE_STEPS,
    FINALIZATION_ROLE,
    AGENT_SECTION_SEPARATOR, AGENT_SECTION_REGEX_PATTERN, MAX_AGENT_OUTPUT_SIZE, MAX_AGENT_NAME_LENGTH,
)
import database
from database import (
    create_db_and_tables, get_session, Conversation, ExecutionHistory, AgentRun, ExecutionCheckpoint, _env_int,
)
from agent_metrics import (
    ExecutionMetrics, build_agent_run_rows, flush_events, step_for_role,
)
from auth import get_current_user, close_http_client
from orphans import HEARTBEAT_RETRY_SECONDS, HEARTBEAT_SECONDS, sweep_stale_executions
from logs import get_logger
from qa_report import final_verdict
from limits import check_qualify_rate, check_user_execution_quota, record_execution_launch
from summary import partial_work_block
from errors import (
    AppError, DeliveryError, ErrorCode, ErrorInfo, ExecutionTimeoutError, classify_exception, http_status_for,
)
from github_tools import (
    verify_github_delivery, get_branch_head_sha, describe_partial_delivery, GitHubVerificationUnavailable,
    check_github_access, GitHubAccessProblem, DeliveredPullRequest, DeliveryIssue, build_repo_snapshot, track_read_cache,
    track_write_scope, WORK_BRANCH_PREFIX,
)
from delivery import render_partial_delivery_block
import memory_monitor
from routes_metrics import router as metrics_router
from error_handlers import CORS_ALLOW_CREDENTIALS, CORS_ALLOW_ORIGINS, register_error_handlers
from schemas import BulkDeleteInput, ConversationCreateInput, HistoryListEntry, UserRequestInput, WorkflowExecutionInput

log = get_logger("main")

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
# Exécutions arrêtées pour durée maximale dépassée : leur thread de crew continue de tourner (Python ne sait pas le tuer)
# et appelle encore les callbacks de persistance ; ceux-ci ne doivent plus rien écrire sur une ligne déjà en échec
# (message d'échec mêlé à des sections d'agents, étape courante, points de reprise). Quelques entiers par arrêt.
_abandoned_execution_ids: set[int] = set()

# Limite le nombre d'exécutions de crew simultanées, TOUTES conversations confondues (le
# garde-fou par conversation dans execute_workflow n'empêche qu'UNE MÊME conversation d'avoir
# deux exécutions en vol, jamais plusieurs conversations différentes en parallèle). Nécessaire
# depuis que /api/execute ne bloque plus pour toute la durée d'une exécution (voir plus bas) :
# un client qui enchaîne des demandes sur plusieurs conversations différentes ne se heurte plus
# naturellement à la limite qu'imposait le nombre de connexions HTTP lentes qu'il pouvait
# maintenir ouvertes simultanément. Valeur volontairement basse : chaque exécution instancie son
# propre AppDevelopmentCrew (voir _execute_crew_and_persist), coûteux en mémoire sur ce service à
# ressources limitées (voir _CONTAINER_MEMORY_LIMIT_MB plus bas).
# 1 par défaut (MAX_CONCURRENT_EXECUTIONS) : le quota Gemini de l'offre gratuite (15 requêtes par minute) est partagé
# par tout le projet ; deux crews en parallèle le saturent (voir llm_limiter.py, qui étale les requêtes mais ne les
# supprime pas). Les exécutions suivantes attendent leur tour (étape « queued »). À relever avec une offre payante.
# Portée : UN SEUL process (un asyncio.Semaphore n'est jamais partagé entre workers/instances).
# Suffisant tant que ce service tourne en un seul worker Uvicorn sur une seule instance Render
# (le cas aujourd'hui) — passer à plusieurs workers ou à plusieurs instances romprait cette
# limite globale sans avertissement (chaque process aurait alors sa PROPRE limite, portant
# le vrai plafond à cette valeur * nombre de process).
_MAX_CONCURRENT_EXECUTIONS = _env_int("MAX_CONCURRENT_EXECUTIONS", 1, 1)
# Étape persistée tant qu'une exécution attend son créneau (jamais une clé réelle de WORKFLOW_STEPS côté frontend).
QUEUED_STEP = "queued"

# Durée maximale d'UN crew (hors attente dans la file et hors vérification de livraison), EXECUTION_TIMEOUT_S, 20 min par
# défaut. Avec un seul créneau, une exécution bloquée (appel qui ne revient pas, boucle d'un agent) retiendrait le service
# pour tous : son battement de cœur continue, le balayage des orphelines ne la libère pas. Au-delà, la tâche est annulée,
# l'exécution passe en échec (EXECUTION_TIMEOUT, réessayable) et le créneau est rendu. Limite : le crew tourne dans un
# thread que Python ne sait pas tuer ; il peut finir de s'exécuter (et consommer du quota) après l'annulation, sans effet
# sur l'exécution déjà marquée en échec.
_EXECUTION_TIMEOUT_S = _env_int("EXECUTION_TIMEOUT_S", 1200, 60)
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
        with Session(database.engine) as backfill:
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
        log.warning(f"rattrapage des verdicts QA ignoré : {type(e).__name__}: {e}")

# 2. ÉVÉNEMENT DE DÉMARRAGE (Création des tables BDD)
@app.on_event("startup")
def on_startup():
    create_db_and_tables()
    _backfill_qa_verdicts()
    # Imprimé une seule fois, au démarrage : rend le plafond mémoire du conteneur visible dans les
    # logs Render dès le boot, sans attendre qu'une exécution déclenche la première ligne [MEM]
    # (memory_monitor.log_memory) — utile pour juger d'emblée si le plan actuel a une marge suffisante pour ce
    # type de charge (CrewAI + plusieurs agents Gemini), avant même de lancer quoi que ce soit.
    memory_monitor.log_memory("démarrage du service")
    # Libère les conversations bloquées par une exécution morte avec l'ancien process (crash, OOM,
    # redéploiement). Best-effort : ne doit jamais empêcher le service de démarrer.
    try:
        with Session(database.engine) as session:
            swept = sweep_stale_executions(session, active_ids=_active_execution_ids)
        if swept:
            log.info(f"Démarrage : {len(swept)} exécution(s) orpheline(s) marquée(s) interrompue(s) : {swept}")
    except Exception as e:
        log.warning(f"balayage des exécutions orphelines impossible : {type(e).__name__}: {e}")

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
        log.info(f"Arrêt du service : attente (best-effort, jusqu'à {_SHUTDOWN_DRAIN_TIMEOUT_S}s) de "
            f"{len(_background_tasks)} exécution(s) de crew encore en tâche de fond...")
        await asyncio.wait(list(_background_tasks), timeout=_SHUTDOWN_DRAIN_TIMEOUT_S)
    await close_http_client()


# CORS (politique : voir error_handlers.py) puis format d'erreur unique de l'API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS,
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)
register_error_handlers(app)
app.include_router(metrics_router)


crew_instance = AppDevelopmentCrew()


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
        log.warning(f"échec du refresh de db_entry avant finalisation ({context}, id={db_entry.id}) : {type(e).__name__}: {e}")

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
    if execution_id in _abandoned_execution_ids:
        return
    memory_monitor.log_memory(f"execution_id={execution_id}, étape={step_key!r}")
    try:
        with Session(database.engine) as step_session:
            entry = step_session.get(ExecutionHistory, execution_id)
            if entry is not None:
                entry.current_step = step_key
                # Signe de vie : c'est ce qui distingue une exécution lente d'une exécution orpheline
                # (voir orphans.sweep_stale_executions).
                entry.updated_at = datetime.now(timezone.utc)
                step_session.add(entry)
                step_session.commit()
    except Exception as e:
        log.warning(f"échec de la mise à jour de la progression (execution_id={execution_id}, step={step_key!r}) : {type(e).__name__}: {e}")

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
        with Session(database.engine) as attempts_session:
            attempts_session.exec(
                sql_update(ExecutionHistory).where(ExecutionHistory.id == execution_id).values(attempts=attempts)
            )
            attempts_session.commit()
    except Exception as e:
        log.warning(f"nombre de tentatives non enregistré (execution_id={execution_id}) : {type(e).__name__}: {e}")

def _touch_execution(execution_id: int) -> bool:
    """Signe de vie (updated_at) d'une exécution en cours. Best-effort ; True si écrit sans erreur.
    Un seul UPDATE conditionnel (status='running') : un battement tardif ne peut pas écraser le
    updated_at final d'une exécution déjà terminée (la durée affichée s'en déduit)."""
    try:
        with Session(database.engine) as heartbeat_session:
            heartbeat_session.exec(
                sql_update(ExecutionHistory)
                .where(ExecutionHistory.id == execution_id, ExecutionHistory.status == "running")
                .values(updated_at=datetime.now(timezone.utc))
            )
            heartbeat_session.commit()
        return True
    except Exception as e:
        log.warning(f"battement de l'exécution {execution_id} impossible : {type(e).__name__}: {e}")
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
        log.warning(f"métriques par agent non enregistrées pour execution_id={db_entry.id} : {type(e).__name__}: {e}")


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
    if execution_id in _abandoned_execution_ids:
        log.info(f"execution_id={execution_id}: agent '{agent_name}' ignoré (exécution arrêtée).")
        return

    # Valider et nettoyer les données
    agent_name, agent_output, duration_seconds = _validate_agent_data(agent_name, agent_output, duration_seconds)

    # IDEMPOTENCE: Vérifier si cet agent a déjà été persisté pour cette exécution
    if execution_id not in _persisted_agents:
        _persisted_agents[execution_id] = set()

    if agent_name in _persisted_agents[execution_id]:
        log.info(f"execution_id={execution_id}: agent '{agent_name}' déjà persisté, skip (idempotence).")
        return

    try:
        with Session(database.engine) as agent_session:
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

                log.debug(f"execution_id={execution_id}: agent '{agent_name}' persisté ({output_size} bytes).")
    except Exception as e:
        log.warning(f"échec de la persistance de l'agent complété (execution_id={execution_id}, agent={agent_name!r}) : {type(e).__name__}: {e}")

def _load_checkpoints(session: Session, execution_id: int) -> dict[str, str]:
    """{étape: sortie} sauvegardées pour une exécution (la plus récente gagne en cas de doublon)."""
    rows = session.exec(
        select(ExecutionCheckpoint).where(ExecutionCheckpoint.execution_id == execution_id).order_by(ExecutionCheckpoint.id)
    ).all()
    return {row.step: row.raw for row in rows}

def _load_checkpoints_for(execution_id: int) -> dict[str, str]:
    """_load_checkpoints avec sa propre Session (appelable depuis un thread, hors boucle asyncio)."""
    with Session(database.engine) as checkpoint_session:
        return _load_checkpoints(checkpoint_session, execution_id)

def _delete_checkpoints_for(execution_id: int) -> None:
    """Les points de reprise ne servent qu'à une exécution en échec : inutiles (et volumineux, le code
    complet de l'Analyste y figure) une fois celle-ci réussie. Best-effort."""
    try:
        with Session(database.engine) as cleanup_session:
            for checkpoint in cleanup_session.exec(
                select(ExecutionCheckpoint).where(ExecutionCheckpoint.execution_id == execution_id)
            ).all():
                cleanup_session.delete(checkpoint)
            cleanup_session.commit()
    except Exception as e:
        log.warning(f"purge des points de reprise impossible (execution_id={execution_id}) : {type(e).__name__}: {e}")

def _fail_execution(execution_id: int, message: str) -> None:
    """Marque une exécution « failed » (interne) quand plus aucun autre chemin ne peut le faire. Best-effort."""
    try:
        with Session(database.engine) as fail_session:
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

def _failed_before_development(exc: BaseException, request_type: str, scope: Optional[str] = None) -> bool:
    """Vrai si l'échec est celui d'une étape reprenable (design, architecture, diagnostic) : seul cas où
    une relance automatique ne réécrit rien sur GitHub. Faux pour tout échec hors étape (création du crew,
    vérification de livraison) ou à partir du développement."""
    if not isinstance(exc, CrewStepError):
        return False
    keys = workflow_step_keys(request_type, scope)
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
        # Seule « PETIT » change les étapes : GRAND, absent (ancienne ligne, clarification) = parcours complet.
        or (previous.scope == "PETIT") != (data.scope == "PETIT")
        # Même demande : réutiliser design/architecture/code d'une AUTRE demande ferait committer du code pour
        # la mauvaise demande.
        or (previous.user_request or "").strip() != data.user_request.strip()
        or (previous.clarifications or "").strip() != (data.clarifications or "").strip()
        or (previous.repo_owner or None) != data.repo_owner or (previous.repo_name or None) != data.repo_name
        or (previous.base_branch or None) != ((data.base_branch or "main") if data.repo_owner and data.repo_name else None)
    ):
        return {}
    saved = _load_checkpoints(session, previous.id)
    prefix = resumable_prefix(workflow_step_keys(data.target_workflow, data.scope), saved)
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

    "queued" persisté AVANT d'acquérir le sémaphore (jamais après) : si les emplacements sont
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
    await asyncio.to_thread(_persist_current_step, db_entry_id, QUEUED_STEP)
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
            await asyncio.to_thread(_persist_current_step, db_entry_id, QUEUED_STEP)
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
    work_branch: str, base_branch: Optional[str], has_repo_target: bool, branch_exists: bool, repo_snapshot: str = "",
    previous_plan: str = "",
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
        # Aperçu du repo lu en Python (vide : l'agent lit lui-même avec ses outils).
        'repo_snapshot': repo_snapshot,
        # Plan d'architecture du tour précédent de la conversation (vide : l'Architecte part de zéro).
        'previous_plan': previous_plan,
    }


async def _prefetch_repo_snapshot(
    data: "WorkflowExecutionInput", work_branch: str, base_branch: Optional[str], has_repo_target: bool, branch_exists: bool,
    resume_outputs: Optional[dict[str, str]] = None,
) -> str:
    """Aperçu du repo pour l'Architecte ET le Diagnostic (racine, résumé de package.json/tsconfig.json, src), lu en
    Python plutôt que par leurs appels d'outils : autant de tours de LLM en moins, et la même vue pour les deux.
    Seulement avec un repository cible et si l'une de ces deux étapes va réellement tourner (pas réutilisée par une
    reprise, pas sautée par une petite FEATURE).
    Best-effort : toute erreur renvoie "" et l'agent lit lui-même (même règle de branche que _repo_instructions)."""
    if not has_repo_target:
        return ""
    steps = workflow_step_keys(data.target_workflow, data.scope)
    if not any(step in steps and step not in (resume_outputs or {}) for step in ("architecture", "diagnostic")):
        return ""
    branch = work_branch if branch_exists else (base_branch or "main")
    try:
        return await asyncio.to_thread(build_repo_snapshot, data.repo_owner, data.repo_name, branch)
    except Exception as e:
        log.warning(f"aperçu du repository non lu ({type(e).__name__}: {e}) : l'agent lira lui-même.")
        return ""


MAX_PREVIOUS_PLAN_CHARS = 6000
_AGENT_DURATION_MARKER = re.compile(r"<!--agent-duration:[0-9.]+-->\s*")


def _previous_architecture_plan(
    session: Session, data: "WorkflowExecutionInput", has_repo_target: bool, user_id, conversation_id: int, current_id: int,
) -> str:
    """Plan de l'Architecte du dernier tour RÉUSSI de cette conversation qui en a produit un (parmi les 3 derniers),
    sur le même repository cible, ou "". Sert de base à l'Architecte (il ne décrit alors que ce qui change) ; il peut
    être périmé, la consigne lui demande de le confronter à l'aperçu du repository."""
    target = (data.repo_owner, data.repo_name) if has_repo_target else (None, None)
    rows = session.exec(
        select(ExecutionHistory)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .where(ExecutionHistory.user_id == user_id)
        .where(ExecutionHistory.id != current_id)
        .where(ExecutionHistory.status == "success")
        .order_by(col(ExecutionHistory.created_at).desc())
        .limit(3)
    ).all()
    for row in rows:
        if (row.repo_owner or None, row.repo_name or None) != target:
            continue
        if "architecture" not in workflow_step_keys(row.workflow, row.scope):
            continue
        for agent_name, content in _parse_completed_agents(row.result or "").items():
            if step_for_role(agent_name) != "architecture":
                continue
            body = content.split("\n", 1)[1] if content.startswith("## ") and "\n" in content else content
            body = _AGENT_DURATION_MARKER.sub("", body).strip()
            if body:
                cut = "\n[… tronqué]" if len(body) > MAX_PREVIOUS_PLAN_CHARS else ""
                return body[:MAX_PREVIOUS_PLAN_CHARS] + cut
    return ""


async def _load_previous_plan(
    data: "WorkflowExecutionInput", has_repo_target: bool, user_id, conversation_id: int, current_id: int,
    resume_outputs: Optional[dict[str, str]],
) -> str:
    """Plan du tour précédent à donner à l'Architecte, ou "" : seulement si son étape va réellement tourner (ni
    sautée par une petite FEATURE, ni réutilisée par une reprise). Best-effort : une erreur donne ""."""
    if "architecture" not in workflow_step_keys(data.target_workflow, data.scope) or "architecture" in (resume_outputs or {}):
        return ""

    def read() -> str:
        with Session(database.engine) as plan_session:
            return _previous_architecture_plan(plan_session, data, has_repo_target, user_id, conversation_id, current_id)

    try:
        return await asyncio.to_thread(read)
    except Exception as e:
        log.warning(f"plan du tour précédent non lu ({type(e).__name__}: {e}) : l'Architecte part de zéro.")
        return ""


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
    resume_outputs: Optional[dict[str, str]], scope: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Any:
    """Lance le crew avec ses métriques et son sondage mémoire périodique (toujours annulé, succès ou non)."""
    memory_ticker = asyncio.create_task(memory_monitor.periodic_memory_logger(f"execution_id={execution_id}, sondage périodique"))
    try:
        with track_execution_metrics() as run_metrics:
            state.metrics = run_metrics
            try:
                async with asyncio.timeout(_EXECUTION_TIMEOUT_S) as deadline:
                    return await crew.run_dynamic_crew(
                        inputs=inputs,
                        request_type=request_type,
                        on_step_change=lambda step_key: _persist_current_step(execution_id, step_key),
                        on_task_output_complete=lambda agent_name, output, duration: _persist_completed_agent(
                            execution_id, agent_name, output, duration),
                        resume_outputs=resume_outputs,
                        # Seulement quand il y en a un : le parcours complet reste l'appel historique, sans argument en plus.
                        **({"scope": scope} if scope else {}),
                    )
            except TimeoutError:
                # Une TimeoutError levée PAR le crew (délai d'un appel) n'est pas notre échéance : elle garde sa classe.
                if not deadline.expired():
                    raise
                # Le thread du crew survit à l'annulation : il ne doit plus rien écrire (base ni GitHub).
                _abandoned_execution_ids.add(execution_id)
                if cancel_event is not None:
                    cancel_event.set()
                waited = run_metrics.total_wait_time
                detail = f"durée maximale d'une exécution dépassée ({_EXECUTION_TIMEOUT_S / 60:g} min)"
                if waited > 0:
                    detail += f", dont {waited / 60:.1f} min d'attente du quota du modèle"
                raise ExecutionTimeoutError(detail, quota_wait_seconds=waited, limit_seconds=_EXECUTION_TIMEOUT_S) from None
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


# Rapport de l'agent joint à un échec de livraison ; le détail technique stocké garde en plus la place du constat
# (sans cette marge, la fin du rapport serait coupée, c'est précisément la cause qui disparaîtrait).
_AGENT_REPORT_CHARS = 3000
_TECHNICAL_DETAIL_CHARS = _AGENT_REPORT_CHARS + 1500


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
        f"{raw_result[:_AGENT_REPORT_CHARS]}"
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
        raise DeliveryError(_delivery_failure_message(issue, raw_result))
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
    log.info(f"execution_id={db_entry.id} : terminée avec succès (tâche de fond).")
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
        log.warning(f"validation du succès impossible (execution_id={db_entry_id}), nouvel essai : "
              f"{type(first_error).__name__}: {first_error}")
    try:
        with Session(database.engine) as fresh:
            fresh_entry = fresh.get(ExecutionHistory, db_entry_id)
            fresh_conversation = fresh.get(Conversation, conversation_id)
            if fresh_entry is None or fresh_conversation is None:
                return
            await _persist_success(fresh, fresh_entry, fresh_conversation, raw_result, state)
    except Exception as second_error:
        log.error(f"succès non enregistré (execution_id={db_entry_id}) : {type(second_error).__name__}: "
              f"{second_error}")
        _cleanup_persisted_agents(db_entry_id)


def _failure_detail(exc: BaseException, info: ErrorInfo) -> str:
    """Message d'échec : cause lisible (sinon texte d'origine) suivie du détail technique tronqué — sans lui,
    ni l'utilisateur ni la base ne diraient POURQUOI (ce que reproche un garde-fou, délai « retry after »)."""
    technical = str(exc).strip()[:_TECHNICAL_DETAIL_CHARS]
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
    # Travail déjà accompli : les sections des agents terminés sont dans `result` jusqu'à ce qu'il soit écrasé
    # ci-dessous. Ajouté APRÈS le bloc GitHub : le frontend le retire en premier (splitPartialWork).
    completed = [(name, text) for name, text in _parse_completed_agents(db_entry.result or "").items()]
    partial = partial_work_block(completed)
    if partial:
        detail += "\n\n" + partial
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
    if not (auto_retry_allowed and info.retryable and _failed_before_development(exc, data.target_workflow, data.scope)):
        return None
    saved = await asyncio.to_thread(_load_checkpoints_for, db_entry_id)
    prefix = resumable_prefix(workflow_step_keys(data.target_workflow, data.scope), saved)
    if len(prefix) < exc.step_index - 1:
        return None
    log.info(f"execution_id={db_entry_id} : échec transitoire ({info.code}), nouvelle tentative "
        f"automatique dans {AUTO_RETRY_DELAY_S}s.")
    return {key: saved[key] for key in prefix}


def _mark_startup_failure(db_entry_id: int, exc: BaseException) -> None:
    """Dernier filet : l'ouverture de la Session ou les `get` initiaux ont échoué (pool épuisé, coupure base).
    Sans lui la ligne resterait « running » pour toujours. Best-effort : si la base est injoignable, rien de
    mieux n'est possible depuis ce process."""
    log.warning(f"échec du démarrage de la tâche de fond pour db_entry={db_entry_id} : {exc}")
    try:
        with Session(database.engine) as session:
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
        with Session(database.engine) as session:
            db_entry = session.get(ExecutionHistory, db_entry_id)
            conversation = session.get(Conversation, conversation_id)
            if db_entry is None or conversation is None:
                # Ne devrait jamais arriver (enregistrements tout juste commités par execute_workflow) : un
                # print plutôt qu'une exception que personne ne retrouverait (rien n'attend cette tâche).
                log.warning(f"db_entry={db_entry_id} ou conversation={conversation_id} "
                    "introuvable au lancement de la tâche de fond, exécution abandonnée.")
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
                memory_monitor.log_memory(f"execution_id={db_entry.id}, crew instancié, avant kickoff")
                # Cache de lecture GitHub partagé par l'aperçu et les agents de CETTE exécution (voir track_read_cache) ;
                # écritures GitHub limitées à la branche de travail de cette exécution (voir track_write_scope).
                cancel_event = threading.Event()
                with track_read_cache(), (track_write_scope(work_branch, cancel_event) if work_branch else nullcontext()):
                    branch_exists = state.sha_before is not None
                    repo_snapshot = await _prefetch_repo_snapshot(
                        data, work_branch, normalized_base_branch, has_repo_target, branch_exists, resume_outputs)
                    previous_plan = await _load_previous_plan(
                        data, has_repo_target, db_entry.user_id, conversation_id, db_entry.id, resume_outputs)
                    inputs = _crew_inputs(
                        data, conversation_id, final_prompt, conversation_context, work_branch,
                        normalized_base_branch, has_repo_target, branch_exists, repo_snapshot, previous_plan,
                    )
                    result = await _run_crew(
                        crew, state, db_entry.id, data.target_workflow, inputs, resume_outputs, data.scope, cancel_event)
                raw_result = str(result.raw) if hasattr(result, 'raw') else str(result)

                # Un rapport « réussi » ne prouve rien sur GitHub (un outil github_* en échec renvoie du texte à
                # l'agent, jamais une exception). Vérifié seulement quand du code a été écrit (pas ANALYSE_ONLY).
                delivered_pr = None
                if should_verify_github_delivery:
                    delivered_pr = await _verify_delivery(
                        data, work_branch, normalized_base_branch, state.sha_before, raw_result)
                raw_result = _with_pull_request_line(raw_result, delivered_pr)
            except Exception as e:
                memory_monitor.log_memory(f"execution_id={db_entry.id}, exception attrapée")
                log.error("erreur CrewAI pendant l'exécution", exc_info=True)
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

def _prior_turns(session: Session, conversation_id: int) -> tuple[list[int], str]:
    """(identifiants des tours encore « running », rappel des derniers tours) d'une conversation. Seuls les
    MAX_PRIOR_TURNS_IN_CONTEXT derniers tours sont lus en entier (pour leur résumé) ; le nombre total sert à signaler
    ceux qui sont omis. Même contenu que lire toute la conversation, sans charger les résultats des tours anciens."""
    in_conversation = ExecutionHistory.conversation_id == conversation_id
    running_ids = [
        row_id for row_id in session.exec(
            select(ExecutionHistory.id).where(in_conversation).where(ExecutionHistory.status == "running")
        ).all() if row_id is not None
    ]
    total = session.exec(select(func.count()).select_from(ExecutionHistory).where(in_conversation)).one()
    recent = session.exec(
        select(ExecutionHistory).where(in_conversation)
        .order_by(col(ExecutionHistory.created_at).desc(), col(ExecutionHistory.id).desc())
        .limit(MAX_PRIOR_TURNS_IN_CONTEXT)
    ).all()
    return running_ids, build_conversation_context(list(reversed(recent)), total_count=total)


def _previous_work_branch(
    session: Session, conversation_id: int, owner: Optional[str], repo: Optional[str], base_branch: Optional[str],
) -> str:
    """Branche de travail du dernier tour de la conversation sur le même repository et la même branche de base,
    quel que soit son statut (« failed » compris : « Relancer » continue sur la même branche et la même PR), ou ""."""
    found = session.exec(
        select(ExecutionHistory.work_branch)
        .where(ExecutionHistory.conversation_id == conversation_id)
        .where(col(ExecutionHistory.work_branch).is_not(None))
        .where(col(ExecutionHistory.work_branch) != "")
        .where(ExecutionHistory.repo_owner == owner)
        .where(ExecutionHistory.repo_name == repo)
        .where(ExecutionHistory.base_branch == base_branch)
        .order_by(col(ExecutionHistory.created_at).desc(), col(ExecutionHistory.id).desc())
        .limit(1)
    ).first()
    return found or ""


def _load_qualification_context(conversation_id: int, user_id) -> str | None:
    """Rappel des tours précédents pour /api/qualify, ou None si la conversation est introuvable
    ou n'appartient pas à cet utilisateur. Synchrone (accès DB bloquant) : à appeler via
    asyncio.to_thread, avec sa propre Session, pour ne pas bloquer la boucle asyncio."""
    with Session(database.engine) as session:
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
    check_qualify_rate(user.get("id"))
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
        log.error("erreur CrewAI pendant la qualification", exc_info=True)
        # Pas de str(e) dans la réponse : le détail réel reste dans les logs ci-dessus.
        info = classify_exception(e)
        raise AppError(
            http_status_for(info.code), info.code,
            info.message or "La qualification de la demande a échoué.", info.retryable,
        )

_CONVERSATION_BUSY_MESSAGE = (
    "Une exécution est déjà en cours pour cette conversation. Attends qu'elle se termine avant d'envoyer un nouveau message."
)


def _ensure_conversation_idle(session: Session, conversation_id: int) -> str:
    """Refuse (409) si une exécution est déjà en cours sur cette conversation ; renvoie sinon le rappel de ses tours.

    Tours précédents : donnent aux agents un rappel de ce qui a déjà été demandé/livré, et permettent de continuer sur
    la même branche de travail plutôt que d'en ouvrir une nouvelle déconnectée à chaque message. Trois requêtes ciblées
    (tours en cours, derniers tours, branche) plutôt que de relire toute la conversation avec le résultat de chaque tour.

    Empêche deux exécutions concurrentes sur la même conversation. Nécessaire depuis la réutilisation du work_branch
    entre tours : sans ce garde-fou, deux requêtes parallèles écriraient toutes les deux sur la même branche via
    github_write_file/github_edit_file, qui se basent sur le SHA du fichier pour détecter les conflits (optimistic
    concurrency) — l'une des deux échouerait avec un SHA obsolète au lieu d'une erreur claire. Un tour resté bloqué à
    "running" (crash serveur en cours d'exécution) est balayé (orphans.sweep_stale_executions) s'il n'a plus donné signe
    de vie : il ne doit pas bloquer la conversation pour toujours."""
    running_ids, conversation_context = _prior_turns(session, conversation_id)
    if running_ids and sweep_stale_executions(session, active_ids=_active_execution_ids, conversation_id=conversation_id):
        session.expire_all()
        running_ids, conversation_context = _prior_turns(session, conversation_id)
    if running_ids:
        raise HTTPException(status_code=409, detail=_CONVERSATION_BUSY_MESSAGE)
    return conversation_context


@app.post("/api/execute")
async def execute_workflow(
    data: WorkflowExecutionInput,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Étape 2 : Lancement dynamique des agents & enregistrement BDD"""
    memory_monitor.log_memory("début /api/execute")
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

    # Conversation existante : son conflit (double clic, deux onglets) est testé AVANT les plafonds, pour garder le message
    # précis « déjà en cours pour cette conversation ». Rien n'est écrit à ce stade.
    conversation_context = _ensure_conversation_idle(session, conversation.id) if conversation is not None else ""

    # Plafonds par utilisateur (exécutions simultanées, exécutions par heure) : un compte ne sature pas les autres.
    # AVANT toute écriture : un refus ne laisse pas de conversation vide. Quelques lectures légères en base (balayage
    # compris), comme les autres accès à `session` de ce point d'accès.
    check_user_execution_quota(session, user.get("id"), _active_execution_ids)

    if conversation is None:
        conversation = Conversation(
            user_id=user.get("id"),
            title=data.user_request.strip()[:80] or "Nouvelle conversation",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)

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
        work_branch = _previous_work_branch(session, conversation.id, data.repo_owner, data.repo_name, normalized_base_branch)
        if not work_branch:
            work_branch = f"{WORK_BRANCH_PREFIX}{data.target_workflow.lower()}-{uuid.uuid4().hex[:8]}"

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
        scope=data.scope,
        attempts=1,
        reused_steps=len(resume_outputs),
    )
    session.add(db_entry)
    try:
        session.commit()
    except IntegrityError:
        # Index unique partiel (database.ONE_RUNNING_PER_CONVERSATION_INDEX) : une requête simultanée a pris la place
        # entre le contrôle ci-dessus et cet insert. Même réponse que le contrôle applicatif.
        session.rollback()
        raise HTTPException(status_code=409, detail=_CONVERSATION_BUSY_MESSAGE)
    session.refresh(db_entry)
    record_execution_launch(session, user.get("id"))

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
def create_conversation(
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
def list_conversations(
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
def get_conversation_messages(
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

def _queue_ahead(session: Session, execution_id: int, current_step: Optional[str], created_at: datetime) -> int:
    """Nombre d'exécutions (tous utilisateurs) devant celle-ci : celles qui tournent réellement (étape réelle) et celles
    en attente créées avant elle. Un décompte seulement, rien d'autre d'une exécution d'un autre compte."""
    mine = _aware_utc(created_at)
    rows = session.exec(
        select(ExecutionHistory.id, ExecutionHistory.current_step, ExecutionHistory.created_at)
        .where(ExecutionHistory.status == "running")
    ).all()
    return sum(
        1 for row_id, step, created in rows
        if row_id != execution_id and (step not in (None, QUEUED_STEP) or _aware_utc(created) < mine)
    )


def _aware_utc(value: datetime) -> datetime:
    # SQLite renvoie des datetimes naïfs (UTC) même pour une colonne écrite avec fuseau.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


@app.get("/api/conversations/{conversation_id}/progress")
def get_conversation_progress(
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
        select(
            ExecutionHistory.id, ExecutionHistory.status, ExecutionHistory.current_step, ExecutionHistory.result,
            ExecutionHistory.created_at,
        )
        .where(ExecutionHistory.conversation_id == conversation_id)
        .where(ExecutionHistory.status == "running")
    )
    row = session.exec(statement).first()
    if row is None:
        return {"id": None, "status": None, "current_step": None, "completed_agents": {}, "queue_ahead": None}

    completed_agents = {}
    if row[3]:  # if result is not None
        completed_agents = _parse_completed_agents(row[3])

    return {
        "id": row[0],
        "status": row[1],
        "current_step": row[2],
        "completed_agents": completed_agents,
        "queue_ahead": _queue_ahead(session, row[0], row[2], row[4]) if row[2] == QUEUED_STEP else None,
    }

@app.get("/api/repo-targets")
def list_repo_targets(
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


@app.get("/api/executions/{execution_id}", response_model=ExecutionHistory)
def get_execution(
    execution_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """UNE exécution de l'utilisateur, résultat complet compris (404 si elle n'est pas à lui) : permet de resynchroniser
    un tour sans relire toute la conversation."""
    entry = session.get(ExecutionHistory, execution_id)
    if not entry or entry.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Exécution introuvable.")
    return entry


@app.get("/api/history", response_model=List[HistoryListEntry])
def get_history(
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
        # defer : le résultat et les précisions ne sont même pas lus en base (jamais renvoyés par cette liste).
        .options(defer(col(ExecutionHistory.result)), defer(col(ExecutionHistory.clarifications)))
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
def delete_history_entry(
    execution_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Supprime une exécution de l'historique de l'utilisateur courant."""
    log.debug("suppression de l'exécution %s demandée", execution_id)
    # Une exécution orpheline (plus de signe de vie) devient « failed » donc supprimable.
    sweep_stale_executions(session, active_ids=_active_execution_ids, user_id=user.get("id"), ids=[execution_id])
    session.expire_all()
    entry = session.get(ExecutionHistory, execution_id)
    if not entry:
        log.info("suppression : exécution %s introuvable", execution_id)
        raise HTTPException(status_code=404, detail="Exécution introuvable.")
    if entry.user_id != user.get("id"):
        # Ni identifiant d'utilisateur ni contenu dans le log : l'exécution d'un autre compte reste invisible (404).
        log.warning("suppression refusée : l'exécution %s n'appartient pas à l'utilisateur de la requête", execution_id)
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
        log.info("suppression refusée : l'exécution %s est encore en cours", execution_id)
        raise HTTPException(status_code=409, detail="Impossible de supprimer une exécution encore en cours.")

    _delete_executions(session, [entry])
    session.commit()
    log.info("exécution %s supprimée", execution_id)
    return {"status": "deleted", "id": execution_id}

@app.post("/api/history/bulk-delete")
def bulk_delete_history(
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
