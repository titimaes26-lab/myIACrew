import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

from database import ExecutionHistory  # noqa: E402
from errors import AppError, ErrorCode  # noqa: E402
from limits import SlidingWindowLimiter, check_qualify_rate, check_user_execution_quota  # noqa: E402

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _row(db, user="u1", status="running", conversation_id=None, created=NOW, updated=NOW):
    entry = ExecutionHistory(
        user_request="x", workflow="BUGFIX", status=status, user_id=user, conversation_id=conversation_id,
        created_at=created, updated_at=updated,
    )
    db.add(entry)
    db.commit()
    return entry


def test_running_limit_blocks_with_retryable_429(session):
    _row(session, conversation_id=1)
    _row(session, conversation_id=2)
    with pytest.raises(AppError) as err:
        check_user_execution_quota(session, "u1", now=NOW, max_running=2, max_per_hour=100)
    assert err.value.status_code == 429 and err.value.code == ErrorCode.RATE_LIMITED and err.value.retryable
    assert "2 exécutions en cours" in err.value.message


def test_other_users_and_finished_runs_do_not_count(session):
    _row(session, user="u2", conversation_id=1)
    _row(session, status="success", conversation_id=2)
    _row(session, status="failed", conversation_id=3)
    check_user_execution_quota(session, "u1", now=NOW, max_running=1, max_per_hour=100)


def test_stale_running_row_is_swept_instead_of_counted(session):
    old = NOW - timedelta(hours=3)
    _row(session, conversation_id=1, created=old, updated=old)
    check_user_execution_quota(session, "u1", now=NOW, max_running=1, max_per_hour=100)
    assert session.exec(select(ExecutionHistory)).first().status == "failed"


def test_hourly_limit_counts_recent_executions_only(session):
    for minutes in (5, 10, 15):
        _row(session, status="success", created=NOW - timedelta(minutes=minutes))
    with pytest.raises(AppError) as err:
        check_user_execution_quota(session, "u1", now=NOW, max_running=5, max_per_hour=3)
    assert "3 exécutions par heure" in err.value.message
    check_user_execution_quota(session, "u1", now=NOW, max_running=5, max_per_hour=4)


def test_hourly_limit_ignores_old_executions(session):
    for hours in (2, 3, 4):
        _row(session, status="success", created=NOW - timedelta(hours=hours))
    check_user_execution_quota(session, "u1", now=NOW, max_running=5, max_per_hour=3)


def test_no_user_no_check(session):
    check_user_execution_quota(session, None, now=NOW, max_running=1, max_per_hour=1)


def test_one_running_row_per_conversation_is_enforced_by_the_database(session):
    _row(session, conversation_id=7)
    with pytest.raises(IntegrityError):
        _row(session, conversation_id=7)
    session.rollback()
    _row(session, status="failed", conversation_id=7)  # seules les lignes « running » sont uniques
    _row(session, conversation_id=8)


def test_sliding_window_limiter():
    limiter = SlidingWindowLimiter(2, window_seconds=60)
    assert limiter.allow("a", now=0) and limiter.allow("a", now=10)
    assert not limiter.allow("a", now=20)
    assert limiter.allow("b", now=20)  # autre clé
    assert limiter.allow("a", now=61)  # la plus ancienne est sortie de la fenêtre


def test_qualify_rate_raises_429(monkeypatch):
    import limits
    monkeypatch.setattr(limits, "qualify_limiter", SlidingWindowLimiter(1))
    check_qualify_rate("u9")
    with pytest.raises(AppError) as err:
        check_qualify_rate("u9")
    assert err.value.status_code == 429 and err.value.code == ErrorCode.RATE_LIMITED
    check_qualify_rate(None)
