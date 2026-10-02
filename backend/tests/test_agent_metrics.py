import asyncio
import os
import sys
import threading
from contextvars import copy_context
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import main  # noqa: E402
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
        {"date": "2026-10-01", "executions": 1, "failed": 0, "llm_calls": 4, "tokens": 200},
        {"date": "2026-10-02", "executions": 1, "failed": 1, "llm_calls": 2, "tokens": 0},
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
    main._persist_agent_runs(session, entry, metrics)
    session.commit()
    rows = list(session.exec(select(AgentRun)))
    assert len(rows) == 1 and rows[0].agent == "design" and rows[0].total_tokens == 4 and rows[0].user_id == "u1"


def test_persist_agent_runs_never_raises(session, capsys):
    main._persist_agent_runs(session, ExecutionHistory(user_request="r", workflow="x"), object())
    assert "non enregistrées" in capsys.readouterr().out


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

    everything = asyncio.run(main.metrics_summary(days=30, workflow=None, session=session, user={"id": "u1"}))
    assert everything["executions"]["total"] == 2  # ni l'ancienne, ni celle d'un autre, ni la « running »
    design = next(a for a in everything["agents"] if a["agent"] == "design")
    assert design["runs"] == 2 and design["duration_p95"] < 100
    only_bugfix = asyncio.run(main.metrics_summary(days=30, workflow="BUGFIX", session=session, user={"id": "u1"}))
    assert only_bugfix["executions"]["total"] == 1 and only_bugfix["workflow"] == "BUGFIX"
    wide = asyncio.run(main.metrics_summary(days=90, workflow=None, session=session, user={"id": "u1"}))
    assert wide["executions"]["total"] == 3
    clamped = asyncio.run(main.metrics_summary(days=100000, workflow=None, session=session, user={"id": "u1"}))
    assert clamped["period_days"] == 365


def test_metrics_summary_with_no_executions_is_empty(session):
    result = asyncio.run(main.metrics_summary(days=30, workflow=None, session=session, user={"id": "nobody"}))
    assert result["executions"]["total"] == 0 and result["agents"] == []


def test_execution_agent_runs_endpoint_orders_by_pipeline_and_checks_ownership(session):
    from fastapi import HTTPException
    entry = _execution(session, "u1")
    _agent_run(session, entry, "qa", 5.0)
    _agent_run(session, entry, "design", 3.0)
    rows = asyncio.run(main.execution_agent_runs(execution_id=entry.id, session=session, user={"id": "u1"}))
    assert [r["agent"] for r in rows] == ["design", "qa"] and rows[0]["label"] == "Conception"
    assert rows[0]["tokens_known"] is True and rows[0]["duration_seconds"] == 3.0
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main.execution_agent_runs(execution_id=entry.id, session=session, user={"id": "u2"}))
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException):
        asyncio.run(main.execution_agent_runs(execution_id=9999, session=session, user={"id": "u1"}))
