"""Persistance des résultats d'une exécution : sections des agents terminés (au fil de l'eau), points de reprise,
mesures par agent."""
from typing import Optional

from sqlmodel import Session, select

import database
import execution_state
from agent_metrics import build_agent_run_rows, step_for_role
from crewquestion import (
    AGENT_SECTION_SEPARATOR, MAX_AGENT_NAME_LENGTH, MAX_AGENT_OUTPUT_SIZE, RESUMABLE_STEPS,
)
from database import AgentRun, ExecutionCheckpoint, ExecutionHistory
from logs import get_logger

log = get_logger("execution_persistence")

# --- TRACKER D'AGENTS PERSISTÉS POUR IDEMPOTENCE ---
# Structure: {execution_id: set(agent_names_persisted)}
# Évite les doublons si on_task_output_complete est appelé plusieurs fois pour le même agent
persisted_agents: dict[int, set[str]] = {}

def validate_agent_data(
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

def persist_agent_runs(session: Session, db_entry: ExecutionHistory, run_metrics) -> None:
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

def persist_completed_agent(
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
    Similaire à execution_state.persist_current_step : ouvre sa propre Session thread-safe et best-effort.
    """
    if execution_id in execution_state.abandoned_execution_ids:
        log.info(f"execution_id={execution_id}: agent '{agent_name}' ignoré (exécution arrêtée).")
        return

    # Valider et nettoyer les données
    agent_name, agent_output, duration_seconds = validate_agent_data(agent_name, agent_output, duration_seconds)

    # IDEMPOTENCE: Vérifier si cet agent a déjà été persisté pour cette exécution
    if execution_id not in persisted_agents:
        persisted_agents[execution_id] = set()

    if agent_name in persisted_agents[execution_id]:
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
                persisted_agents[execution_id].add(agent_name)

                log.debug(f"execution_id={execution_id}: agent '{agent_name}' persisté ({output_size} bytes).")
    except Exception as e:
        log.warning(f"échec de la persistance de l'agent complété (execution_id={execution_id}, agent={agent_name!r}) : {type(e).__name__}: {e}")

def load_checkpoints(session: Session, execution_id: int) -> dict[str, str]:
    """{étape: sortie} sauvegardées pour une exécution (la plus récente gagne en cas de doublon)."""
    rows = session.exec(
        select(ExecutionCheckpoint).where(ExecutionCheckpoint.execution_id == execution_id).order_by(ExecutionCheckpoint.id)
    ).all()
    return {row.step: row.raw for row in rows}

def load_checkpoints_for(execution_id: int) -> dict[str, str]:
    """load_checkpoints avec sa propre Session (appelable depuis un thread, hors boucle asyncio)."""
    with Session(database.engine) as checkpoint_session:
        return load_checkpoints(checkpoint_session, execution_id)

def delete_checkpoints_for(execution_id: int) -> None:
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

def cleanup_persisted_agents(execution_id: int) -> None:
    """Nettoie le tracker d'agents persistés après que l'exécution soit terminée.

    Appelé après succès ou échec pour libérer la mémoire.
    """
    persisted_agents.pop(execution_id, None)
