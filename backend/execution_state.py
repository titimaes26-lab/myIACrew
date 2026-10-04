"""État partagé des exécutions de crew en tâche de fond : suivi des tâches, créneau d'exécution, battement de cœur et
écritures d'avancement (étape courante, tentatives, échec interne)."""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import update as sql_update
from sqlmodel import Session

import database
import memory_monitor
from database import ExecutionHistory, _env_int
from errors import ErrorCode
from logs import get_logger
from orphans import HEARTBEAT_RETRY_SECONDS, HEARTBEAT_SECONDS

log = get_logger("main")

# Références fortes vers les exécutions de crew en tâche de fond (voir _execute_crew_and_persist,
# lancée via asyncio.create_task dans execute_workflow) : un Task asyncio sans référence conservée
# ailleurs peut être ramassé par le garbage collector AVANT sa fin (piège classique documenté dans
# la doc asyncio elle-même) puisque asyncio.create_task ne retient qu'une référence FAIBLE en
# interne — une exécution de plusieurs minutes s'interromprait alors silencieusement dès le
# prochain passage du GC. task.add_done_callback(background_tasks.discard) retire l'entrée une
# fois la tâche terminée, pour que cet ensemble ne grossisse pas indéfiniment sur la durée de vie
# du process.
background_tasks: set[asyncio.Task] = set()

# Identifiants des exécutions réellement en cours dans CE process : le balayage des orphelines
# (orphans.py) ne les touche jamais, même après un long silence.
active_execution_ids: set[int] = set()

# Exécutions arrêtées pour durée maximale dépassée : leur thread de crew continue de tourner (Python ne sait pas le tuer)
# et appelle encore les callbacks de persistance ; ceux-ci ne doivent plus rien écrire sur une ligne déjà en échec
# (message d'échec mêlé à des sections d'agents, étape courante, points de reprise). Quelques entiers par arrêt.
abandoned_execution_ids: set[int] = set()

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
MAX_CONCURRENT_EXECUTIONS = _env_int("MAX_CONCURRENT_EXECUTIONS", 1, 1)

# Étape persistée tant qu'une exécution attend son créneau (jamais une clé réelle de WORKFLOW_STEPS côté frontend).
QUEUED_STEP = "queued"

# Durée maximale d'UN crew (hors attente dans la file et hors vérification de livraison), EXECUTION_TIMEOUT_S, 20 min par
# défaut. Avec un seul créneau, une exécution bloquée (appel qui ne revient pas, boucle d'un agent) retiendrait le service
# pour tous : son battement de cœur continue, le balayage des orphelines ne la libère pas. Au-delà, la tâche est annulée,
# l'exécution passe en échec (EXECUTION_TIMEOUT, réessayable) et le créneau est rendu. Limite : le crew tourne dans un
# thread que Python ne sait pas tuer ; il peut finir de s'exécuter (et consommer du quota) après l'annulation, sans effet
# sur l'exécution déjà marquée en échec.
EXECUTION_TIMEOUT_S = _env_int("EXECUTION_TIMEOUT_S", 1200, 60)

execution_semaphore = asyncio.Semaphore(MAX_CONCURRENT_EXECUTIONS)

def safe_refresh(session: Session, db_entry: ExecutionHistory, context: str) -> None:
    """Recharge db_entry depuis la base avant d'y réassigner des champs — voir ses appelants.

    Nécessaire avant de réassigner current_step à None (succès ou échec) : sans ce refresh, la
    Session de CETTE requête ignore les écritures faites entretemps par persist_current_step via
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

def persist_current_step(execution_id: int, step_key: Optional[str]) -> None:
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
    if execution_id in abandoned_execution_ids:
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

def set_attempts(execution_id: int, attempts: int) -> None:
    """Nombre de tentatives d'une exécution (2 dès qu'une relance automatique est décidée). Best-effort."""
    try:
        with Session(database.engine) as attempts_session:
            attempts_session.exec(
                sql_update(ExecutionHistory).where(ExecutionHistory.id == execution_id).values(attempts=attempts)
            )
            attempts_session.commit()
    except Exception as e:
        log.warning(f"nombre de tentatives non enregistré (execution_id={execution_id}) : {type(e).__name__}: {e}")

def touch_execution(execution_id: int) -> bool:
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

async def heartbeat_loop(execution_id: int) -> None:
    """Écrit un signe de vie toutes les HEARTBEAT_SECONDS tant que l'exécution tourne, même pendant
    une longue étape ou une pause de quota (sinon une instance voisine la croirait morte)."""
    while True:
        await asyncio.sleep(HEARTBEAT_SECONDS)
        if not await asyncio.to_thread(touch_execution, execution_id):
            # Échec (base occupée, coupure brève) : un nouvel essai rapide plutôt que d'attendre un
            # battement entier, pour ne pas laisser updated_at vieillir jusqu'au seuil des orphelines.
            await asyncio.sleep(HEARTBEAT_RETRY_SECONDS)
            await asyncio.to_thread(touch_execution, execution_id)

def track_execution_task(task: asyncio.Task, execution_id: int) -> None:
    """Un seul endroit pour tout le suivi d'une tâche de fond : référence forte (anti-GC, voir
    background_tasks), identifiant actif (jamais balayé comme orphelin) et battement de cœur."""
    heartbeat = asyncio.create_task(heartbeat_loop(execution_id))
    background_tasks.add(task)
    active_execution_ids.add(execution_id)

    def _done(_task: asyncio.Task) -> None:
        heartbeat.cancel()
        background_tasks.discard(_task)
        active_execution_ids.discard(execution_id)

    task.add_done_callback(_done)

def fail_execution(execution_id: int, message: str) -> None:
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
