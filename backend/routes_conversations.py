"""Points d'accès des conversations : création, liste, messages, progression en direct et dépôts récemment ciblés."""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, col, func, select

import execution_context
import execution_state
from auth import get_current_user
from database import Conversation, ExecutionHistory, get_session
from logs import get_logger
from orphans import sweep_stale_executions
from schemas import ConversationCreateInput

log = get_logger("routes_conversations")

router = APIRouter()

@router.post("/api/conversations", response_model=Conversation)
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

@router.get("/api/conversations", response_model=List[Conversation])
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

# Une conversation longue renvoyait le résultat COMPLET de chaque tour à chaque resynchronisation (plusieurs Mo) :
# par défaut, les MESSAGES_DEFAULT_LIMIT derniers tours ; `before_id` remonte plus loin dans l'historique.
MESSAGES_DEFAULT_LIMIT = 100
MESSAGES_MAX_LIMIT = 500


@router.get("/api/conversations/{conversation_id}/messages", response_model=List[ExecutionHistory])
def get_conversation_messages(
    conversation_id: int,
    limit: int = MESSAGES_DEFAULT_LIMIT,
    before_id: Optional[int] = None,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Derniers messages (échanges qualify+execute) d'une conversation, dans l'ordre chronologique. `limit` (1 à
    MESSAGES_MAX_LIMIT) borne le nombre de tours ; `before_id` ne garde que les tours d'identifiant inférieur."""
    conversation = session.get(Conversation, conversation_id)
    if not conversation or conversation.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Conversation introuvable.")

    limit = min(max(limit, 1), MESSAGES_MAX_LIMIT)
    statement = select(ExecutionHistory).where(ExecutionHistory.conversation_id == conversation_id)
    if before_id is not None:
        statement = statement.where(ExecutionHistory.id < before_id)
    rows = session.exec(
        statement.order_by(col(ExecutionHistory.created_at).desc(), col(ExecutionHistory.id).desc()).limit(limit)
    ).all()
    return list(reversed(rows))


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
        if row_id != execution_id and (step not in (None, execution_state.QUEUED_STEP) or _aware_utc(created) < mine)
    )

def _aware_utc(value: datetime) -> datetime:
    # SQLite renvoie des datetimes naïfs (UTC) même pour une colonne écrite avec fuseau.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

@router.get("/api/conversations/{conversation_id}/progress")
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
    sweep_stale_executions(session, active_ids=execution_state.active_execution_ids, conversation_id=conversation_id)

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
        completed_agents = execution_context.parse_completed_agents(row[3])

    return {
        "id": row[0],
        "status": row[1],
        "current_step": row[2],
        "completed_agents": completed_agents,
        "queue_ahead": _queue_ahead(session, row[0], row[2], row[4]) if row[2] == execution_state.QUEUED_STEP else None,
    }

REPO_TARGETS_LIMIT = 20


@router.get("/api/repo-targets")
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
        .group_by(ExecutionHistory.repo_owner, ExecutionHistory.repo_name, ExecutionHistory.base_branch)
        .order_by(func.max(ExecutionHistory.created_at).desc())
        .limit(REPO_TARGETS_LIMIT)
    )
    return [
        {"repo_owner": repo_owner, "repo_name": repo_name, "base_branch": base_branch}
        for repo_owner, repo_name, base_branch in session.exec(statement).all()
    ]
