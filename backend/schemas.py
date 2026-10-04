"""Modèles d'entrée de l'API (corps de requête validés) et ligne de la liste de l'historique."""
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, field_validator, model_validator
from sqlmodel import SQLModel

import validation

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
    # Taille d'une FEATURE donnée par la qualification : PETIT saute l'étape d'architecture (voir
    # crewquestion.workflow_step_keys). Ignoré, sans erreur, pour tout autre workflow ; absent = parcours complet.
    scope: Optional[Literal["PETIT", "GRAND"]] = None

    @model_validator(mode="after")
    def _scope_only_for_feature(self):
        if self.target_workflow != "FEATURE":
            self.scope = None
        return self

    # Refus immédiat (422, un message par champ) plutôt qu'une erreur découverte en pleine exécution.
    _check_request = field_validator("user_request")(validation.validate_user_request)
    _check_workflow = field_validator("target_workflow")(validation.validate_workflow)
    _check_clarifications = field_validator("clarifications")(validation.validate_clarifications)
    _check_owner = field_validator("repo_owner")(validation.validate_repo_owner)
    _check_repo = field_validator("repo_name")(validation.validate_repo_name)
    _check_branch = field_validator("base_branch")(validation.validate_branch_name)

class ConversationCreateInput(BaseModel):
    title: Optional[str] = None

class BulkDeleteInput(BaseModel):
    ids: List[int]

class HistoryListEntry(SQLModel):
    """Une ligne de la liste de l'historique : de quoi l'afficher et la reprendre, SANS le résultat de l'exécution
    (texte souvent volumineux : une liste de 100 lignes en pèserait des centaines de Ko). Le résultat d'une exécution
    s'obtient à la demande (GET /api/executions/{id}) ou avec sa conversation (/api/conversations/{id}/messages)."""
    id: int
    user_request: str
    workflow: str
    status: str
    conversation_id: Optional[int] = None
    repo_owner: Optional[str] = None
    repo_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    current_step: Optional[str] = None
    error_code: Optional[str] = None
    error_retryable: Optional[bool] = None
    api_calls_count: Optional[int] = None
    rate_limit_hits: Optional[int] = None
    total_wait_time_seconds: Optional[float] = None
    scope: Optional[str] = None
    attempts: Optional[int] = None
    reused_steps: Optional[int] = None
    qa_verdict: Optional[str] = None
