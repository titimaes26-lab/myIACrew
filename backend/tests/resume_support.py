"""Fabriques communes aux tests de reprise : base en mémoire, exécutions échouées avec points de reprise, lancement avec un faux crew."""
import asyncio


from sqlmodel import Session

import crew_workflow
import schemas
import execution
from database import Conversation, ExecutionCheckpoint, ExecutionHistory


def TaskOutputFactory(raw):
    from crewai.tasks.task_output import TaskOutput
    return TaskOutput(description="d", raw=raw, agent="x")


def _failed_with_checkpoints(db, workflow="DESIGN_AND_DEV", user="u1", steps=("design", "architecture"), **kwargs):
    conversation = Conversation(user_id=user, title="t")
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    entry = ExecutionHistory(user_request="x", workflow=workflow, status="failed", user_id=user, conversation_id=conversation.id, **kwargs)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    for step in steps:
        db.add(ExecutionCheckpoint(execution_id=entry.id, step=step, raw=f"sortie {step}"))
    db.commit()
    return conversation, entry


def _data(entry, **kwargs):
    params = dict(user_request="x", target_workflow="DESIGN_AND_DEV", resume_from_execution_id=entry.id)
    params.update(kwargs)
    return schemas.WorkflowExecutionInput(**params)


DESIGNER = "Lead Product / Game Designer"


def _step_error(index, role, message="503 UNAVAILABLE: high demand", total=5):
    return crew_workflow.CrewStepError(index, total, role, RuntimeError(message))


def _launch_with(engine, monkeypatch, fake_run, request_type="DESIGN_AND_DEV", resume_outputs=None):
    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow=request_type, status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow=request_type)
    asyncio.run(execution.execute_crew_and_persist(
        ids[0], ids[1], data, False, False, "", None, "prompt", "ctx", resume_outputs))
    return ids[0]


def _saved(engine, execution_id):
    with Session(engine) as db:
        return db.get(ExecutionHistory, execution_id)


def _new_failed_execution(engine, result):
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow="BUGFIX", status="failed", user_id="u1",
                                 conversation_id=conversation.id, result=result)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.id
