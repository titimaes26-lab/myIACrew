"""Issue d'une exécution : vérification de la livraison GitHub, enregistrement du succès ou de l'échec (avec le travail déjà
accompli), seconde tentative automatique et filet de démarrage."""
import asyncio
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Session

import database
import execution_context
import execution_resume
import execution_persistence
import execution_state
from crew_workflow import CrewStepError, resumable_prefix, workflow_step_keys
from database import Conversation, ExecutionHistory
from delivery import render_partial_delivery_block
from errors import DeliveryError, ErrorCode, ErrorInfo
from github_delivery import DeliveredPullRequest, DeliveryIssue, describe_partial_delivery, verify_github_delivery
from logs import get_logger
from qa_report import final_verdict
from schemas import WorkflowExecutionInput
from summary import partial_work_block

log = get_logger("execution_outcomes")

async def partial_delivery_block(owner: str, repo: str, branch: str, base_branch: str, sha_before) -> str:
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

# Rapport de l'agent joint à un échec de livraison ; le détail technique stocké garde en plus la place du constat
# (sans cette marge, la fin du rapport serait coupée, c'est précisément la cause qui disparaîtrait).
AGENT_REPORT_CHARS = 3000

TECHNICAL_DETAIL_CHARS = AGENT_REPORT_CHARS + 1500

def delivery_failure_message(issue: DeliveryIssue, raw_result: str) -> str:
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
        f"{raw_result[:AGENT_REPORT_CHARS]}"
    )

async def verify_delivery(
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
        raise DeliveryError(delivery_failure_message(issue, raw_result))
    return delivered_pr

def with_pull_request_line(raw_result: str, delivered_pr: Optional[DeliveredPullRequest]) -> str:
    """Ajoute l'URL de la PR réellement observée à la SUITE du résultat, sans nouveau séparateur de section :
    le frontend ne découpe que sur « \\n\\n---\\n\\n## », donc la ligne reste dans la dernière section."""
    if delivered_pr is None:
        return raw_result
    state_label = "fusionnée" if delivered_pr.merged else "ouverte"
    return f"{raw_result}\n\n**Pull Request {state_label} :** {delivered_pr.html_url}"

def record_run_metrics(session: Session, db_entry: ExecutionHistory, state: execution_context.RunState) -> None:
    if state.metrics is None:
        return
    db_entry.api_calls_count = state.metrics.api_calls_count
    db_entry.rate_limit_hits = state.metrics.rate_limit_hits
    db_entry.total_wait_time_seconds = state.metrics.total_wait_time
    execution_persistence.persist_agent_runs(session, db_entry, state.metrics)

def commit_outcome(session: Session, db_entry: ExecutionHistory, conversation: Conversation) -> None:
    now = datetime.now(timezone.utc)
    db_entry.updated_at = now
    conversation.updated_at = now
    session.add(db_entry)
    session.add(conversation)
    session.commit()

async def persist_success(
    session: Session, db_entry: ExecutionHistory, conversation: Conversation, raw_result: str, state: execution_context.RunState,
) -> None:
    execution_state.safe_refresh(session, db_entry, "succès")
    db_entry.result = raw_result
    db_entry.status = "success"
    db_entry.qa_verdict = final_verdict(raw_result)
    db_entry.current_step = None
    record_run_metrics(session, db_entry, state)
    commit_outcome(session, db_entry, conversation)
    log.info(f"execution_id={db_entry.id} : terminée avec succès (tâche de fond).")
    # Après le commit du succès, dans sa propre Session et au mieux : purger une ressource optionnelle ne
    # doit jamais faire échouer (ni être validée par) le chemin d'une exécution réussie.
    await asyncio.to_thread(execution_persistence.delete_checkpoints_for, db_entry.id)
    execution_persistence.cleanup_persisted_agents(db_entry.id)

async def persist_success_safely(
    db_entry_id: int, conversation_id: int, session: Session, db_entry: ExecutionHistory,
    conversation: Conversation, raw_result: str, state: execution_context.RunState,
) -> None:
    """persist_success, avec UNE reprise sur une Session neuve si la première tentative échoue (connexion
    périmée après une longue exécution). Si la reprise échoue aussi, l'erreur est journalisée et la ligne
    reste « running » : le balayage des orphelines la libérera, plutôt que de la déclarer en échec à tort."""
    try:
        await persist_success(session, db_entry, conversation, raw_result, state)
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
            await persist_success(fresh, fresh_entry, fresh_conversation, raw_result, state)
    except Exception as second_error:
        log.error(f"succès non enregistré (execution_id={db_entry_id}) : {type(second_error).__name__}: "
              f"{second_error}")
        execution_persistence.cleanup_persisted_agents(db_entry_id)

def failure_detail(exc: BaseException, info: ErrorInfo) -> str:
    """Message d'échec : cause lisible (sinon texte d'origine) suivie du détail technique tronqué — sans lui,
    ni l'utilisateur ni la base ne diraient POURQUOI (ce que reproche un garde-fou, délai « retry after »)."""
    technical = str(exc).strip()[:TECHNICAL_DETAIL_CHARS]
    reason = f"{info.message} (détail : {technical})" if info.message and technical else (info.message or technical)
    if isinstance(exc, CrewStepError):
        return f"Échec à l'étape {exc.step_index}/{exc.total_steps} ({exc.agent_role}) : {reason}"
    return reason

async def persist_failure(
    session: Session, db_entry: ExecutionHistory, conversation: Conversation, exc: BaseException, info: ErrorInfo,
    data: "WorkflowExecutionInput", work_branch: str, base_branch: Optional[str], should_verify: bool,
    state: execution_context.RunState,
) -> None:
    detail = failure_detail(exc, info)
    # Écritures GitHub partielles : l'échec peut venir après qu'une branche, des commits ou une PR ont DÉJÀ
    # été créés. Constaté via l'API (jamais d'après un agent) pour que l'utilisateur sache quoi reprendre.
    if should_verify and data.repo_owner and data.repo_name and work_branch:
        detail += "\n\n" + await partial_delivery_block(
            data.repo_owner, data.repo_name, work_branch, base_branch or "main", state.sha_before,
        )
    execution_state.safe_refresh(session, db_entry, "échec")
    # Travail déjà accompli : les sections des agents terminés sont dans `result` jusqu'à ce qu'il soit écrasé
    # ci-dessous. Ajouté APRÈS le bloc GitHub : le frontend le retire en premier (splitPartialWork).
    completed = [(name, text) for name, text in execution_context.parse_completed_agents(db_entry.result or "").items()]
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
    record_run_metrics(session, db_entry, state)
    commit_outcome(session, db_entry, conversation)
    execution_persistence.cleanup_persisted_agents(db_entry.id)

async def retry_outputs_if_transient(
    exc: BaseException, info: ErrorInfo, db_entry_id: int, data: "WorkflowExecutionInput", auto_retry_allowed: bool,
) -> Optional[dict[str, str]]:
    """Sorties à réutiliser pour la seconde tentative AUTOMATIQUE (une seule), ou None (échec définitif).
    Seulement après un échec transitoire survenu AVANT l'écriture du code : rien n'a été poussé sur GitHub et
    les étapes réussies sont reprises. Plus tard (développement, QA, vérification), l'utilisateur relance :
    il faudrait réécrire sur GitHub et repayer ces étapes. Sans le point de reprise de CHAQUE étape déjà
    réussie, relancer les repayerait pour rien. L'attente se fait chez l'appelant, hors sémaphore."""
    # isinstance (en plus de execution_resume.failed_before_development) : `exc.step_index` ci-dessous ne dépend ainsi pas
    # d'un couplage implicite entre ces deux conditions.
    if not isinstance(exc, CrewStepError):
        return None
    if not (auto_retry_allowed and info.retryable and execution_resume.failed_before_development(exc, data.target_workflow, data.scope)):
        return None
    saved = await asyncio.to_thread(execution_persistence.load_checkpoints_for, db_entry_id)
    prefix = resumable_prefix(workflow_step_keys(data.target_workflow, data.scope), saved)
    if len(prefix) < exc.step_index - 1:
        return None
    log.info(f"execution_id={db_entry_id} : échec transitoire ({info.code}), nouvelle tentative "
        f"automatique dans {execution_state.AUTO_RETRY_DELAY_S}s.")
    return {key: saved[key] for key in prefix}

def mark_startup_failure(db_entry_id: int, exc: BaseException) -> None:
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
        execution_persistence.cleanup_persisted_agents(db_entry_id)
