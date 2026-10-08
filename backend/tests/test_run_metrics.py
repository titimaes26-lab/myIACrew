"""Exécution de bout en bout avec un faux crew : mesures persistées, échec partiel, rattrapage des verdicts QA au démarrage."""
import asyncio


from sqlmodel import Session, select

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import database
import execution
import main
import schemas
from database import AgentRun, ExecutionHistory
from metrics_collect import current_metrics
from metrics_support import DESIGNER, _llm_event


def _launch(engine, monkeypatch, fake_run):
    """Lance execution.run_crew_and_persist avec un faux crew (aucun LLM, aucun GitHub)."""

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)
    with Session(engine) as db:
        conversation = database.Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="bug", workflow="BUGFIX", status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = schemas.WorkflowExecutionInput(user_request="bug", target_workflow="BUGFIX")
    asyncio.run(execution.run_crew_and_persist(ids[0], ids[1], data, False, False, "", None, "prompt", "contexte"))
    return ids[0]


def test_successful_run_persists_agent_runs_and_real_llm_call_count(engine, monkeypatch):
    from types import SimpleNamespace
    from crewai.events.event_bus import crewai_event_bus

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        crewai_event_bus.emit(None, _llm_event(usage={"prompt_tokens": 30, "completion_tokens": 10}, call_id="a"))
        crewai_event_bus.emit(None, _llm_event(usage={"prompt_tokens": 20, "completion_tokens": 5}, call_id="b"))
        crewai_event_bus.emit(None, _llm_event(role="QA Engineer / Automated Tester", call_id="c"))
        metrics = current_metrics.get()
        metrics.record_agent_done(DESIGNER, 3.5)
        metrics.record_agent_done("QA Engineer / Automated Tester", 6.0)
        return SimpleNamespace(raw="résultat final")

    execution_id = _launch(engine, monkeypatch, fake_run)
    with Session(engine) as db:
        entry = db.get(ExecutionHistory, execution_id)
        runs = {r.agent: r for r in db.exec(select(AgentRun)).all()}
    assert entry.status == "success" and entry.api_calls_count == 3
    assert set(runs) == {"design", "qa"} and all(r.execution_id == execution_id and r.user_id == "u1" for r in runs.values())
    assert runs["design"].llm_calls == 2 and runs["design"].total_tokens == 65 and runs["design"].duration_seconds == 3.5
    assert runs["qa"].status == "completed" and runs["qa"].usage_calls == 0


def test_failed_run_still_persists_what_was_measured_and_marks_incomplete_agents(engine, monkeypatch):
    from crewai.events.event_bus import crewai_event_bus

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        crewai_event_bus.emit(None, _llm_event(usage={"total_tokens": 12}, call_id="x"))
        current_metrics.get().record_agent_done(DESIGNER, 2.0)
        crewai_event_bus.emit(None, _llm_event(role="Analyste Diagnostic Technique", usage={"total_tokens": 8}, call_id="y"))
        raise RuntimeError("boom inattendu")

    execution_id = _launch(engine, monkeypatch, fake_run)
    with Session(engine) as db:
        entry = db.get(ExecutionHistory, execution_id)
        runs = {r.agent: r for r in db.exec(select(AgentRun)).all()}
    assert entry.status == "failed" and "boom inattendu" in entry.result and entry.api_calls_count == 2
    assert (entry.error_code, entry.error_retryable) == ("INTERNAL_ERROR", False)
    assert runs["design"].status == "completed" and runs["diagnostic"].status == "incomplete"
    assert runs["diagnostic"].duration_seconds is None and runs["diagnostic"].llm_calls == 1


def test_backfill_sets_the_verdict_of_old_successful_executions_only(engine):
    with Session(engine) as db:
        old = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1", result="## QA\nVerdict : NO_GO")
        none = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1", result="aucun QA")
        failed = ExecutionHistory(user_request="r", workflow="BUGFIX", status="failed", user_id="u1", result="Verdict : GO")
        done = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1", result="Verdict : NO_GO", qa_verdict="GO")
        db.add_all([old, none, failed, done])
        db.commit()
        ids = [old.id, none.id, failed.id, done.id]
    main._backfill_qa_verdicts()
    with Session(engine) as db:
        verdicts = [db.get(ExecutionHistory, i).qa_verdict for i in ids]
    assert verdicts == ["NO_GO", None, None, "GO"]
