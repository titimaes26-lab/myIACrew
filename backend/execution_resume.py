"""Reprise d'une exécution en échec : l'échec est-il survenu avant le développement, quelles sorties d'agents réutiliser."""
from typing import Optional

from sqlmodel import Session

import execution_persistence
from crewquestion import CrewStepError, FINALIZATION_ROLE, RESUMABLE_STEPS, resumable_prefix, workflow_step_keys
from database import ExecutionHistory
from logs import get_logger
from schemas import WorkflowExecutionInput

log = get_logger("execution_resume")

def failed_before_development(exc: BaseException, request_type: str, scope: Optional[str] = None) -> bool:
    """Vrai si l'échec est celui d'une étape reprenable (design, architecture, diagnostic) : seul cas où
    une relance automatique ne réécrit rien sur GitHub. Faux pour tout échec hors étape (création du crew,
    vérification de livraison) ou à partir du développement."""
    if not isinstance(exc, CrewStepError):
        return False
    keys = workflow_step_keys(request_type, scope)
    return 1 <= exc.step_index <= len(keys) and keys[exc.step_index - 1] in RESUMABLE_STEPS and exc.agent_role != FINALIZATION_ROLE

def resumable_outputs(session: Session, data: "WorkflowExecutionInput", user_id, conversation_id: int) -> dict[str, str]:
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
    saved = execution_persistence.load_checkpoints(session, previous.id)
    prefix = resumable_prefix(workflow_step_keys(data.target_workflow, data.scope), saved)
    return {key: saved[key] for key in prefix}
