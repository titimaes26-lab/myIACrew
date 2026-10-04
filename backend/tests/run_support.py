"""Fabriques communes aux tests des étapes d'exécution : entrée type, base en mémoire, faux crew."""
"""Étapes extraites de _run_crew_and_persist : fonctions pures ou presque, testées une à une."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

import schemas  # noqa: E402
import execution  # noqa: E402
import execution_context  # noqa: E402
import database  # noqa: E402
from crew_workflow import CrewStepError  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402
from database import Conversation, ExecutionHistory  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_snapshot(monkeypatch):
    # L'aperçu du repo lit GitHub : jamais de réseau dans ces tests (les tests dédiés le remplacent).
    monkeypatch.setattr(execution_context, "build_repo_snapshot", lambda *a: "")


def _data(**kwargs):
    params = dict(user_request="x", target_workflow="BUGFIX", repo_owner="o", repo_name="r")
    params.update(kwargs)
    return schemas.WorkflowExecutionInput(**params)


@pytest.fixture()
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(database, "engine", eng)
    return eng


def _new_execution(engine, workflow="BUGFIX"):
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow=workflow, status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.id, conversation.id


def _fake_crew(monkeypatch, outcome, seen):
    async def run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        seen.append(inputs)
        if isinstance(outcome, BaseException):
            raise outcome
        return SimpleNamespace(raw="résultat")

    class FakeCrew:
        run_dynamic_crew = run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)


def _step_error(index, message="503 UNAVAILABLE"):
    return CrewStepError(index, 5, "Designer", RuntimeError(message))
