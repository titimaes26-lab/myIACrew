"""Lancement d'une exécution : l'accès base se fait hors de la boucle d'événements, dans l'ordre attendu."""
import asyncio
import threading

import pytest
from fastapi import HTTPException
from sqlmodel import Session, select

import execution
import routes_execute
import schemas
from database import Conversation, ExecutionHistory


def _data(**kwargs):
    params = dict(user_request="corrige le bug", target_workflow="BUGFIX")
    params.update(kwargs)
    return schemas.WorkflowExecutionInput(**params)


@pytest.fixture
def launched(monkeypatch):
    """execute_crew_and_persist remplacé par un faux qui note ses arguments : aucun crew n'est lancé."""
    calls = []

    async def fake(*args, **kwargs):
        calls.append(args)

    monkeypatch.setattr(execution, "execute_crew_and_persist", fake)
    return calls


def _launch(engine, data, user="u1"):
    async def go():
        with Session(engine) as session:
            result = await routes_execute.execute_workflow(data, session=session, user={"id": user})
            await asyncio.sleep(0)               # laisse la tâche de fond (le faux) s'exécuter
            return result
    return asyncio.run(go())


def test_the_database_work_of_a_launch_runs_off_the_event_loop_thread(engine, launched, monkeypatch):
    threads = {}
    for name in ("_load_conversation", "_register_execution"):
        original = getattr(routes_execute, name)

        def spy(*args, _name=name, _original=original, **kwargs):
            threads[_name] = threading.get_ident()
            return _original(*args, **kwargs)
        monkeypatch.setattr(routes_execute, name, spy)
    loop_thread = []

    async def go():
        loop_thread.append(threading.get_ident())
        with Session(engine) as session:
            await routes_execute.execute_workflow(_data(), session=session, user={"id": "u1"})
    asyncio.run(go())
    assert set(threads) == {"_load_conversation", "_register_execution"}
    assert all(thread != loop_thread[0] for thread in threads.values())


def test_a_first_message_creates_the_conversation_and_a_running_row_then_starts_the_crew(engine, launched):
    result = _launch(engine, _data())
    assert result["status"] == "running" and result["resumed_steps"] == []
    with Session(engine) as session:
        conversation = session.get(Conversation, result["conversation_id"])
        entry = session.exec(select(ExecutionHistory)).one()
    assert conversation.user_id == "u1" and conversation.title == "corrige le bug"
    assert (entry.id, entry.status, entry.user_id) == (result["id"], "running", "u1")
    assert len(launched) == 1 and launched[0][0] == entry.id and launched[0][1] == conversation.id


def test_another_users_conversation_is_a_404_before_any_github_call_or_row(engine, launched, monkeypatch):
    with Session(engine) as session:
        other = Conversation(user_id="u2", title="privée")
        session.add(other)
        session.commit()
        session.refresh(other)
        conversation_id = other.id

    def no_github(*args):
        raise AssertionError("aucun appel GitHub avant le contrôle de la conversation")
    monkeypatch.setattr(routes_execute, "check_github_access", no_github)
    with pytest.raises(HTTPException) as error:
        _launch(engine, _data(conversation_id=conversation_id, repo_owner="o", repo_name="r"))
    assert error.value.status_code == 404
    with Session(engine) as session:
        assert session.exec(select(ExecutionHistory)).all() == [] and not launched


def test_the_github_check_comes_after_the_conversation_and_before_any_row(engine, launched, monkeypatch):
    order = []

    def fake_check(owner, name, base):
        with Session(engine) as session:
            order.append(("github", len(session.exec(select(ExecutionHistory)).all())))
    monkeypatch.setattr(routes_execute, "check_github_access", fake_check)
    _launch(engine, _data(repo_owner="o", repo_name="r"))
    assert order == [("github", 0)]                          # aucune ligne créée au moment du contrôle GitHub
