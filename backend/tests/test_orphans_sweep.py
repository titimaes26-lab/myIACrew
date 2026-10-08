"""Balayage des exécutions orphelines : lesquelles, dans quelle portée, sans doublon sous concurrence."""
from datetime import timedelta


import pytest
from sqlmodel import Session

import routes_conversations
import routes_history
from database import Conversation
from orphans import INTERRUPTED_MESSAGE, sweep_stale_executions
from orphans_support import _running  # noqa: E402


def test_stale_running_execution_is_marked_interrupted_and_retryable(session):
    stale = _running(session, result="## Designer\nplan")
    assert sweep_stale_executions(session) == [stale.id]
    session.refresh(stale)
    assert (stale.status, stale.error_code, stale.error_retryable, stale.current_step) == ("failed", "INTERRUPTED", True, None)
    assert stale.result.startswith("## Designer\nplan") and stale.result.endswith(INTERRUPTED_MESSAGE)


def test_fresh_and_active_executions_are_never_swept(session):
    fresh = _running(session, age=timedelta(seconds=30))
    active = _running(session)
    assert sweep_stale_executions(session, active_ids={active.id}) == []
    session.refresh(fresh)
    session.refresh(active)
    assert fresh.status == active.status == "running"


def test_sweep_respects_its_scope(session):
    mine = _running(session, user="u1", conversation_id=1)
    other_conversation = _running(session, user="u1", conversation_id=2)
    other_user = _running(session, user="u2", conversation_id=3)
    assert sweep_stale_executions(session, conversation_id=1) == [mine.id]
    assert sweep_stale_executions(session, user_id="u1") == [other_conversation.id]
    session.refresh(other_user)
    assert other_user.status == "running"


def test_progress_poll_releases_a_dead_execution(session):
    conversation = Conversation(user_id="u1", title="t")
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    stale = _running(session, conversation_id=conversation.id)
    result = routes_conversations.get_conversation_progress(conversation_id=conversation.id, session=session, user={"id": "u1"})
    assert result["status"] is None  # plus rien en cours : le frontend resynchronise le tour
    session.refresh(stale)
    assert stale.status == "failed" and stale.error_code == "INTERRUPTED"


def test_stale_running_entry_becomes_deletable(session):
    stale = _running(session)
    result = routes_history.delete_history_entry(execution_id=stale.id, session=session, user={"id": "u1"})
    assert result["status"] == "deleted"
    live = _running(session, age=timedelta(seconds=5))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        routes_history.delete_history_entry(execution_id=live.id, session=session, user={"id": "u1"})
    assert excinfo.value.status_code == 409


def test_sweep_by_ids_leaves_other_stale_rows_alone(session):
    wanted, other = _running(session), _running(session)
    assert sweep_stale_executions(session, ids=[wanted.id]) == [wanted.id]
    session.refresh(other)
    assert other.status == "running"


def test_two_concurrent_sweeps_free_the_row_once_and_write_the_message_once(session):
    stale = _running(session, result="## Designer\nplan")
    engine = session.get_bind()

    class RacingSession(Session):
        """Un autre balayage libère la ligne entre la lecture de celui-ci et sa propre écriture."""
        raced = False

        def exec(self, statement, *args, **kwargs):
            if getattr(statement, "is_update", False) and not self.raced:
                RacingSession.raced = True
                with Session(engine) as other:
                    other.exec(statement)
                    other.commit()
            return super().exec(statement, *args, **kwargs)

    with RacingSession(engine) as racing:
        assert sweep_stale_executions(racing) == []   # la perdante ne revendique rien
    session.expire_all()
    session.refresh(stale)
    assert stale.status == "failed" and stale.result.count(INTERRUPTED_MESSAGE) == 1


def test_sweeping_twice_never_duplicates_the_interruption_message(session):
    stale = _running(session)
    assert sweep_stale_executions(session) == [stale.id]
    assert sweep_stale_executions(session) == []
    session.refresh(stale)
    assert stale.result.count(INTERRUPTED_MESSAGE) == 1


def test_a_sweep_that_reads_the_row_after_another_sweep_freed_it_does_not_append_again(session):
    # Scénario du doublon : la liste des orphelines est lue, un autre balayage libère la ligne (message ajouté), puis
    # celui-ci recharge la ligne — qui contient déjà le message. Sans UPDATE conditionnel, il l'ajouterait une 2e fois.
    stale = _running(session, result="## Designer\nplan")
    engine = session.get_bind()

    class LateReadSession(Session):
        reads = 0

        def exec(self, statement, *args, **kwargs):
            if not getattr(statement, "is_update", False):
                LateReadSession.reads += 1
                if LateReadSession.reads == 2:   # le rechargement des lignes, après la liste des orphelines
                    with Session(engine) as other:
                        assert sweep_stale_executions(other) == [stale.id]
            return super().exec(statement, *args, **kwargs)

    with LateReadSession(engine) as late:
        assert sweep_stale_executions(late) == []
    session.expire_all()
    session.refresh(stale)
    assert stale.result.count(INTERRUPTED_MESSAGE) == 1


def test_aware_keeps_its_own_offset_and_naive_dates_are_read_as_utc():
    from datetime import datetime, timedelta, timezone

    import orphans
    plus_two = timezone(timedelta(hours=2))
    aware = datetime(2026, 1, 1, 12, 0, tzinfo=plus_two)
    assert orphans._aware(aware) == aware and orphans._aware(aware).utcoffset() == timedelta(hours=2)
    assert orphans._aware(datetime(2026, 1, 1, 12, 0)).tzinfo == timezone.utc
