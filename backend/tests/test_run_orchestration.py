"""Orchestration d'une exécution : persistance du succès, mesures, démarrage en échec, portée d'écriture GitHub."""
"""Étapes extraites de _run_crew_and_persist : fonctions pures ou presque, testées une à une."""
import asyncio
from types import SimpleNamespace


import pytest
import github_guards

import routes_execute
import execution
import execution_outcomes
import execution_context
import execution_persistence
import memory_monitor
from sqlmodel import Session, select
from database import Conversation, ExecutionCheckpoint, ExecutionHistory
from run_support import no_network_snapshot  # noqa: E402,F401  (fixture automatique : jamais de réseau pour l'aperçu du dépôt)
from run_support import _data, _new_execution, _fake_crew


def test_success_persistence_error_never_turns_a_delivered_run_into_a_failure(engine, monkeypatch):
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)
    real = execution_outcomes.persist_success
    calls = []

    async def flaky(session, db_entry, conversation, raw_result, state):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("connexion coupée par le pooler")
        await real(session, db_entry, conversation, raw_result, state)

    monkeypatch.setattr(execution_outcomes, "persist_success", flaky)
    execution_id, conversation_id = _new_execution(engine)
    asyncio.run(execution.run_crew_and_persist(execution_id, conversation_id, _data(repo_owner=None, repo_name=None), False, False, "", None, "p", "c"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, execution_id)
    assert len(calls) == 2  # une reprise sur Session neuve
    assert saved.status == "success" and saved.error_code is None


def test_when_success_persistence_fails_twice_the_row_is_not_declared_failed(engine, monkeypatch, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    seen: list[dict] = []
    _fake_crew(monkeypatch, None, seen)

    async def broken(*args, **kwargs):
        raise RuntimeError("base injoignable")

    monkeypatch.setattr(execution_outcomes, "persist_success", broken)
    execution_id, conversation_id = _new_execution(engine)
    asyncio.run(execution.run_crew_and_persist(execution_id, conversation_id, _data(repo_owner=None, repo_name=None), False, False, "", None, "p", "c"))
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).status == "running"  # libérée plus tard par le balayage
    assert "succès non enregistré" in caplog.text
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_persist_success_records_result_metrics_and_clears_the_step(engine, monkeypatch):
    execution_id, conversation_id = _new_execution(engine)
    with Session(engine) as db:
        db.add(ExecutionCheckpoint(execution_id=execution_id, step="design", raw="d"))
        db.commit()
        entry, conversation = db.get(ExecutionHistory, execution_id), db.get(Conversation, conversation_id)
        entry.current_step = "qa"
        state = execution_context.RunState(metrics=SimpleNamespace(api_calls_count=7, rate_limit_hits=1, total_wait_time=3.5))
        monkeypatch.setattr(execution_persistence, "persist_agent_runs", lambda *a: None)
        asyncio.run(execution_outcomes.persist_success(db, entry, conversation, "texte final", state))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, execution_id)
        assert (saved.status, saved.result, saved.current_step) == ("success", "texte final", None)
        assert (saved.api_calls_count, saved.rate_limit_hits, saved.total_wait_time_seconds) == (7, 1, 3.5)
        assert not db.exec(select(ExecutionCheckpoint)).all()  # points de reprise purgés après le succès


def test_run_crew_keeps_the_metrics_and_stops_the_memory_ticker_when_the_crew_crashes(monkeypatch):
    state = execution_context.RunState()
    ticker = {"cancelled": False}

    async def endless_ticker(label):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            ticker["cancelled"] = True
            raise

    async def crash(inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        raise RuntimeError("le crew plante en route")

    monkeypatch.setattr(memory_monitor, "periodic_memory_logger", endless_ticker)
    crew = SimpleNamespace(run_dynamic_crew=crash)
    with pytest.raises(RuntimeError, match="plante en route"):
        asyncio.run(execution.run_crew(crew, state, 1, "BUGFIX", {}, None))
    assert state.metrics is not None  # le chemin d'échec peut encore lire les métriques de la tentative
    assert ticker["cancelled"] is True


def test_mark_startup_failure_only_touches_a_running_row(engine):
    execution_id, _ = _new_execution(engine)
    execution_outcomes.mark_startup_failure(execution_id, RuntimeError("pool épuisé"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, execution_id)
        assert (saved.status, saved.error_code, saved.error_retryable) == ("failed", "INTERNAL_ERROR", False)
        assert "pool épuisé" in saved.result and saved.current_step is None
        saved.status, saved.result = "success", "déjà livré"
        db.add(saved)
        db.commit()
    execution_outcomes.mark_startup_failure(execution_id, RuntimeError("autre"))
    with Session(engine) as db:
        assert db.get(ExecutionHistory, execution_id).result == "déjà livré"  # jamais écrasée


def test_the_crew_runs_with_its_write_scope_limited_to_the_work_branch(engine, monkeypatch):
    seen_scopes: list = []

    async def run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None, **kwargs):
        seen_scopes.append(github_guards._write_scope.get())
        return SimpleNamespace(raw="résultat")

    monkeypatch.setattr(execution, "AppDevelopmentCrew", type("C", (), {"run_dynamic_crew": run}))
    monkeypatch.setattr(execution_context, "get_branch_head_sha", lambda *a: None)
    execution_id, conversation_id = _new_execution(engine, "FEATURE")
    asyncio.run(execution.run_crew_and_persist(execution_id, conversation_id, _data(target_workflow="FEATURE"), True, False, "crewai/feature-ab12cd34", "main", "p", "c"))
    assert seen_scopes == ["crewai/feature-ab12cd34"]
    assert github_guards._write_scope.get() is None   # jamais conservé après l'exécution


def test_work_branches_are_named_with_the_shared_prefix():
    assert routes_execute.WORK_BRANCH_PREFIX == github_guards.WORK_BRANCH_PREFIX == "crewai/"
