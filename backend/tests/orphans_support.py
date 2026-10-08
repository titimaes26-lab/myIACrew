"""Fabriques communes aux tests des exécutions orphelines : âge de référence, exécution « running » à balayer."""
import itertools
from datetime import datetime, timedelta, timezone



from database import ExecutionHistory
from orphans import ORPHAN_AFTER_SECONDS


OLD = timedelta(seconds=ORPHAN_AFTER_SECONDS + 60)


_conversation_ids = itertools.count(100)


def _running(db, age=OLD, user="u1", conversation_id=None, result=None):
    # Une seule exécution « running » par conversation (index unique partiel) : un identifiant distinct par défaut.
    if conversation_id is None:
        conversation_id = next(_conversation_ids)
    stamp = datetime.now(timezone.utc) - age
    entry = ExecutionHistory(
        user_request="r", workflow="BUGFIX", status="running", user_id=user, conversation_id=conversation_id,
        current_step="qa", result=result, created_at=stamp, updated_at=stamp,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
