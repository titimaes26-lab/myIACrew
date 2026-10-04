"""Points d'accès de l'historique : lecture d'une exécution, liste paginée, suppression unitaire et par lot."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import defer
from sqlmodel import Session, col, select

import execution_state
from auth import get_current_user
from database import AgentRun, ExecutionCheckpoint, ExecutionHistory, get_session
from logs import get_logger
from orphans import sweep_stale_executions
from schemas import BulkDeleteInput, HistoryListEntry

log = get_logger("routes_history")

router = APIRouter()

@router.get("/api/executions/{execution_id}", response_model=ExecutionHistory)
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

@router.get("/api/history", response_model=List[HistoryListEntry])
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

@router.delete("/api/history/{execution_id}")
def delete_history_entry(
    execution_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Supprime une exécution de l'historique de l'utilisateur courant."""
    log.debug("suppression de l'exécution %s demandée", execution_id)
    # Une exécution orpheline (plus de signe de vie) devient « failed » donc supprimable.
    sweep_stale_executions(session, active_ids=execution_state.active_execution_ids, user_id=user.get("id"), ids=[execution_id])
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

@router.post("/api/history/bulk-delete")
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
        sweep_stale_executions(session, active_ids=execution_state.active_execution_ids, user_id=user.get("id"), ids=valid_ids)
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

BULK_DELETE_MAX = 100

_SQL_INT_MAX = 2**31 - 1
