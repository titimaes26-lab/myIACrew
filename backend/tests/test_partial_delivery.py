"""Livraison partielle : ce que GitHub contient déjà quand une exécution échoue, et ce qu'on n'a pas pu vérifier."""
import asyncio


import github_snapshot
import github_client
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

import schemas
import execution
import execution_outcomes
import execution_context
import database
from database import Conversation, ExecutionHistory
from delivery import render_partial_delivery_block
from github_delivery import PartialDelivery, describe_partial_delivery
from github_snapshot import GitHubVerificationUnavailable


def test_block_for_a_branch_with_new_commits_and_a_pr():
    block = render_partial_delivery_block(
        "o", "r", "crewai/x", "main",
        PartialDelivery("crewai/x", True, True, 3, "https://github.com/o/r/pull/7", "open"),
    )
    assert "3 commit(s) d'avance sur `main`" in block and "nouveaux commits de cette tentative" in block
    assert "Pull Request ouverte : https://github.com/o/r/pull/7" in block
    assert "« Relancer » continue sur cette même branche" in block and "supprimez la branche `crewai/x`" in block


def test_block_when_nothing_was_pushed_or_github_is_unreachable():
    assert "Rien n'a été poussé" in render_partial_delivery_block(
        "o", "r", "b", "main", PartialDelivery("b", False, None, None, None, None))
    unreachable = render_partial_delivery_block("o", "r", "b", "main", None, "rate limit")
    assert "Impossible de vérifier GitHub (rate limit)" in unreachable and "peut contenir" in unreachable


def test_describe_partial_delivery_reads_branch_commits_and_pr(monkeypatch):

    class Pull:
        state, merged_at, html_url = "open", None, "https://github.com/o/r/pull/1"

    class Repo:
        def compare(self, base, head):
            return type("C", (), {"ahead_by": 2})()

        def get_pulls(self, **kwargs):
            return [Pull()]

    monkeypatch.setattr(github_snapshot, "get_branch_head_sha", lambda *a: "new")
    monkeypatch.setattr(github_client, "_get_repo", lambda *a: Repo())
    assert describe_partial_delivery("o", "r", "b", "main", "old") == PartialDelivery(
        "b", True, True, 2, "https://github.com/o/r/pull/1", "open")
    assert describe_partial_delivery("o", "r", "b", "main", "new").new_commits is False
    monkeypatch.setattr(github_snapshot, "get_branch_head_sha", lambda *a: None)
    assert describe_partial_delivery("o", "r", "b", "main", None).branch_exists is False


def test_failed_execution_reports_what_github_already_has(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        raise RuntimeError("boom")

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)
    monkeypatch.setattr(execution_context, "get_branch_head_sha", lambda *a: "sha0")
    monkeypatch.setattr(
        execution_outcomes, "describe_partial_delivery",
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
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX", repo_owner="o", repo_name="r")
    asyncio.run(execution.run_crew_and_persist(ids[0], ids[1], data, True, True, "crewai/b", "main", "prompt", "ctx"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, ids[0])
    assert saved.status == "failed" and "boom" in saved.result
    assert "--- Travail déjà présent sur GitHub ---" in saved.result and "Branche `crewai/b`" in saved.result


def test_github_unreachable_during_failure_report_is_stated_not_hidden(monkeypatch):
    def unavailable(*args):
        raise GitHubVerificationUnavailable("api down")
    monkeypatch.setattr(execution_outcomes, "describe_partial_delivery", unavailable)
    block = asyncio.run(execution_outcomes.partial_delivery_block("o", "r", "b", "main", None))
    assert "Impossible de vérifier GitHub (api down)" in block


def test_pr_lookup_failure_is_reported_as_unverified_not_as_absent(monkeypatch):

    class Repo:
        def compare(self, base, head):
            return type("C", (), {"ahead_by": 1})()

        def get_pulls(self, **kwargs):
            raise RuntimeError("rate limit")

    monkeypatch.setattr(github_snapshot, "get_branch_head_sha", lambda *a: "sha")
    monkeypatch.setattr(github_client, "_get_repo", lambda *a: Repo())
    partial = describe_partial_delivery("o", "r", "b", "main", "sha")
    assert partial.pr_checked is False
    block = render_partial_delivery_block("o", "r", "b", "main", partial)
    assert "non vérifiée" in block and "aucune ouverte" not in block


def test_timeout_while_checking_github_is_readable(monkeypatch):
    def slow(*args):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(execution_outcomes, "describe_partial_delivery", slow)
    block = asyncio.run(execution_outcomes.partial_delivery_block("o", "r", "b", "main", None))
    assert "délai dépassé" in block and "TimeoutError" not in block
