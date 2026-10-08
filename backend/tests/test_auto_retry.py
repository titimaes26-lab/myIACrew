"""Seconde tentative automatique : quelles erreurs, avec quelles étapes réutilisées, sans occuper de créneau pendant l'attente."""
import asyncio


import pytest
from sqlmodel import Session, select

import schemas
import execution
import execution_persistence
import execution_state
from database import Conversation, ExecutionCheckpoint, ExecutionHistory
from resume_support import DESIGNER, _step_error, _launch_with, _saved


def test_transient_failure_in_an_early_step_is_retried_once_reusing_saved_steps(engine, monkeypatch):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(dict(resume_outputs or {}))
        if len(calls) == 1:
            on_task_output_complete(DESIGNER, "conception", 1.0)
            raise _step_error(2, "Architecte Logiciel React / TypeScript")
        return type("R", (), {"raw": "résultat final"})()

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 2
    assert calls[0] == {}
    assert calls[1] == {"design": "conception"}  # l'étape réussie est reprise, pas rejouée
    assert saved.status == "success" and saved.error_code is None


def test_second_failure_is_final_and_never_retried_again(engine, monkeypatch):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        raise _step_error(1, DESIGNER)

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 2 and saved.status == "failed" and saved.error_code == "LLM_UNAVAILABLE"


@pytest.mark.parametrize("error", [
    _step_error(4, "Développeur Fullstack React / TypeScript"),        # développement : écrit sur GitHub
    _step_error(5, "QA Engineer / Automated Tester"),                  # QA : dépend du développement
    _step_error(5, "finalisation du résultat"),                        # après toutes les étapes
    RuntimeError("503 UNAVAILABLE après le crew (vérification GitHub)"),  # hors étape
])
def test_failures_from_development_on_are_never_retried_automatically(engine, monkeypatch, error):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        raise error

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 1 and saved.status == "failed"


def test_non_transient_failure_is_not_retried(engine, monkeypatch):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        raise _step_error(1, DESIGNER, "bug interne inattendu")

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 1 and saved.status == "failed" and saved.error_code == "INTERNAL_ERROR"


def test_the_wait_before_the_second_attempt_does_not_hold_an_execution_slot(engine, monkeypatch):
    seen = []
    original = execution_state.persist_current_step

    def spy(execution_id, step_key):
        if step_key == "queued":
            seen.append(execution_state.execution_semaphore._value)
        return original(execution_id, step_key)

    monkeypatch.setattr(execution_state, "persist_current_step", spy)
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        if len(calls) == 1:
            raise _step_error(1, DESIGNER)
        return type("R", (), {"raw": "ok"})()

    _launch_with(engine, monkeypatch, fake_run)
    assert len(calls) == 2
    # "queued" est écrit avant la 1re acquisition ET avant la 2de : à ces deux instants, aucun emplacement pris.
    assert seen == [execution_state.MAX_CONCURRENT_EXECUTIONS, execution_state.MAX_CONCURRENT_EXECUTIONS]


def test_checkpoints_are_deleted_when_the_execution_succeeds(engine, monkeypatch):
    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        on_task_output_complete(DESIGNER, "conception", 1.0)
        return type("R", (), {"raw": "ok"})()

    execution_id = _launch_with(engine, monkeypatch, fake_run)
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "success"
        assert not db.exec(select(ExecutionCheckpoint)).all()


def test_no_automatic_retry_when_a_finished_step_has_no_checkpoint(engine, monkeypatch):
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        # Échec à l'étape 3 alors qu'AUCUN point de reprise n'a été sauvegardé pour les étapes 1 et 2.
        raise _step_error(3, "Analyste Diagnostic Technique")

    saved = _saved(engine, _launch_with(engine, monkeypatch, fake_run))
    assert len(calls) == 1 and saved.status == "failed"


def test_checkpoint_purge_failure_never_fails_a_successful_execution(engine, monkeypatch):
    real_session = execution_persistence.Session
    state = {"purge": False}

    class BrokenOnPurge:
        def __init__(self, eng):
            if state["purge"]:
                raise RuntimeError("connexion périmée")
            self._inner = real_session(eng)

        def __enter__(self):
            return self._inner.__enter__()

        def __exit__(self, *args):
            return self._inner.__exit__(*args)

    original = execution_persistence.delete_checkpoints_for

    def purge(execution_id):
        state["purge"] = True
        try:
            original(execution_id)
        finally:
            state["purge"] = False

    monkeypatch.setattr(execution_persistence, "delete_checkpoints_for", purge)
    monkeypatch.setattr(execution_persistence, "Session", BrokenOnPurge)

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        return type("R", (), {"raw": "ok"})()

    execution_id = _launch_with(engine, monkeypatch, fake_run)
    with real_session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "success"


def test_cancellation_during_the_retry_wait_does_not_leave_the_row_running(engine, monkeypatch):
    monkeypatch.setattr(execution_state, "AUTO_RETRY_DELAY_S", 5)
    calls = []

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        calls.append(1)
        raise _step_error(1, DESIGNER)

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow="DESIGN_AND_DEV", status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="DESIGN_AND_DEV")

    async def scenario():
        task = asyncio.create_task(execution.execute_crew_and_persist(ids[0], ids[1], data, False, False, "", None, "p", "c"))
        await asyncio.sleep(0.5)  # 1re tentative échouée, on attend la 2de
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    asyncio.run(scenario())
    saved = _saved(engine, ids[0])
    assert len(calls) == 1 and saved.status == "failed" and "interrompue" in saved.result
