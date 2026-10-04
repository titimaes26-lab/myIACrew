"""Créneau d'exécution : une à la fois, file d'attente, échéance maximale, arrêt d'un crew bloqué, message de délai."""
# ruff: noqa: F811  (les fixtures importées de resume_support sont reprises comme arguments des tests)
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

import schemas  # noqa: E402
import routes_conversations  # noqa: E402
import execution  # noqa: E402
import execution_context  # noqa: E402
import execution_persistence  # noqa: E402
import execution_state  # noqa: E402
from database import Conversation, ExecutionCheckpoint, ExecutionHistory  # noqa: E402
from resume_support import engine, DESIGNER, _launch_with, _saved, _new_failed_execution  # noqa: E402,F401,F811  (fixtures reprises par nom)


def test_one_execution_runs_at_a_time_by_default_and_the_others_queue(engine, monkeypatch):
    # Valeur fixée ici : le test ne dépend pas de MAX_CONCURRENT_EXECUTIONS dans l'environnement de CI ou de dev.
    monkeypatch.setattr(execution_state, "execution_semaphore", asyncio.Semaphore(1))
    running, peak, order = 0, 0, []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        order.append("start")
        await asyncio.sleep(0.05)
        order.append("end")
        running -= 1
        return type("R", (), {"raw": "ok"})()

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)
    steps = []
    original = execution_state.persist_current_step
    monkeypatch.setattr(execution_state, "persist_current_step", lambda execution_id, step: (steps.append(step), original(execution_id, step))[1])

    def new_execution():
        with Session(engine) as db:
            conversation = Conversation(user_id="u1", title="t")
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
            entry = ExecutionHistory(user_request="x", workflow="BUGFIX", status="running", user_id="u1", conversation_id=conversation.id)
            db.add(entry)
            db.commit()
            db.refresh(entry)
            return entry.id, conversation.id

    async def scenario():
        data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX")
        launches = [execution.execute_crew_and_persist(*new_execution(), data, False, False, "", None, "prompt", "ctx", None) for _ in range(3)]
        await asyncio.gather(*launches)

    asyncio.run(scenario())
    assert peak == 1                                   # jamais deux crews en même temps
    assert order == ["start", "end"] * 3               # les suivants attendent leur tour
    assert steps.count("queued") >= 3                  # chacun annonce « queued » avant d'attendre


def test_a_stuck_crew_is_stopped_at_the_deadline_marked_failed_and_the_slot_is_released(engine, monkeypatch):
    monkeypatch.setattr(execution_state, "EXECUTION_TIMEOUT_S", 0.05)
    monkeypatch.setattr(execution_state, "execution_semaphore", asyncio.Semaphore(1))
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        await asyncio.sleep(30)   # bloqué : seul le délai global peut le libérer

    execution_id = _launch_with(engine, monkeypatch, fake_run, request_type="BUGFIX")
    saved = _saved(engine, execution_id)
    assert len(calls) == 1                                    # pas de seconde tentative automatique
    assert saved.status == "failed" and saved.error_code == "EXECUTION_TIMEOUT" and saved.error_retryable is True
    assert "durée maximale" in saved.result
    assert execution_state.execution_semaphore._value == 1              # le créneau est rendu : la file peut avancer


def test_a_timeout_raised_by_the_crew_itself_is_not_taken_for_the_global_deadline(engine, monkeypatch):
    monkeypatch.setattr(execution_state, "EXECUTION_TIMEOUT_S", 600)

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        raise TimeoutError("délai d'un appel LLM")

    execution_id = _launch_with(engine, monkeypatch, fake_run, request_type="BUGFIX")
    assert _saved(engine, execution_id).error_code == "LLM_TIMEOUT"


def test_queue_position_counts_running_executions_and_older_queued_ones(engine):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
    with Session(engine) as db:
        def add(user, step, minutes):
            conversation = Conversation(user_id=user, title="t")
            db.add(conversation)
            db.commit()
            db.refresh(conversation)
            entry = ExecutionHistory(user_request="x", workflow="BUGFIX", status="running", user_id=user,
                                     conversation_id=conversation.id, current_step=step, created_at=base + timedelta(minutes=minutes))
            db.add(entry)
            db.commit()
            db.refresh(entry)
            return entry
        running = add("u1", "diagnostic", 0)           # en cours : devant tout le monde
        first = add("u2", "queued", 1)                 # première en attente
        second = add("u3", "queued", 2)                # seconde en attente
        later_running = add("u4", "development", 3)    # tourne (rare) : compte, créée après ou non
        add("u5", None, 4)                             # vient d'être lancée, pas encore « queued » : derrière
        assert routes_conversations._queue_ahead(db, first.id, "queued", first.created_at) == 2     # running + later_running
        assert routes_conversations._queue_ahead(db, second.id, "queued", second.created_at) == 3   # + first
        assert running.id != later_running.id
        progress = routes_conversations.get_conversation_progress(conversation_id=second.conversation_id, session=db, user={"id": "u3"})
        assert progress["queue_ahead"] == 3 and progress["current_step"] == "queued"
        done = routes_conversations.get_conversation_progress(conversation_id=running.conversation_id, session=db, user={"id": "u1"})
        assert done["queue_ahead"] is None                                          # seulement quand l'exécution attend


def _run_crew_with_deadline(monkeypatch, fake_run, cancel_event=None):
    from metrics_collect import current_metrics  # noqa: F401
    monkeypatch.setattr(execution_state, "EXECUTION_TIMEOUT_S", 0.05)
    monkeypatch.setattr(execution_state, "abandoned_execution_ids", set())

    class FakeCrew:
        run_dynamic_crew = fake_run
    state = execution_context.RunState()
    with pytest.raises(execution.ExecutionTimeoutError) as err:
        asyncio.run(execution.run_crew(FakeCrew(), state, 4242, "BUGFIX", {}, None, None, cancel_event))
    return err.value


def test_a_stopped_execution_signals_its_thread_and_ignores_its_later_persistence(engine, monkeypatch):
    import threading

    async def stuck(self, **kwargs):
        await asyncio.sleep(30)

    event = threading.Event()
    error = _run_crew_with_deadline(monkeypatch, stuck, event)
    assert event.is_set() and 4242 in execution_state.abandoned_execution_ids and error.quota_wait_seconds == 0

    # le thread survivant appelle encore ces callbacks : rien ne doit être écrit sur la ligne déjà en échec
    execution_id = _new_failed_execution(engine, "Échec : message d'échec")
    execution_state.abandoned_execution_ids.add(execution_id)
    execution_persistence.persist_completed_agent(execution_id, DESIGNER, "texte tardif", 1.0)
    execution_state.persist_current_step(execution_id, "qa")
    saved = _saved(engine, execution_id)
    assert saved.result == "Échec : message d'échec" and saved.current_step is None
    with Session(engine) as db:
        assert db.exec(select(ExecutionCheckpoint).where(ExecutionCheckpoint.execution_id == execution_id)).all() == []


def test_the_timeout_message_blames_the_quota_when_waiting_dominated(monkeypatch):
    from metrics_collect import current_metrics
    from errors import ErrorCode, classify_exception

    async def quota_bound(self, **kwargs):
        current_metrics.get().record_wait(600)      # 10 min d'attente du quota
        await asyncio.sleep(30)

    error = _run_crew_with_deadline(monkeypatch, quota_bound)
    assert error.quota_dominant and "10.0 min d'attente du quota" in str(error)
    info = classify_exception(error)
    assert info.code == ErrorCode.EXECUTION_TIMEOUT and info.retryable and "quota" in info.message

    async def slow(self, **kwargs):
        await asyncio.sleep(30)
    plain = classify_exception(_run_crew_with_deadline(monkeypatch, slow))
    assert plain.code == ErrorCode.EXECUTION_TIMEOUT and "simplifiez" in plain.message
