"""Exécutions « orphelines » : restées à `running` alors que plus aucun process ne les fait avancer
(crash, OOM, redéploiement pendant une exécution — voir la limite documentée dans main.py).

Sans ce balayage, une telle ligne bloquait la conversation pour toujours : 409 « déjà en cours » à
chaque nouveau message, et suppression refusée (409 aussi). Elle est désormais marquée `failed` /
`INTERRUPTED`, ce qui libère la conversation et propose « Réessayer ».
"""
from datetime import datetime, timedelta, timezone
from typing import Collection, List, Optional

from sqlmodel import Session, select

from database import ExecutionHistory
from errors import ErrorCode

# Une exécution vivante écrit updated_at toutes les HEARTBEAT_SECONDS (voir main._heartbeat, indépendant
# des étapes, donc aussi pendant une longue pause de quota) et à chaque changement d'étape : sans aucun
# signe de vie depuis ce délai, et sans tâche active dans CE process, elle est morte. Le délai vaut
# plusieurs battements : une instance voisine (déploiement en continu) ne voit jamais une exécution
# vivante comme morte.
HEARTBEAT_SECONDS = 60
ORPHAN_AFTER_SECONDS = 600

INTERRUPTED_MESSAGE = (
    "Exécution interrompue avant la fin (redémarrage ou arrêt du serveur). "
    "Réessayez : le travail déjà poussé sur la branche de travail est repris."
)


def _aware(value: datetime) -> datetime:
    # SQLite renvoie des datetimes naïfs (UTC) même pour une colonne écrite avec fuseau.
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def sweep_stale_executions(
    session: Session,
    *,
    active_ids: Collection[int] = (),
    conversation_id: Optional[int] = None,
    user_id: Optional[str] = None,
    ids: Optional[Collection[int]] = None,
    max_age_seconds: int = ORPHAN_AFTER_SECONDS,
    now: Optional[datetime] = None,
) -> List[int]:
    """Marque `failed` les exécutions `running` sans signe de vie. Renvoie leurs identifiants.

    `active_ids` : exécutions réellement en cours dans CE process (jamais touchées, quel que soit
    leur âge). `conversation_id` / `user_id` / `ids` restreignent le balayage (rien n'est balayé hors du
    périmètre demandé : pas de requête sur toute la table à chaque sondage de progression)."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=max_age_seconds)
    # Le filtre d'âge est posé dans la requête : seules les lignes réellement périmées sont chargées
    # (le sondage de progression appelle ceci toutes les quelques secondes).
    statement = select(ExecutionHistory).where(
        ExecutionHistory.status == "running", ExecutionHistory.updated_at < cutoff
    )
    if ids is not None:
        statement = statement.where(ExecutionHistory.id.in_(list(ids)))
    if conversation_id is not None:
        statement = statement.where(ExecutionHistory.conversation_id == conversation_id)
    if user_id is not None:
        statement = statement.where(ExecutionHistory.user_id == user_id)

    swept: List[int] = []
    for entry in session.exec(statement).all():
        if entry.id in active_ids or _aware(entry.updated_at) >= cutoff:
            continue
        # Garde le texte déjà produit (sections des agents terminés) : utile pour comprendre où ça
        # s'est arrêté ; le message d'interruption est ajouté à la suite.
        previous = (entry.result or "").strip()
        entry.result = f"{previous}\n\n{INTERRUPTED_MESSAGE}" if previous else INTERRUPTED_MESSAGE
        entry.status = "failed"
        entry.current_step = None
        entry.error_code = ErrorCode.INTERRUPTED
        entry.error_retryable = True
        entry.updated_at = now
        session.add(entry)
        swept.append(entry.id)
    if swept:
        session.commit()
    return swept
