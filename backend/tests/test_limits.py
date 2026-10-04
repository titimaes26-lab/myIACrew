from datetime import datetime, timedelta, timezone


import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import select

from database import ExecutionHistory, ExecutionLaunch
from errors import AppError, ErrorCode
from limits import (
    MAX_RUNNING_PER_USER, SlidingWindowLimiter, check_qualify_rate, check_user_execution_quota, record_execution_launch,
)

NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)


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


def _launch(db, user="u1", minutes_ago=0.0):
    db.add(ExecutionLaunch(user_id=user, created_at=NOW - timedelta(minutes=minutes_ago)))
    db.commit()


def test_hourly_limit_counts_recent_launches_only(session):
    for minutes in (5, 10, 15):
        _launch(session, minutes_ago=minutes)
    _launch(session, user="u2", minutes_ago=1)
    with pytest.raises(AppError) as err:
        check_user_execution_quota(session, "u1", now=NOW, max_running=5, max_per_hour=3)
    assert "3 exécutions par heure" in err.value.message
    check_user_execution_quota(session, "u1", now=NOW, max_running=5, max_per_hour=4)
    check_user_execution_quota(session, "u3", now=NOW, max_running=5, max_per_hour=1)


def test_hourly_limit_ignores_old_launches(session):
    for hours in (2, 3, 4):
        _launch(session, minutes_ago=hours * 60)
    check_user_execution_quota(session, "u1", now=NOW, max_running=5, max_per_hour=3)


def test_deleting_history_does_not_reset_the_hourly_limit(session):
    for index in range(3):
        _row(session, status="success", conversation_id=index + 1, created=NOW - timedelta(minutes=index + 1))
        _launch(session, minutes_ago=index + 1)
    for entry in session.exec(select(ExecutionHistory)).all():
        session.delete(entry)
    session.commit()
    with pytest.raises(AppError):
        check_user_execution_quota(session, "u1", now=NOW, max_running=5, max_per_hour=3)


def test_record_launch_adds_a_line_and_purges_old_ones(session):
    _launch(session, minutes_ago=3 * 60)
    _launch(session, minutes_ago=30)
    record_execution_launch(session, "u1", now=NOW)
    ages = sorted((NOW - row.created_at.replace(tzinfo=timezone.utc)).total_seconds() / 60 for row in session.exec(select(ExecutionLaunch)).all())
    assert ages == [0, 30]
    record_execution_launch(session, None, now=NOW)
    assert len(session.exec(select(ExecutionLaunch)).all()) == 2


def test_one_concurrent_execution_per_user_by_default():
    assert MAX_RUNNING_PER_USER == 1


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
