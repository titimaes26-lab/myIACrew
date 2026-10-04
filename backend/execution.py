"""Lancement d'une exécution en tâche de fond : créneau d'exécution, crew avec sa durée maximale, puis issue (succès, échec,
seconde tentative automatique)."""
import asyncio
import threading
from contextlib import nullcontext
from typing import Any, Optional

from sqlmodel import Session

import database
import execution_context
import execution_outcomes
import execution_persistence
import execution_state
import memory_monitor
from metrics_collect import flush_events
from crewquestion import AppDevelopmentCrew, track_execution_metrics
from database import Conversation, ExecutionHistory
from errors import ExecutionTimeoutError, classify_exception
from github_guards import track_write_scope
from github_client import track_read_cache
from logs import get_logger
from schemas import WorkflowExecutionInput

log = get_logger("execution")

async def execute_crew_and_persist(
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
    """Acquiert execution_state.execution_semaphore (voir sa définition : borne le nombre d'exécutions de crew
    simultanées, TOUTES conversations confondues) avant de lancer run_crew_and_persist, qui porte
    toute la logique réelle (voir sa propre docstring) — séparée dans sa propre fonction plutôt que
    de tout indenter d'un niveau ici, pour un diff plus lisible que le simple ajout de ce garde-fou
    de concurrence globale ne justifierait pas autrement.

    "queued" persisté AVANT d'acquérir le sémaphore (jamais après) : si les emplacements sont
    déjà occupés par d'autres exécutions, potentiellement longues de plusieurs minutes (voir
    execution_state.MAX_CONCURRENT_EXECUTIONS), cette exécution-ci peut rester bloquée ici un bon moment AVANT que
    le crew ne soit même instancié — current_step resterait alors None tout ce temps, ce que
    StepIndicator (frontend) interprète comme "aucun signal réel encore reçu" et comblerait par une
    estimation basée sur le temps écoulé, faisant défiler puis "terminer" toutes les étapes du
    workflow en quelques dizaines de secondes alors que rien n'a commencé. "queued" est une clé
    dédiée (jamais une clé réelle de WORKFLOW_STEPS côté frontend) : le tout premier appel à
    on_step_change une fois le crew réellement lancé (voir run_crew_and_persist plus bas) l'écrase
    naturellement avec la vraie première étape, sans action supplémentaire ici.
    """
    await asyncio.to_thread(execution_state.persist_current_step, db_entry_id, execution_state.QUEUED_STEP)
    async with execution_state.execution_semaphore:
        retry_outputs = await run_crew_and_persist(
            db_entry_id, conversation_id, data, has_repo_target, should_verify_github_delivery,
            work_branch, normalized_base_branch, final_prompt, conversation_context,
            resume_outputs=resume_outputs,
        )
    if retry_outputs is not None:
        # Seconde tentative automatique : l'attente se fait HORS du sémaphore (et hors de toute Session
        # de base) pour ne bloquer ni un emplacement d'exécution ni une connexion pendant 90 s. « queued »
        # (et non None) pendant l'attente : None ferait simuler une progression par StepIndicator.
        try:
            await asyncio.to_thread(execution_state.persist_current_step, db_entry_id, execution_state.QUEUED_STEP)
            await asyncio.to_thread(execution_state.set_attempts, db_entry_id, 2)
            await asyncio.sleep(execution_state.AUTO_RETRY_DELAY_S)
            async with execution_state.execution_semaphore:
                await run_crew_and_persist(
                    db_entry_id, conversation_id, data, has_repo_target, should_verify_github_delivery,
                    work_branch, normalized_base_branch, final_prompt, conversation_context,
                    resume_outputs=retry_outputs, auto_retry_allowed=False,
                )
        except BaseException as e:
            # Annulation pendant l'attente (arrêt du service) ou erreur imprévue avant la 2e tentative :
            # la ligne ne doit pas rester « running » et le suivi d'idempotence ne doit pas fuir.
            execution_state.fail_execution(db_entry_id, f"Exécution interrompue pendant l'attente de la nouvelle tentative : {type(e).__name__}")
            execution_persistence.cleanup_persisted_agents(db_entry_id)
            raise

async def run_crew(
    crew: AppDevelopmentCrew, state: execution_context.RunState, execution_id: int, request_type: str, inputs: dict,
    resume_outputs: Optional[dict[str, str]], scope: Optional[str] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Any:
    """Lance le crew avec ses métriques et son sondage mémoire périodique (toujours annulé, succès ou non)."""
    memory_ticker = asyncio.create_task(memory_monitor.periodic_memory_logger(f"execution_id={execution_id}, sondage périodique"))
    try:
        with track_execution_metrics() as run_metrics:
            state.metrics = run_metrics
            try:
                async with asyncio.timeout(execution_state.EXECUTION_TIMEOUT_S) as deadline:
                    return await crew.run_dynamic_crew(
                        inputs=inputs,
                        request_type=request_type,
                        on_step_change=lambda step_key: execution_state.persist_current_step(execution_id, step_key),
                        on_task_output_complete=lambda agent_name, output, duration: execution_persistence.persist_completed_agent(
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
                execution_state.abandoned_execution_ids.add(execution_id)
                if cancel_event is not None:
                    cancel_event.set()
                waited = run_metrics.total_wait_time
                detail = f"durée maximale d'une exécution dépassée ({execution_state.EXECUTION_TIMEOUT_S / 60:g} min)"
                if waited > 0:
                    detail += f", dont {waited / 60:.1f} min d'attente du quota du modèle"
                raise ExecutionTimeoutError(detail, quota_wait_seconds=waited, limit_seconds=execution_state.EXECUTION_TIMEOUT_S) from None
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

async def run_crew_and_persist(
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
    seconde tentative automatique est demandée (voir execution_outcomes.retry_outputs_if_transient), sinon None.

    Étapes : capturer le SHA de référence et instancier le crew → lancer (run_crew) → vérifier la livraison
    GitHub → persister le succès ; toute exception passe par le chemin d'échec (execution_outcomes.persist_failure).
    """
    state = execution_context.RunState()
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
                    execution_context.capture_branch_sha(data, work_branch, has_repo_target),
                )
                memory_monitor.log_memory(f"execution_id={db_entry.id}, crew instancié, avant kickoff")
                # Cache de lecture GitHub partagé par l'aperçu et les agents de CETTE exécution (voir track_read_cache) ;
                # écritures GitHub limitées à la branche de travail de cette exécution (voir track_write_scope).
                cancel_event = threading.Event()
                with track_read_cache(), (track_write_scope(work_branch, cancel_event) if work_branch else nullcontext()):
                    branch_exists = state.sha_before is not None
                    repo_snapshot = await execution_context.prefetch_repo_snapshot(
                        data, work_branch, normalized_base_branch, has_repo_target, branch_exists, resume_outputs)
                    previous_plan = await execution_context.load_previous_plan(
                        data, has_repo_target, db_entry.user_id, conversation_id, db_entry.id, resume_outputs)
                    inputs = execution_context.crew_inputs(
                        data, conversation_id, final_prompt, conversation_context, work_branch,
                        normalized_base_branch, has_repo_target, branch_exists, repo_snapshot, previous_plan,
                    )
                    result = await run_crew(
                        crew, state, db_entry.id, data.target_workflow, inputs, resume_outputs, data.scope, cancel_event)
                raw_result = str(result.raw) if hasattr(result, 'raw') else str(result)

                # Un rapport « réussi » ne prouve rien sur GitHub (un outil github_* en échec renvoie du texte à
                # l'agent, jamais une exception). Vérifié seulement quand du code a été écrit (pas ANALYSE_ONLY).
                delivered_pr = None
                if should_verify_github_delivery:
                    delivered_pr = await execution_outcomes.verify_delivery(
                        data, work_branch, normalized_base_branch, state.sha_before, raw_result)
                raw_result = execution_outcomes.with_pull_request_line(raw_result, delivered_pr)
            except Exception as e:
                memory_monitor.log_memory(f"execution_id={db_entry.id}, exception attrapée")
                log.error("erreur CrewAI pendant l'exécution", exc_info=True)
                info = classify_exception(e)
                retry_outputs = await execution_outcomes.retry_outputs_if_transient(e, info, db_entry.id, data, auto_retry_allowed)
                if retry_outputs is not None:
                    return retry_outputs
                await execution_outcomes.persist_failure(
                    session, db_entry, conversation, e, info, data, work_branch, normalized_base_branch,
                    should_verify_github_delivery, state,
                )
            else:
                # Hors du try du crew : une erreur APRÈS le succès (connexion coupée par le pooler en pleine
                # validation) ne doit jamais faire passer en échec une exécution dont la PR est déjà ouverte
                # et vérifiée — elle proposerait un nouvel essai, donc du travail en double.
                await execution_outcomes.persist_success_safely(db_entry_id, conversation_id, session, db_entry, conversation, raw_result, state)
        return None
    except Exception as e:
        execution_outcomes.mark_startup_failure(db_entry_id, e)
        return None
