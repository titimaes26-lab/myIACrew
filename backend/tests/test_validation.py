import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from github import GithubException  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import github_tools  # noqa: E402
import main  # noqa: E402
import fastapi  # noqa: E402
import schemas  # noqa: E402
import routes_execute  # noqa: E402
import execution  # noqa: E402
import database  # noqa: E402
import validation  # noqa: E402
from auth import get_current_user  # noqa: E402
from database import Conversation, ExecutionHistory, ExecutionLaunch  # noqa: E402
from errors import AppError  # noqa: E402
from github_tools import GitHubAccessProblem, check_github_access  # noqa: E402


@pytest.mark.parametrize("owner", ["octocat", "a", "my-org", "A1-b2", "x" * 39, "jdoe_acme"])
def test_valid_owners(owner):
    assert validation.validate_repo_owner(owner) == owner


@pytest.mark.parametrize("owner", ["-bad", "bad-", "_bad", "bad_", "a b", "a/b", "x" * 40, "né"])
def test_invalid_owners(owner):
    with pytest.raises(ValueError):
        validation.validate_repo_owner(owner)


@pytest.mark.parametrize("name", ["repo", "my.repo_1-x", "a" * 100])
def test_valid_repo_names(name):
    assert validation.validate_repo_name(name) == name


@pytest.mark.parametrize("name", [".", "..", "a b", "a/b", "a" * 101, "répo"])
def test_invalid_repo_names(name):
    with pytest.raises(ValueError):
        validation.validate_repo_name(name)


@pytest.mark.parametrize("branch", ["main", "feature/x-1", "release_2.0", "crewai/bugfix-ab12"])
def test_valid_branches(branch):
    assert validation.validate_branch_name(branch) == branch


@pytest.mark.parametrize("branch", [
    "feature/.hidden", "release.lock/x", ".hidden", "a b", "a..b", "-x", "/x", "x/", "x.", "x.lock", "a//b", "a@{b", "a~b", "a^b", "a:b", "a?b", "a*b", "a[b", "a\\b", "@", "a\nb", "x" * 256,
])
def test_invalid_branches(branch):
    with pytest.raises(ValueError):
        validation.validate_branch_name(branch)


def test_empty_values_become_none():
    for fn in (validation.validate_repo_owner, validation.validate_repo_name, validation.validate_branch_name):
        assert fn("") is None and fn("   ") is None and fn(None) is None


def test_request_and_workflow_limits():
    with pytest.raises(ValueError):
        validation.validate_user_request("   ")
    with pytest.raises(ValueError):
        validation.validate_user_request("x" * (validation.MAX_REQUEST_CHARS + 1))
    with pytest.raises(ValueError):
        validation.validate_clarifications("x" * (validation.MAX_REQUEST_CHARS + 1))
    with pytest.raises(ValueError):
        validation.validate_workflow("AUTO")
    assert validation.validate_workflow("FEATURE") == "FEATURE"


def test_models_normalize_empty_repo_fields_so_no_repo_target():
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX", repo_owner="", repo_name="", base_branch="")
    assert (data.repo_owner, data.repo_name, data.base_branch) == (None, None, None)


# --- Endpoint : 422 lisible, rien créé ---------------------------------------------------------

@pytest.fixture()
def engine(monkeypatch):
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    monkeypatch.setattr(database, "engine", eng)
    return eng


def test_invalid_repo_gives_a_field_level_422_and_creates_nothing(engine):
    main.app.dependency_overrides[get_current_user] = lambda: {"id": "u1"}
    main.app.dependency_overrides[database.get_session] = lambda: Session(engine)
    try:
        res = TestClient(main.app, raise_server_exceptions=False).post("/api/execute", json={
            "user_request": "corrige", "target_workflow": "BUGFIX", "repo_owner": "o", "repo_name": "a b",
        })
    finally:
        main.app.dependency_overrides.clear()
    body = res.json()
    assert res.status_code == 422 and body["code"] == "VALIDATION_ERROR"
    assert body["errors"][0]["field"] == "repo_name" and "Value error" not in body["detail"]
    with Session(engine) as db:
        assert not db.exec(select(Conversation)).all() and not db.exec(select(ExecutionHistory)).all()


# --- Contrôle préalable GitHub ------------------------------------------------------------------

class _Repo:
    def __init__(self, push=True, branch_error=None):
        self.permissions = type("P", (), {"push": push})()
        self._branch_error = branch_error

    def get_branch(self, name):
        if self._branch_error:
            raise self._branch_error
        return object()


@pytest.fixture()
def token(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "t")


def _problem(**kwargs):
    with pytest.raises(GitHubAccessProblem) as excinfo:
        check_github_access("o", "r", "main")
    return excinfo.value


def test_access_ok(token, monkeypatch):
    monkeypatch.setattr(github_tools, "_get_repo", lambda *a: _Repo())
    check_github_access("o", "r", "main")


def test_missing_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    assert _problem().kind == "missing_token"


@pytest.mark.parametrize("status, kind", [(404, "not_found"), (401, "invalid_token"), (403, "forbidden"), (500, "unavailable")])
def test_repository_errors_by_status(token, monkeypatch, status, kind):
    def boom(*args):
        raise GithubException(status, {"message": "x"}, {})
    monkeypatch.setattr(github_tools, "_get_repo", boom)
    assert _problem().kind == kind


def test_network_failure_is_unavailable(token, monkeypatch):
    def boom(*args):
        raise ConnectionError("réseau")
    monkeypatch.setattr(github_tools, "_get_repo", boom)
    assert _problem().kind == "unavailable"


def test_read_only_token_is_forbidden(token, monkeypatch):
    monkeypatch.setattr(github_tools, "_get_repo", lambda *a: _Repo(push=False))
    problem = _problem()
    assert problem.kind == "forbidden" and "écrire" in problem.message


def test_missing_base_branch(token, monkeypatch):
    monkeypatch.setattr(github_tools, "_get_repo", lambda *a: _Repo(branch_error=GithubException(404, {}, {})))
    problem = _problem()
    assert problem.kind == "not_found" and "« main »" in problem.message


# --- execute_workflow : refus avant toute écriture ----------------------------------------------

def test_execute_refuses_before_creating_anything(engine, monkeypatch):
    def refuse(*args):
        raise GitHubAccessProblem("forbidden", "pas de droit d'écriture")
    monkeypatch.setattr(routes_execute, "check_github_access", refuse)
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX", repo_owner="o", repo_name="r")
    with Session(engine) as db:
        with pytest.raises(AppError) as excinfo:
            asyncio.run(routes_execute.execute_workflow(data=data, session=db, user={"id": "u1"}))
        assert (excinfo.value.status_code, excinfo.value.code, excinfo.value.retryable) == (403, "FORBIDDEN", False)
        assert not db.exec(select(Conversation)).all() and not db.exec(select(ExecutionHistory)).all()


def test_execute_skips_the_check_for_analysis_and_without_repo(engine, monkeypatch):
    calls = []
    monkeypatch.setattr(routes_execute, "check_github_access", lambda *a: calls.append(a))
    monkeypatch.setattr("limits.MAX_RUNNING_PER_USER", 5)  # la première exécution simulée reste « running »

    async def fake_run(*args, **kwargs):
        return None
    monkeypatch.setattr(execution, "execute_crew_and_persist", fake_run)
    for data in (
        schemas.WorkflowExecutionInput(user_request="x", target_workflow="ANALYSE_ONLY", repo_owner="o", repo_name="r"),
        schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX"),
    ):
        async def scenario():
            with Session(engine) as db:
                return await routes_execute.execute_workflow(data=data, session=db, user={"id": "u1"})
            await asyncio.sleep(0)
        assert asyncio.run(scenario())["status"] == "running"
    assert calls == []


def test_rate_limited_launch_creates_no_conversation_and_accepted_launch_is_journaled(engine, monkeypatch):
    monkeypatch.setattr("limits.MAX_RUNNING_PER_USER", 1)

    async def fake_run(*args, **kwargs):
        return None
    monkeypatch.setattr(execution, "execute_crew_and_persist", fake_run)
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX")

    def launch():
        async def scenario():
            with Session(engine) as db:
                return await routes_execute.execute_workflow(data=data, session=db, user={"id": "u1"})
        return asyncio.run(scenario())

    assert launch()["status"] == "running"          # la première exécution reste « running »
    with Session(engine) as db:
        conversations = len(db.exec(select(Conversation)).all())
        assert len(db.exec(select(ExecutionLaunch)).all()) == 1
    with pytest.raises(AppError) as err:
        launch()                                   # nouvelle conversation demandée : refus AVANT toute écriture
    assert err.value.status_code == 429
    with Session(engine) as db:
        assert len(db.exec(select(Conversation)).all()) == conversations
        assert len(db.exec(select(ExecutionLaunch)).all()) == 1


def test_double_send_in_the_same_conversation_keeps_the_precise_409(engine, monkeypatch):
    monkeypatch.setattr("limits.MAX_RUNNING_PER_USER", 1)

    async def fake_run(*args, **kwargs):
        return None
    monkeypatch.setattr(execution, "execute_crew_and_persist", fake_run)

    def launch(conversation_id=None):
        data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX", conversation_id=conversation_id)

        async def scenario():
            with Session(engine) as db:
                return await routes_execute.execute_workflow(data=data, session=db, user={"id": "u1"})
        return asyncio.run(scenario())

    first = launch()
    with pytest.raises(fastapi.HTTPException) as err:
        launch(first["conversation_id"])           # même conversation : conflit précis, pas le plafond par compte
    assert err.value.status_code == 409 and "cette conversation" in err.value.detail
    with Session(engine) as db:
        assert len(db.exec(select(ExecutionLaunch)).all()) == 1


def test_workflows_follow_the_qualification_literal():
    from typing import get_args
    from qualification import RequestType
    assert validation.ALLOWED_WORKFLOWS == get_args(RequestType)


def test_rate_limit_is_retryable_not_forbidden(token, monkeypatch):
    def boom(*args):
        raise GithubException(403, {"message": "API rate limit exceeded for user"}, {})
    monkeypatch.setattr(github_tools, "_get_repo", boom)
    problem = _problem()
    assert problem.kind == "rate_limited"
    assert routes_execute._GITHUB_ACCESS_ERRORS["rate_limited"] == (503, "GITHUB_UNAVAILABLE", True)


def test_plain_403_stays_forbidden(token, monkeypatch):
    def boom(*args):
        raise GithubException(403, {"message": "Resource not accessible by integration"}, {})
    monkeypatch.setattr(github_tools, "_get_repo", boom)
    assert _problem().kind == "forbidden"


def test_slow_github_precheck_times_out_with_a_retryable_error(engine, monkeypatch):
    import time
    monkeypatch.setattr(routes_execute, "_GITHUB_PRECHECK_TIMEOUT_S", 0.05)
    monkeypatch.setattr(routes_execute, "check_github_access", lambda *a: time.sleep(0.3))
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX", repo_owner="o", repo_name="r")
    with Session(engine) as db:
        with pytest.raises(AppError) as excinfo:
            asyncio.run(routes_execute.execute_workflow(data=data, session=db, user={"id": "u1"}))
        assert (excinfo.value.status_code, excinfo.value.retryable) == (503, True)
        assert not db.exec(select(Conversation)).all()


def test_foreign_conversation_is_refused_before_any_github_call(engine, monkeypatch):
    from fastapi import HTTPException
    calls = []
    monkeypatch.setattr(routes_execute, "check_github_access", lambda *a: calls.append(a))
    with Session(engine) as db:
        foreign = Conversation(user_id="u2", title="t")
        db.add(foreign)
        db.commit()
        db.refresh(foreign)
        data = schemas.WorkflowExecutionInput(
            user_request="x", target_workflow="BUGFIX", repo_owner="o", repo_name="r", conversation_id=foreign.id)
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(routes_execute.execute_workflow(data=data, session=db, user={"id": "u1"}))
    assert excinfo.value.status_code == 404 and calls == []
