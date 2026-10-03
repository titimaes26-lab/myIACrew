import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import main  # noqa: E402
from database import Conversation, ExecutionHistory  # noqa: E402
from delivery import render_partial_delivery_block  # noqa: E402
from github_tools import GitHubVerificationUnavailable, PartialDelivery, describe_partial_delivery  # noqa: E402
from orphans import INTERRUPTED_MESSAGE, ORPHAN_AFTER_SECONDS, sweep_stale_executions  # noqa: E402

OLD = timedelta(seconds=ORPHAN_AFTER_SECONDS + 60)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as db:
        yield db


def _running(db, age=OLD, user="u1", conversation_id=1, result=None):
    stamp = datetime.now(timezone.utc) - age
    entry = ExecutionHistory(
        user_request="r", workflow="BUGFIX", status="running", user_id=user, conversation_id=conversation_id,
        current_step="qa", result=result, created_at=stamp, updated_at=stamp,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def test_stale_running_execution_is_marked_interrupted_and_retryable(session):
    stale = _running(session, result="## Designer\nplan")
    assert sweep_stale_executions(session) == [stale.id]
    session.refresh(stale)
    assert (stale.status, stale.error_code, stale.error_retryable, stale.current_step) == ("failed", "INTERRUPTED", True, None)
    assert stale.result.startswith("## Designer\nplan") and stale.result.endswith(INTERRUPTED_MESSAGE)


def test_fresh_and_active_executions_are_never_swept(session):
    fresh = _running(session, age=timedelta(seconds=30))
    active = _running(session)
    assert sweep_stale_executions(session, active_ids={active.id}) == []
    session.refresh(fresh)
    session.refresh(active)
    assert fresh.status == active.status == "running"


def test_sweep_respects_its_scope(session):
    mine = _running(session, user="u1", conversation_id=1)
    other_conversation = _running(session, user="u1", conversation_id=2)
    other_user = _running(session, user="u2", conversation_id=3)
    assert sweep_stale_executions(session, conversation_id=1) == [mine.id]
    assert sweep_stale_executions(session, user_id="u1") == [other_conversation.id]
    session.refresh(other_user)
    assert other_user.status == "running"


def test_progress_poll_releases_a_dead_execution(session):
    conversation = Conversation(user_id="u1", title="t")
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    stale = _running(session, conversation_id=conversation.id)
    result = asyncio.run(main.get_conversation_progress(conversation_id=conversation.id, session=session, user={"id": "u1"}))
    assert result["status"] is None  # plus rien en cours : le frontend resynchronise le tour
    session.refresh(stale)
    assert stale.status == "failed" and stale.error_code == "INTERRUPTED"


def test_stale_running_entry_becomes_deletable(session):
    stale = _running(session)
    result = asyncio.run(main.delete_history_entry(execution_id=stale.id, session=session, user={"id": "u1"}))
    assert result["status"] == "deleted"
    live = _running(session, age=timedelta(seconds=5))
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main.delete_history_entry(execution_id=live.id, session=session, user={"id": "u1"}))
    assert excinfo.value.status_code == 409


def test_step_change_is_a_heartbeat(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    with Session(engine) as db:
        entry = _running(db)
        before = entry.updated_at
        entry_id = entry.id
    main._persist_current_step(entry_id, "design")
    with Session(engine) as db:
        assert db.get(ExecutionHistory, entry_id).updated_at.replace(tzinfo=None) > before.replace(tzinfo=None) + timedelta(seconds=60)


# --- Écritures GitHub partielles ----------------------------------------------------------------

def test_block_for_a_branch_with_new_commits_and_a_pr():
    block = render_partial_delivery_block(
        "o", "r", "crewai/x", "main",
        PartialDelivery("crewai/x", True, True, 3, "https://github.com/o/r/pull/7", "open"),
    )
    assert "3 commit(s) d'avance sur `main`" in block and "nouveaux commits de cette tentative" in block
    assert "Pull Request ouverte : https://github.com/o/r/pull/7" in block
    assert "« Réessayer » continue sur cette même branche" in block and "supprimez la branche `crewai/x`" in block


def test_block_when_nothing_was_pushed_or_github_is_unreachable():
    assert "Rien n'a été poussé" in render_partial_delivery_block(
        "o", "r", "b", "main", PartialDelivery("b", False, None, None, None, None))
    unreachable = render_partial_delivery_block("o", "r", "b", "main", None, "rate limit")
    assert "Impossible de vérifier GitHub (rate limit)" in unreachable and "peut contenir" in unreachable


def test_describe_partial_delivery_reads_branch_commits_and_pr(monkeypatch):
    import github_tools

    class Pull:
        state, merged_at, html_url = "open", None, "https://github.com/o/r/pull/1"

    class Repo:
        def compare(self, base, head):
            return type("C", (), {"ahead_by": 2})()

        def get_pulls(self, **kwargs):
            return [Pull()]

    monkeypatch.setattr(github_tools, "get_branch_head_sha", lambda *a: "new")
    monkeypatch.setattr(github_tools, "_get_repo", lambda *a: Repo())
    assert describe_partial_delivery("o", "r", "b", "main", "old") == PartialDelivery(
        "b", True, True, 2, "https://github.com/o/r/pull/1", "open")
    assert describe_partial_delivery("o", "r", "b", "main", "new").new_commits is False
    monkeypatch.setattr(github_tools, "get_branch_head_sha", lambda *a: None)
    assert describe_partial_delivery("o", "r", "b", "main", None).branch_exists is False


def test_failed_execution_reports_what_github_already_has(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None):
        raise RuntimeError("boom")

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(main, "AppDevelopmentCrew", FakeCrew)
    monkeypatch.setattr(main, "get_branch_head_sha", lambda *a: "sha0")
    monkeypatch.setattr(
        main, "describe_partial_delivery",
        lambda *a: PartialDelivery("crewai/b", True, True, 1, None, None),
    )
    with Session(engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow="BUGFIX", status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = main.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX", repo_owner="o", repo_name="r")
    asyncio.run(main._run_crew_and_persist(ids[0], ids[1], data, True, True, "crewai/b", "main", "prompt", "ctx"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, ids[0])
    assert saved.status == "failed" and "boom" in saved.result
    assert "--- Travail déjà présent sur GitHub ---" in saved.result and "Branche `crewai/b`" in saved.result


def test_github_unreachable_during_failure_report_is_stated_not_hidden(monkeypatch):
    def unavailable(*args):
        raise GitHubVerificationUnavailable("api down")
    monkeypatch.setattr(main, "describe_partial_delivery", unavailable)
    block = asyncio.run(main._partial_delivery_block("o", "r", "b", "main", None))
    assert "Impossible de vérifier GitHub (api down)" in block


def test_pr_lookup_failure_is_reported_as_unverified_not_as_absent(monkeypatch):
    import github_tools

    class Repo:
        def compare(self, base, head):
            return type("C", (), {"ahead_by": 1})()

        def get_pulls(self, **kwargs):
            raise RuntimeError("rate limit")

    monkeypatch.setattr(github_tools, "get_branch_head_sha", lambda *a: "sha")
    monkeypatch.setattr(github_tools, "_get_repo", lambda *a: Repo())
    partial = describe_partial_delivery("o", "r", "b", "main", "sha")
    assert partial.pr_checked is False
    block = render_partial_delivery_block("o", "r", "b", "main", partial)
    assert "non vérifiée" in block and "aucune ouverte" not in block


def test_timeout_while_checking_github_is_readable(monkeypatch):
    def slow(*args):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(main, "describe_partial_delivery", slow)
    block = asyncio.run(main._partial_delivery_block("o", "r", "b", "main", None))
    assert "délai dépassé" in block and "TimeoutError" not in block


def test_sweep_by_ids_leaves_other_stale_rows_alone(session):
    wanted, other = _running(session), _running(session)
    assert sweep_stale_executions(session, ids=[wanted.id]) == [wanted.id]
    session.refresh(other)
    assert other.status == "running"


def test_tracked_task_registers_active_id_and_releases_everything_when_done():
    async def scenario():
        async def work():
            await asyncio.sleep(0.01)
        task = asyncio.create_task(work())
        main._track_execution_task(task, 4242)
        assert 4242 in main._active_execution_ids and task in main._background_tasks
        await task
        await asyncio.sleep(0)
        return 4242 in main._active_execution_ids, task in main._background_tasks
    assert asyncio.run(scenario()) == (False, False)


def test_heartbeat_touches_only_running_rows(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    with Session(engine) as db:
        alive = _running(db)
        done = _running(db)
        done.status = "success"
        db.add(done)
        db.commit()
        ids = (alive.id, done.id)
        before = alive.updated_at.replace(tzinfo=None)
    main._touch_execution(ids[0])
    main._touch_execution(ids[1])
    with Session(engine) as db:
        assert db.get(ExecutionHistory, ids[0]).updated_at.replace(tzinfo=None) > before + timedelta(seconds=60)
        assert db.get(ExecutionHistory, ids[1]).updated_at.replace(tzinfo=None) <= before + timedelta(seconds=1)
