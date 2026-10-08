"""Éléments communs aux tests des métriques : base SQLite en mémoire, fabriques d'exécutions et de mesures par agent."""
from datetime import datetime, timedelta, timezone


import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
from database import AgentRun, ExecutionHistory


DESIGNER = "Lead Product / Game Designer"


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


def _llm_event(role=DESIGNER, usage=None, call_id="c1"):
    from crewai.events.types.llm_events import LLMCallCompletedEvent
    return LLMCallCompletedEvent(call_id=call_id, response="ok", call_type="llm_call", agent_role=role, usage=usage)
