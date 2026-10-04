"""Suivi d'une exécution : tâche enregistrée, battement de cœur, étape courante."""
import asyncio
from datetime import timedelta


from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import execution_state
import database
from database import ExecutionHistory
from orphans_support import _running  # noqa: E402


def test_step_change_is_a_heartbeat(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    with Session(engine) as db:
        entry = _running(db)
        before = entry.updated_at
        entry_id = entry.id
    execution_state.persist_current_step(entry_id, "design")
    with Session(engine) as db:
        assert db.get(ExecutionHistory, entry_id).updated_at.replace(tzinfo=None) > before.replace(tzinfo=None) + timedelta(seconds=60)


def test_tracked_task_registers_active_id_and_releases_everything_when_done():
    async def scenario():
        async def work():
            await asyncio.sleep(0.01)
        task = asyncio.create_task(work())
        execution_state.track_execution_task(task, 4242)
        assert 4242 in execution_state.active_execution_ids and task in execution_state.background_tasks
        await task
        await asyncio.sleep(0)
        return 4242 in execution_state.active_execution_ids, task in execution_state.background_tasks
    assert asyncio.run(scenario()) == (False, False)


def test_heartbeat_touches_only_running_rows(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    with Session(engine) as db:
        alive = _running(db)
        done = _running(db)
        done.status = "success"
        db.add(done)
        db.commit()
        ids = (alive.id, done.id)
        before = alive.updated_at.replace(tzinfo=None)
    execution_state.touch_execution(ids[0])
    execution_state.touch_execution(ids[1])
    with Session(engine) as db:
        assert db.get(ExecutionHistory, ids[0]).updated_at.replace(tzinfo=None) > before + timedelta(seconds=60)
        assert db.get(ExecutionHistory, ids[1]).updated_at.replace(tzinfo=None) <= before + timedelta(seconds=1)


def test_heartbeat_retries_quickly_after_a_failed_write(monkeypatch):
    calls = []

    def flaky(execution_id):
        calls.append(execution_id)
        return len(calls) > 1  # le premier battement échoue

    monkeypatch.setattr(execution_state, "touch_execution", flaky)
    monkeypatch.setattr(execution_state, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(execution_state, "HEARTBEAT_RETRY_SECONDS", 0.01)

    async def scenario():
        task = asyncio.create_task(execution_state.heartbeat_loop(7))
        await asyncio.sleep(0.15)
        task.cancel()

    asyncio.run(scenario())
    assert calls[:2] == [7, 7]  # échec puis nouvel essai sans attendre un battement entier


def test_touch_reports_failure_instead_of_raising(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(execution_state, "Session", broken)
    assert execution_state.touch_execution(1) is False
