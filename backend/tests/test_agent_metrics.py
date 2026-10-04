import asyncio
import os
import sys
import threading
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import main  # noqa: E402
import execution  # noqa: E402
import execution_persistence  # noqa: E402
import database  # noqa: E402
import routes_metrics  # noqa: E402
from agent_metrics import (  # noqa: E402
    ExecutionMetrics,
    _current_metrics,
    build_agent_run_rows,
    flush_events,
    parse_usage,
    percentile,
    step_for_role,
    summarize,
    track_execution_metrics,
)
from database import AgentRun, ExecutionHistory  # noqa: E402

DESIGNER = "Lead Product / Game Designer"


@pytest.mark.parametrize("usage, expected", [
    ({"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}, (10, 5, 15)),
    ({"input_tokens": 7, "output_tokens": 3}, (7, 3, 10)),
    ({"prompt_token_count": 4, "candidates_token_count": 6, "total_token_count": 11}, (4, 6, 11)),
    ({"total_tokens": 9}, (0, 0, 9)),
    ({"prompt_tokens": 0, "completion_tokens": 0}, None), ({"total_tokens": 0}, None),
    ({"prompt_token_count": 12, "candidates_token_count": 3, "completion_tokens": 8, "total_token_count": 20, "total_tokens": 20, "reasoning_tokens": 5}, (12, 8, 20)),
    ({}, None), (None, None), ("texte", None), ({"prompt_tokens": "x"}, None), ({"prompt_tokens": True}, None),
])
def test_parse_usage_accepts_provider_variants_and_distinguishes_unknown_from_zero(usage, expected):
    assert parse_usage(usage) == expected


def test_parse_usage_reads_pydantic_like_objects():
    class Usage:
        def model_dump(self):
            return {"prompt_tokens": 2, "completion_tokens": 3}
    assert parse_usage(Usage()) == (2, 3, 5)


def test_roles_from_the_yaml_map_to_pipeline_steps():
    assert step_for_role(DESIGNER) == "design"
    assert step_for_role("  Lead Product / Game Designer \n") == "design"
    assert step_for_role("Architecte Logiciel React / TypeScript") == "architecture"
    assert step_for_role("Analyste Diagnostic Technique") == "diagnostic"
    assert step_for_role("Développeur Fullstack React / TypeScript") == "development"
    assert step_for_role("QA Engineer / Automated Tester") == "qa"
    assert step_for_role(None) == "system" and step_for_role("  ") == "system"
    assert step_for_role("Rôle inconnu") == "other"


def test_metrics_count_real_llm_calls_tokens_tools_and_durations():
    metrics = ExecutionMetrics()
    metrics.record_attempt()
    metrics.record_llm_call(DESIGNER, {"prompt_tokens": 100, "completion_tokens": 20})
    metrics.record_llm_call(DESIGNER, None)  # usage inconnu : compté comme appel, pas comme tokens
    metrics.record_llm_call(None, {"total_tokens": 5})
    metrics.record_llm_error(DESIGNER)
    metrics.record_tool(DESIGNER, True)
    metrics.record_tool(DESIGNER, False)
    metrics.record_agent_done(DESIGNER, 12.5)
    assert metrics.attempts == 1 and metrics.api_calls_count == 3
    design = metrics.agent_snapshot()["design"]
    assert (design.llm_calls, design.usage_calls, design.total_tokens) == (2, 1, 120)
    assert (design.llm_errors, design.tool_calls, design.tool_errors, design.duration_seconds) == (1, 2, 1, 12.5)
    assert metrics.agent_snapshot()["system"].total_tokens == 5


def test_snapshot_is_a_copy():
    metrics = ExecutionMetrics()
    metrics.record_llm_call(DESIGNER)
    snap = metrics.agent_snapshot()
    snap["design"].llm_calls = 99
    assert metrics.agent_snapshot()["design"].llm_calls == 1


# --- Événements CrewAI réels sur le bus -------------------------------------------------------

def _llm_event(role=DESIGNER, usage=None, call_id="c1"):
    from crewai.events.types.llm_events import LLMCallCompletedEvent
    return LLMCallCompletedEvent(call_id=call_id, response="ok", call_type="llm_call", agent_role=role, usage=usage)


def test_bus_events_are_counted_for_the_tracked_execution_only():
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent
    now = datetime.now()
    with track_execution_metrics() as metrics:
        crewai_event_bus.emit(None, _llm_event(usage={"prompt_tokens": 40, "completion_tokens": 10}))
        crewai_event_bus.emit(None, _llm_event(role="QA Engineer / Automated Tester", call_id="c2"))
        crewai_event_bus.emit(None, ToolUsageFinishedEvent(
            tool_name="t", tool_args={}, started_at=now, finished_at=now, output="x", agent_role=DESIGNER))
        crewai_event_bus.emit(None, ToolUsageErrorEvent(tool_name="t", tool_args={}, error="boom", agent_role=DESIGNER))
        flush_events()
    snap = metrics.agent_snapshot()
    assert snap["design"].llm_calls == 1 and snap["design"].total_tokens == 50
    assert snap["design"].tool_calls == 2 and snap["design"].tool_errors == 1
    assert snap["qa"].llm_calls == 1
    # Hors exécution suivie : ignoré, sans erreur.
    crewai_event_bus.emit(None, _llm_event(call_id="c3"))
    flush_events()
    assert metrics.api_calls_count == 2


def test_concurrent_executions_do_not_mix_their_events():
    from crewai.events.event_bus import crewai_event_bus
    results = {}

    def run(name, calls):
        with track_execution_metrics() as metrics:
            for i in range(calls):
                crewai_event_bus.emit(None, _llm_event(call_id=f"{name}{i}"))
            flush_events()
            results[name] = metrics.api_calls_count

    threads = [threading.Thread(target=run, args=("a", 3)), threading.Thread(target=run, args=("b", 5))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == {"a": 3, "b": 5}


def test_failed_llm_calls_are_counted_as_errors_not_as_calls():
    from crewai.events.event_bus import crewai_event_bus
    from crewai.events.types.llm_events import LLMCallFailedEvent
    with track_execution_metrics() as metrics:
        crewai_event_bus.emit(None, LLMCallFailedEvent(call_id="f1", error="429", agent_role=DESIGNER))
        flush_events()
    assert metrics.api_calls_count == 0 and metrics.agent_snapshot()["design"].llm_errors == 1


# --- Lignes persistées et agrégats ------------------------------------------------------------

def test_agent_run_rows_follow_the_pipeline_and_mark_incomplete_agents():
    metrics = ExecutionMetrics()
    metrics.record_llm_call("QA Engineer / Automated Tester", {"total_tokens": 9})
    metrics.record_llm_call(DESIGNER, {"prompt_tokens": 5, "completion_tokens": 5})
    metrics.record_agent_done(DESIGNER, 3.0)
    metrics.record_llm_call(None)
    rows = build_agent_run_rows(metrics, execution_id=7, conversation_id=2, user_id="u", workflow="BUGFIX")
    assert [(r["agent"], r["status"]) for r in rows] == [("design", "completed"), ("qa", "incomplete"), ("system", "n/a")]
    assert rows[0]["execution_id"] == 7 and rows[0]["duration_seconds"] == 3.0 and rows[1]["duration_seconds"] is None
    assert build_agent_run_rows(ExecutionMetrics(), execution_id=1, conversation_id=None, user_id=None, workflow="x") == []


def test_percentile_interpolates_and_handles_empty():
    assert percentile([], 0.5) is None
    assert percentile([10], 0.95) == 10
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([0, 100], 0.95) == pytest.approx(95)


def _run(execution_id, agent, duration, calls=2, tokens=100, usage_calls=2, status="completed"):
    return {"execution_id": execution_id, "agent": agent, "status": status, "duration_seconds": duration,
            "llm_calls": calls, "llm_errors": 0, "usage_calls": usage_calls, "prompt_tokens": tokens * 3 // 4,
            "completion_tokens": tokens // 4, "total_tokens": tokens, "tool_calls": 1, "tool_errors": 0}


def test_summarize_computes_percentiles_averages_and_daily_series():
    base = datetime(2026, 10, 1, 10, 0, 0)
    executions = [
        {"id": 1, "status": "success", "created_at": base, "updated_at": base + timedelta(seconds=60),
         "rate_limit_hits": 1, "total_wait_time_seconds": 15.0},
        {"id": 2, "status": "failed", "created_at": base + timedelta(days=1), "updated_at": base + timedelta(days=1, seconds=100),
         "rate_limit_hits": None, "total_wait_time_seconds": None},
    ]
    runs = [_run(1, "design", 10), _run(1, "qa", 20), _run(2, "design", 30, status="incomplete", usage_calls=0, tokens=0)]
    result = summarize(runs, executions, 30, "BUGFIX")
    assert result["executions"]["total"] == 2 and result["executions"]["success"] == 1 and result["executions"]["failed"] == 1
    assert result["executions"]["median_duration_seconds"] == 80.0
    assert result["executions"]["rate_limit_hits"] == 1 and result["executions"]["wait_seconds"] == 15.0
    assert result["executions"]["avg_llm_calls"] == 3.0 and result["executions"]["token_executions"] == 1
    design = next(a for a in result["agents"] if a["agent"] == "design")
    assert design["runs"] == 2 and design["incomplete"] == 1 and design["duration_p50"] == 20.0
    assert design["token_runs"] == 1 and design["avg_prompt_tokens"] == 75.0
    assert [a["agent"] for a in result["agents"]] == ["design", "qa"]
    assert result["daily"] == [
        {"date": "2026-10-01", "executions": 1, "failed": 0, "llm_calls": 4, "tokens": 200, "qa_total": 0, "qa_go": 0, "median_duration_seconds": 60.0},
        {"date": "2026-10-02", "executions": 1, "failed": 1, "llm_calls": 2, "tokens": 0, "qa_total": 0, "qa_go": 0, "median_duration_seconds": 100.0},
    ]
    assert result["workflow"] == "BUGFIX" and result["period_days"] == 30


def test_summarize_without_data_returns_empty_but_well_formed_values():
    result = summarize([], [], 7)
    assert result["executions"]["total"] == 0 and result["executions"]["median_duration_seconds"] is None
    assert result["agents"] == [] and result["daily"] == []
    assert result["executions"]["avg_tokens"] is None


# --- Base de données et endpoints -------------------------------------------------------------

@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _execution(db, user_id, status="success", workflow="BUGFIX", age_days=0, seconds=50):
    created = datetime.now(timezone.utc) - timedelta(days=age_days)
    entry = ExecutionHistory(
        user_request="r", workflow=workflow, status=status, user_id=user_id,
        created_at=created, updated_at=created + timedelta(seconds=seconds),
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def _agent_run(db, entry, agent="design", duration=10.0, calls=2):
    db.add(AgentRun(
        execution_id=entry.id, user_id=entry.user_id, workflow=entry.workflow, agent=agent,
        duration_seconds=duration, llm_calls=calls, usage_calls=calls, prompt_tokens=30, completion_tokens=10,
        total_tokens=40, created_at=entry.created_at,
    ))
    db.commit()


def test_persist_agent_runs_stores_one_row_per_measured_agent(session):
    entry = _execution(session, "u1")
    metrics = ExecutionMetrics()
    metrics.record_llm_call(DESIGNER, {"prompt_tokens": 3, "completion_tokens": 1})
    metrics.record_agent_done(DESIGNER, 4.2)
    execution_persistence.persist_agent_runs(session, entry, metrics)
    session.commit()
    rows = list(session.exec(select(AgentRun)))
    assert len(rows) == 1 and rows[0].agent == "design" and rows[0].total_tokens == 4 and rows[0].user_id == "u1"


def test_persist_agent_runs_never_raises(session, caplog):
    caplog.set_level("INFO", logger="myiacrew")
    execution_persistence.persist_agent_runs(session, ExecutionHistory(user_request="r", workflow="x"), object())
    assert "non enregistrées" in caplog.text


def test_metrics_summary_endpoint_is_scoped_to_the_user_period_and_workflow(session):
    mine = _execution(session, "u1")
    _agent_run(session, mine, "design", 10.0)
    _agent_run(session, mine, "qa", 20.0)
    other_workflow = _execution(session, "u1", workflow="FEATURE")
    _agent_run(session, other_workflow, "design", 99.0)
    old = _execution(session, "u1", age_days=60)
    _agent_run(session, old, "design", 500.0)
    foreign = _execution(session, "u2")
    _agent_run(session, foreign, "design", 700.0)
    running = _execution(session, "u1", status="running")
    _agent_run(session, running, "design", 800.0)

    everything = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "u1"})
    assert everything["executions"]["total"] == 2  # ni l'ancienne, ni celle d'un autre, ni la « running »
    design = next(a for a in everything["agents"] if a["agent"] == "design")
    assert design["runs"] == 2 and design["duration_p95"] < 100
    only_bugfix = routes_metrics.metrics_summary(days=30, workflow="BUGFIX", session=session, user={"id": "u1"})
    assert only_bugfix["executions"]["total"] == 1 and only_bugfix["workflow"] == "BUGFIX"
    wide = routes_metrics.metrics_summary(days=90, workflow=None, session=session, user={"id": "u1"})
    assert wide["executions"]["total"] == 3
    clamped = routes_metrics.metrics_summary(days=100000, workflow=None, session=session, user={"id": "u1"})
    assert clamped["period_days"] == 365


def test_metrics_summary_with_no_executions_is_empty(session):
    result = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "nobody"})
    assert result["executions"]["total"] == 0 and result["agents"] == []


def test_execution_agent_runs_endpoint_orders_by_pipeline_and_checks_ownership(session):
    from fastapi import HTTPException
    entry = _execution(session, "u1")
    _agent_run(session, entry, "qa", 5.0)
    _agent_run(session, entry, "design", 3.0)
    rows = routes_metrics.execution_agent_runs(execution_id=entry.id, session=session, user={"id": "u1"})
    assert [r["agent"] for r in rows] == ["design", "qa"] and rows[0]["label"] == "Conception"
    assert rows[0]["tokens_known"] is True and rows[0]["duration_seconds"] == 3.0
    with pytest.raises(HTTPException) as excinfo:
        routes_metrics.execution_agent_runs(execution_id=entry.id, session=session, user={"id": "u2"})
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException):
        routes_metrics.execution_agent_runs(execution_id=9999, session=session, user={"id": "u1"})


# --- Suppression d'historique et exécution de bout en bout ------------------------------------

def test_deleting_an_execution_removes_its_agent_runs_and_only_its_own(session):
    from fastapi import HTTPException
    doomed = _execution(session, "u1")
    kept = _execution(session, "u1")
    for entry in (doomed, kept):
        _agent_run(session, entry, "design")
        _agent_run(session, entry, "qa")
    result = main.delete_history_entry(execution_id=doomed.id, session=session, user={"id": "u1"})
    assert result == {"status": "deleted", "id": doomed.id}
    remaining = list(session.exec(select(AgentRun)))
    assert {r.execution_id for r in remaining} == {kept.id} and len(remaining) == 2
    running = _execution(session, "u1", status="running")
    _agent_run(session, running, "design")
    with pytest.raises(HTTPException) as excinfo:
        main.delete_history_entry(execution_id=running.id, session=session, user={"id": "u1"})
    assert excinfo.value.status_code == 409
    assert any(r.execution_id == running.id for r in session.exec(select(AgentRun)))


def _bulk(session, ids, user="u1"):
    return main.bulk_delete_history(
        payload=main.BulkDeleteInput(ids=ids), session=session, user={"id": user},
    )


def test_bulk_delete_removes_only_own_non_running_executions_and_their_agent_runs(session):
    a, b = _execution(session, "u1"), _execution(session, "u1", status="failed")
    running = _execution(session, "u1", status="running")
    foreign = _execution(session, "u2")
    kept = _execution(session, "u1")
    for entry in (a, b, running, foreign, kept):
        _agent_run(session, entry, "design")
    result = _bulk(session, [a.id, b.id, running.id, foreign.id, 9999])
    assert result["deleted"] == [a.id, b.id]
    assert result["skipped"] == [
        {"id": running.id, "reason": "running"},
        {"id": foreign.id, "reason": "not_found"},
        {"id": 9999, "reason": "not_found"},
    ]
    left = {e.id for e in session.exec(select(ExecutionHistory))}
    assert left == {running.id, foreign.id, kept.id}
    assert {r.execution_id for r in session.exec(select(AgentRun))} == {running.id, foreign.id, kept.id}


def test_bulk_delete_ignores_duplicates_and_accepts_an_empty_list(session):
    a = _execution(session, "u1")
    assert _bulk(session, [a.id, a.id, a.id]) == {"deleted": [a.id], "skipped": []}
    assert _bulk(session, []) == {"deleted": [], "skipped": []}


def test_bulk_delete_treats_out_of_range_ids_as_not_found(session):
    a = _execution(session, "u1")
    huge = 10**20
    assert _bulk(session, [a.id, huge, -5, 0]) == {
        "deleted": [a.id],
        "skipped": [{"id": huge, "reason": "not_found"}, {"id": -5, "reason": "not_found"}, {"id": 0, "reason": "not_found"}],
    }


def test_bulk_delete_rejects_more_than_the_maximum(session):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        _bulk(session, list(range(1, main.BULK_DELETE_MAX + 2)))
    assert excinfo.value.status_code == 422


@pytest.fixture()
def run_engine(monkeypatch):
    from sqlalchemy.pool import StaticPool
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)
    return engine


def _launch(run_engine, monkeypatch, fake_run):
    """Lance execution.run_crew_and_persist avec un faux crew (aucun LLM, aucun GitHub)."""

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)
    with Session(run_engine) as db:
        conversation = main.Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="bug", workflow="BUGFIX", status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = main.WorkflowExecutionInput(user_request="bug", target_workflow="BUGFIX")
    asyncio.run(execution.run_crew_and_persist(ids[0], ids[1], data, False, False, "", None, "prompt", "contexte"))
    return ids[0]


def test_successful_run_persists_agent_runs_and_real_llm_call_count(run_engine, monkeypatch):
    from types import SimpleNamespace
    from crewai.events.event_bus import crewai_event_bus

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        crewai_event_bus.emit(None, _llm_event(usage={"prompt_tokens": 30, "completion_tokens": 10}, call_id="a"))
        crewai_event_bus.emit(None, _llm_event(usage={"prompt_tokens": 20, "completion_tokens": 5}, call_id="b"))
        crewai_event_bus.emit(None, _llm_event(role="QA Engineer / Automated Tester", call_id="c"))
        metrics = _current_metrics.get()
        metrics.record_agent_done(DESIGNER, 3.5)
        metrics.record_agent_done("QA Engineer / Automated Tester", 6.0)
        return SimpleNamespace(raw="résultat final")

    execution_id = _launch(run_engine, monkeypatch, fake_run)
    with Session(run_engine) as db:
        entry = db.get(ExecutionHistory, execution_id)
        runs = {r.agent: r for r in db.exec(select(AgentRun)).all()}
    assert entry.status == "success" and entry.api_calls_count == 3
    assert set(runs) == {"design", "qa"} and all(r.execution_id == execution_id and r.user_id == "u1" for r in runs.values())
    assert runs["design"].llm_calls == 2 and runs["design"].total_tokens == 65 and runs["design"].duration_seconds == 3.5
    assert runs["qa"].status == "completed" and runs["qa"].usage_calls == 0


def test_failed_run_still_persists_what_was_measured_and_marks_incomplete_agents(run_engine, monkeypatch):
    from crewai.events.event_bus import crewai_event_bus

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        crewai_event_bus.emit(None, _llm_event(usage={"total_tokens": 12}, call_id="x"))
        _current_metrics.get().record_agent_done(DESIGNER, 2.0)
        crewai_event_bus.emit(None, _llm_event(role="Analyste Diagnostic Technique", usage={"total_tokens": 8}, call_id="y"))
        raise RuntimeError("boom inattendu")

    execution_id = _launch(run_engine, monkeypatch, fake_run)
    with Session(run_engine) as db:
        entry = db.get(ExecutionHistory, execution_id)
        runs = {r.agent: r for r in db.exec(select(AgentRun)).all()}
    assert entry.status == "failed" and "boom inattendu" in entry.result and entry.api_calls_count == 2
    assert (entry.error_code, entry.error_retryable) == ("INTERNAL_ERROR", False)
    assert runs["design"].status == "completed" and runs["diagnostic"].status == "incomplete"
    assert runs["diagnostic"].duration_seconds is None and runs["diagnostic"].llm_calls == 1


# --- Fuseau horaire et lecture par paquets ----------------------------------------------------

def test_summarize_groups_days_in_the_users_time_zone():
    late = datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc)
    early = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)
    executions = [
        {"id": 1, "status": "success", "created_at": late, "updated_at": late + timedelta(seconds=30)},
        {"id": 2, "status": "success", "created_at": early, "updated_at": early + timedelta(seconds=30)},
    ]
    assert [d["date"] for d in summarize([], executions, 30)["daily"]] == ["2026-10-01", "2026-10-02"]
    # UTC+2 : 23 h 30 UTC est déjà le lendemain à 01 h 30 locale, les deux exécutions tombent le 2.
    plus_two = summarize([], executions, 30, tz_offset_minutes=120)["daily"]
    assert [(d["date"], d["executions"]) for d in plus_two] == [("2026-10-02", 2)]
    # UTC-5 : 01 h 00 UTC est encore la veille à 20 h locale, les deux tombent le 1er.
    minus_five = summarize([], executions, 30, tz_offset_minutes=-300)["daily"]
    assert [(d["date"], d["executions"]) for d in minus_five] == [("2026-10-01", 2)]


def test_endpoint_applies_and_clamps_the_time_zone_offset(session):
    created = (datetime.now(timezone.utc) - timedelta(days=3)).replace(hour=23, minute=30, second=0, microsecond=0)
    entry = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                             created_at=created, updated_at=created + timedelta(seconds=40))
    session.add(entry)
    session.commit()
    utc_day = created.date().isoformat()
    next_day = (created + timedelta(days=1)).date().isoformat()

    def days(offset):
        result = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=offset, session=session, user={"id": "u1"})
        return [d["date"] for d in result["daily"]]

    assert days(0) == [utc_day] and days(120) == [next_day]
    assert days(100000) == [next_day]  # borné à +14 h : 23 h 30 UTC passe bien au lendemain, sans erreur
    assert days(-100000) == [utc_day]


def test_endpoint_reads_all_agent_runs_across_several_chunks(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_IN_CLAUSE_CHUNK", 7)
    now = datetime.now(timezone.utc)
    entries = [ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                                created_at=now - timedelta(minutes=i), updated_at=now - timedelta(minutes=i) + timedelta(seconds=10))
               for i in range(23)]
    session.add_all(entries)
    session.commit()
    for entry in entries:
        session.refresh(entry)
    session.add_all([AgentRun(execution_id=e.id, user_id="u1", workflow="BUGFIX", agent="design", duration_seconds=5.0,
                              llm_calls=2, usage_calls=2, total_tokens=10, created_at=e.created_at) for e in entries])
    session.commit()
    result = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "u1"})
    design = next(a for a in result["agents"] if a["agent"] == "design")
    assert result["executions"]["total"] == 23 and design["runs"] == 23


def test_endpoint_handles_the_maximum_number_of_executions_with_the_real_chunk_size(session):
    now = datetime.now(timezone.utc)
    entries = [ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                                created_at=now - timedelta(seconds=i), updated_at=now - timedelta(seconds=i) + timedelta(seconds=5))
               for i in range(1000)]
    session.add_all(entries)
    session.commit()
    for entry in entries:
        session.refresh(entry)
    session.add_all([AgentRun(execution_id=e.id, user_id="u1", workflow="BUGFIX", agent="qa", duration_seconds=1.0,
                              llm_calls=1, created_at=e.created_at) for e in entries])
    session.commit()
    result = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "u1"})
    assert result["executions"]["total"] == 1000
    assert next(a for a in result["agents"] if a["agent"] == "qa")["runs"] == 1000


# --- Échecs par cause ---------------------------------------------------------------------------

def test_failure_causes_group_sort_and_label_failed_executions_only():
    from agent_metrics import failure_causes
    executions = [
        {"status": "failed", "error_code": "QUOTA_EXHAUSTED"},
        {"status": "failed", "error_code": "GUARDRAIL_FAILED"},
        {"status": "failed", "error_code": "QUOTA_EXHAUSTED"},
        {"status": "failed", "error_code": None},
        {"status": "failed", "error_code": "FUTURE_CODE"},
        {"status": "success", "error_code": "QUOTA_EXHAUSTED"},  # jamais compté : pas un échec
    ]
    assert failure_causes(executions) == [
        {"code": "QUOTA_EXHAUSTED", "label": "Quota du modèle épuisé", "count": 2},
        {"code": "FUTURE_CODE", "label": "FUTURE_CODE", "count": 1},
        {"code": "GUARDRAIL_FAILED", "label": "Contrôle de qualité non respecté", "count": 1},
        {"code": "UNCLASSIFIED", "label": "Cause non enregistrée", "count": 1},
    ]
    assert failure_causes([]) == []
    assert failure_causes([{"status": "failed", "error_code": "DELIVERY_FAILED"}])[0]["label"] == "Livraison GitHub non confirmée"


def test_summary_endpoint_exposes_failure_causes_for_the_user_and_period_only(session):
    mine = _execution(session, "u1", status="failed")
    mine.error_code = "LLM_TIMEOUT"
    other = _execution(session, "u2", status="failed")
    other.error_code = "QUOTA_EXHAUSTED"
    old = _execution(session, "u1", status="failed", age_days=60)
    old.error_code = "QUOTA_EXHAUSTED"
    for entry in (mine, other, old):
        session.add(entry)
    session.commit()
    result = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=0, session=session, user={"id": "u1"})
    assert result["failures"] == [{"code": "LLM_TIMEOUT", "label": "Délai du modèle dépassé", "count": 1}]


def test_every_code_the_classifier_can_emit_has_a_dashboard_label():
    import asyncio as _asyncio
    from agent_metrics import FAILURE_LABELS
    from errors import ErrorCode, classify_exception
    samples = [
        RuntimeError("429 quota"), RuntimeError("503 unavailable"), _asyncio.TimeoutError(),
        RuntimeError("guardrail failed"), RuntimeError("inconnue"),
    ]
    emitted = {classify_exception(exc).code for exc in samples} | {ErrorCode.INTERRUPTED, ErrorCode.GITHUB_UNAVAILABLE}
    assert emitted <= set(FAILURE_LABELS)


def test_summary_flags_truncation_when_the_execution_limit_is_reached(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 2)
    for _ in range(3):
        _execution(session, "u1")
    result = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=0, session=session, user={"id": "u1"})
    assert result["truncated"] is True and result["executions"]["total"] == 2
    fewer = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=0, session=session, user={"id": "u9"})
    assert fewer["truncated"] is False


# --- Comparaison à la période précédente et exactitude au-delà de 1 000 exécutions ------------------

def _summary(session, days=30, workflow=None, user="u1"):
    return routes_metrics.metrics_summary(days=days, workflow=workflow, tz_offset=0, session=session, user={"id": user})


def test_summary_compares_with_the_previous_period_of_the_same_length(session):
    _execution(session, "u1", status="success", age_days=5, seconds=100)
    _execution(session, "u1", status="failed", age_days=10, seconds=300)
    _execution(session, "u1", status="success", age_days=40, seconds=50)   # période précédente
    _execution(session, "u1", status="success", age_days=45, seconds=70)   # période précédente
    _execution(session, "u1", status="failed", age_days=90, seconds=10)    # trop ancienne : hors des deux
    result = _summary(session)
    assert result["executions"]["total"] == 2 and result["executions"]["median_duration_seconds"] == 200.0
    previous = result["previous"]
    assert previous["total"] == 2 and previous["success"] == 2 and previous["failed"] == 0
    assert previous["median_duration_seconds"] == 60.0


def test_summary_has_no_comparison_without_a_previous_period(session):
    _execution(session, "u1", age_days=2)
    assert _summary(session)["previous"] is None


def test_previous_period_is_scoped_to_the_user_and_the_workflow(session):
    _execution(session, "u1", workflow="BUGFIX", age_days=2)
    _execution(session, "u1", workflow="BUGFIX", age_days=40)
    _execution(session, "u1", workflow="FEATURE", age_days=40)
    _execution(session, "u2", workflow="BUGFIX", age_days=40)
    assert _summary(session)["previous"]["total"] == 2           # BUGFIX + FEATURE de u1
    assert _summary(session, workflow="BUGFIX")["previous"]["total"] == 1


def test_no_comparison_when_the_limit_cuts_the_previous_period(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 3)
    for age in (1, 2, 40, 41):
        _execution(session, "u1", age_days=age)
    result = _summary(session)
    assert result["previous"] is None            # 3 lignes lues : la période précédente est incomplète
    assert result["truncated"] is False          # …mais la période courante, elle, est complète
    assert result["comparison_limited"] is True  # et l'interface peut dire POURQUOI il n'y a pas de comparaison
    assert result["executions"]["total"] == 2


def test_summary_is_exact_beyond_the_old_thousand_executions_limit(session):
    assert routes_metrics._METRICS_EXECUTION_LIMIT > 1000
    from datetime import datetime as dt
    stamp = dt.now(timezone.utc) - timedelta(days=3)
    session.add_all([
        ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                         created_at=stamp, updated_at=stamp + timedelta(seconds=60))
        for _ in range(1200)
    ])
    session.commit()
    result = _summary(session)
    assert result["executions"]["total"] == 1200 and result["truncated"] is False
    assert result["daily"][0]["executions"] == 1200


def test_daily_median_duration_is_none_for_a_day_without_measurable_duration():
    base = datetime(2026, 10, 1, 10, 0, 0)
    result = summarize([], [{"id": 1, "status": "success", "created_at": base, "updated_at": None,
                             "rate_limit_hits": None, "total_wait_time_seconds": None}], 30)
    assert result["daily"][0]["median_duration_seconds"] is None


def test_comparison_limited_is_false_when_nothing_is_cut(session):
    _execution(session, "u1", age_days=2)
    _execution(session, "u1", age_days=40)
    result = _summary(session)
    assert result["comparison_limited"] is False and result["previous"] is not None


def test_current_period_cut_by_the_limit_is_reported_as_truncated(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 2)
    for age in (1, 2, 3):
        _execution(session, "u1", age_days=age)
    result = _summary(session)
    assert result["truncated"] is True and result["previous"] is None and result["comparison_limited"] is False


def test_measures_of_the_discarded_previous_period_are_not_loaded(session, monkeypatch):
    from sqlalchemy import event
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 3)
    monkeypatch.setattr(routes_metrics, "_IN_CLAUSE_CHUNK", 1)  # une requête de mesures par exécution chargée
    for age in (1, 2, 40, 41):
        _agent_run(session, _execution(session, "u1", age_days=age))
    queries = []
    engine = session.get_bind()

    def count(conn, cursor, statement, *args):
        if "FROM agentrun" in statement:
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        _summary(session)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(queries) == 2  # seulement les 2 exécutions de la période courante, pas les 3 lues


# --- Point 8 : coût, raisonnement, qualité ------------------------------------------------------

def test_final_verdict_takes_the_last_mention_and_none_when_absent():
    from qa_report import final_verdict
    assert final_verdict("Verdict : GO") == "GO"
    assert final_verdict("Verdict : GO\\n\\n... corrigé ...\\nVerdict : NO_GO") == "NO_GO"
    assert final_verdict("Verdict : GO_AVEC_RESERVES") == "GO_AVEC_RESERVES"
    assert final_verdict("aucun rapport") is None and final_verdict("") is None


def test_execution_cost_needs_a_configured_price():
    from agent_metrics import execution_cost
    assert execution_cost(1_000_000, 500_000, (2.0, 8.0)) == 6.0
    assert execution_cost(1000, 1000, None) is None
    assert execution_cost(1000, 1000, (0.0, 0.0)) is None


def test_token_prices_are_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("TOKEN_PRICE_INPUT_PER_MILLION", raising=False)
    monkeypatch.delenv("TOKEN_PRICE_OUTPUT_PER_MILLION", raising=False)
    assert routes_metrics._token_prices() is None
    monkeypatch.setenv("TOKEN_PRICE_INPUT_PER_MILLION", "0.5")
    monkeypatch.setenv("TOKEN_PRICE_OUTPUT_PER_MILLION", "2")
    assert routes_metrics._token_prices() == (0.5, 2.0)
    monkeypatch.setenv("TOKEN_PRICE_INPUT_PER_MILLION", "abc")
    assert routes_metrics._token_prices() is None


def test_summary_counts_reasoning_quality_and_cost(session, monkeypatch):
    monkeypatch.setenv("TOKEN_PRICE_INPUT_PER_MILLION", "1")
    monkeypatch.setenv("TOKEN_PRICE_OUTPUT_PER_MILLION", "4")
    monkeypatch.setenv("COST_CURRENCY", "€")
    retried = _execution(session, "u1", age_days=1)
    retried.attempts, retried.qa_verdict = 2, "GO"
    resumed = _execution(session, "u1", age_days=2)
    resumed.reused_steps, resumed.qa_verdict = 2, "NO_GO"
    plain = _execution(session, "u1", age_days=3)
    plain.attempts, plain.qa_verdict = 1, "GO_AVEC_RESERVES"
    for entry in (retried, resumed, plain):
        session.add(entry)
    session.commit()
    _agent_run(session, retried)  # 30 tokens d'entrée, 10 de sortie
    result = _summary(session)
    e = result["executions"]
    assert e["auto_retried"] == 1 and e["resumed"] == 1
    assert e["qa_verdicts"] == {"GO": 1, "GO_AVEC_RESERVES": 1, "NO_GO": 1}
    assert e["total_cost"] == pytest.approx(0.00007, abs=1e-6)  # 30 × 1/1M + 10 × 4/1M
    assert result["currency"] == "€"
    assert result["daily"][-1]["qa_total"] >= 1


def test_cost_is_hidden_without_a_price_or_without_known_tokens(session, monkeypatch):
    monkeypatch.delenv("TOKEN_PRICE_INPUT_PER_MILLION", raising=False)
    monkeypatch.delenv("TOKEN_PRICE_OUTPUT_PER_MILLION", raising=False)
    _agent_run(session, _execution(session, "u1", age_days=1))
    result = _summary(session)
    assert result["executions"]["total_cost"] is None and result["currency"] is None


def test_new_execution_records_one_attempt_and_the_reused_steps_count():
    from database import ExecutionHistory as Row
    entry = Row(user_request="x", workflow="BUGFIX", attempts=1, reused_steps=0)
    assert (entry.attempts, entry.reused_steps, entry.qa_verdict) == (1, 0, None)


# --- Point 4 : liste des exécutions ---------------------------------------------------------------

def _list(session, **params):
    defaults = dict(days=30, workflow=None, status=None, sort="created_at", order="desc", limit=20, offset=0, user="u1")
    defaults.update(params)
    user = defaults.pop("user")
    return routes_metrics.metrics_executions(session=session, user={"id": user}, **defaults)


def test_executions_list_is_scoped_filtered_and_carries_the_measures(session):
    mine = _execution(session, "u1", age_days=1, seconds=100)
    failed = _execution(session, "u1", status="failed", age_days=2, seconds=50)
    _execution(session, "u2", age_days=1)                   # autre utilisateur
    _execution(session, "u1", age_days=60)                  # hors période
    _agent_run(session, mine, calls=3)
    page = _list(session)
    assert page["total"] == 2 and [i["id"] for i in page["items"]] == [mine.id, failed.id]
    first = page["items"][0]
    assert first["duration_seconds"] == 100.0 and first["llm_calls"] == 3 and first["tokens"] == 40
    assert page["items"][1]["llm_calls"] is None and page["items"][1]["tokens"] is None  # aucune mesure
    assert [i["id"] for i in _list(session, status="failed")["items"]] == [failed.id]


def test_executions_list_sorts_with_missing_values_always_last(session):
    slow = _execution(session, "u1", age_days=1, seconds=300)
    fast = _execution(session, "u1", age_days=2, seconds=20)
    unmeasured = _execution(session, "u1", age_days=3, seconds=60)
    _agent_run(session, slow, calls=9)
    _agent_run(session, fast, calls=2)
    by_calls = [i["id"] for i in _list(session, sort="llm_calls")["items"]]
    assert by_calls == [slow.id, fast.id, unmeasured.id]
    ascending = [i["id"] for i in _list(session, sort="llm_calls", order="asc")["items"]]
    assert ascending == [fast.id, slow.id, unmeasured.id]      # manquante toujours en dernier
    assert [i["id"] for i in _list(session, sort="duration")["items"]] == [slow.id, unmeasured.id, fast.id]


def test_executions_list_paginates_and_clamps_its_limits(session):
    ids = [_execution(session, "u1", age_days=1 + n).id for n in range(5)]
    page = _list(session, limit=2, offset=2)
    assert page["total"] == 5 and [i["id"] for i in page["items"]] == ids[2:4]
    assert len(_list(session, limit=10_000)["items"]) == 5          # borné à 100
    assert _list(session, offset=-5)["items"][0]["id"] == ids[0]   # offset négatif ramené à 0
    assert len(_list(session, sort="n_importe_quoi")["items"]) == 5  # tri inconnu : par date


def test_executions_list_truncates_long_requests(session):
    entry = _execution(session, "u1", age_days=1)
    entry.user_request = "x" * 5000
    session.add(entry)
    session.commit()
    text = _list(session)["items"][0]["user_request"]
    assert len(text) <= 141 and text.endswith("…")


# --- Revue : filtre SQL, paramètres validés, rattrapage des verdicts ------------------------------

def test_executions_endpoint_rejects_unknown_filter_and_sort_values():
    import inspect
    from typing import get_args
    hints = inspect.signature(routes_metrics.metrics_executions).parameters
    assert set(get_args(hints["order"].annotation)) == {"asc", "desc"}
    assert set(get_args(hints["sort"].annotation)) == {"created_at", "duration", "llm_calls", "tokens"}
    assert "failed" in str(hints["status"].annotation) and "success" in str(hints["status"].annotation)


def test_status_filter_total_counts_only_the_filtered_rows(session):
    _execution(session, "u1", age_days=1)
    _execution(session, "u1", status="failed", age_days=2)
    _execution(session, "u1", status="failed", age_days=3)
    page = _list(session, status="failed")
    assert page["total"] == 2 and all(i["status"] == "failed" for i in page["items"])


def test_backfill_sets_the_verdict_of_old_successful_executions_only(run_engine):
    with Session(run_engine) as db:
        old = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1", result="## QA\nVerdict : NO_GO")
        none = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1", result="aucun QA")
        failed = ExecutionHistory(user_request="r", workflow="BUGFIX", status="failed", user_id="u1", result="Verdict : GO")
        done = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1", result="Verdict : NO_GO", qa_verdict="GO")
        db.add_all([old, none, failed, done])
        db.commit()
        ids = [old.id, none.id, failed.id, done.id]
    main._backfill_qa_verdicts()
    with Session(run_engine) as db:
        verdicts = [db.get(ExecutionHistory, i).qa_verdict for i in ids]
    assert verdicts == ["NO_GO", None, None, "GO"]
