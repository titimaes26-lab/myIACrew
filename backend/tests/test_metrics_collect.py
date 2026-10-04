"""Collecte des mesures : lecture des usages, rôles, compteurs par agent, événements CrewAI, lignes persistées."""
import threading
from datetime import datetime


import pytest
from sqlmodel import select

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import execution_persistence
from database import AgentRun, ExecutionHistory
from metrics_collect import ExecutionMetrics, build_agent_run_rows, flush_events, parse_usage, track_execution_metrics
from metrics_roles import step_for_role
from metrics_support import DESIGNER, _execution, _llm_event


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


def test_new_execution_records_one_attempt_and_the_reused_steps_count():
    from database import ExecutionHistory as Row
    entry = Row(user_request="x", workflow="BUGFIX", attempts=1, reused_steps=0)
    assert (entry.attempts, entry.reused_steps, entry.qa_verdict) == (1, 0, None)


def test_the_role_map_is_empty_when_the_yaml_is_unreadable_and_skips_incomplete_entries(monkeypatch):
    import metrics_roles

    def unreadable(*args, **kwargs):
        raise OSError("fichier illisible")
    monkeypatch.setattr(metrics_roles.yaml, "safe_load", unreadable)
    assert metrics_roles._load_role_map() == {}
    monkeypatch.setattr(metrics_roles.yaml, "safe_load", lambda text: {
        "architect_agent": {"role": "  Architecte  Test "}, "qa_agent": {"goal": "pas de rôle"}, "developer_agent": "pas un dict",
    })
    assert metrics_roles._load_role_map() == {metrics_roles._normalize_role("Architecte Test"): "architecture"}
