import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import main  # noqa: E402
from auth import get_current_user  # noqa: E402
from database import ExecutionHistory  # noqa: E402
from errors import AppError, DeliveryError, ErrorCode, classify_exception, http_status_for  # noqa: E402
from github_tools import GitHubVerificationUnavailable  # noqa: E402


@pytest.mark.parametrize("exc, code, retryable", [
    (RuntimeError("429 RESOURCE_EXHAUSTED: quota"), ErrorCode.QUOTA_EXHAUSTED, True),
    (RuntimeError("503 UNAVAILABLE: high demand"), ErrorCode.LLM_UNAVAILABLE, True),
    (asyncio.TimeoutError(), ErrorCode.LLM_TIMEOUT, True),
    (RuntimeError("request timed out"), ErrorCode.LLM_TIMEOUT, True),
    (RuntimeError("Guardrail failed after 1 attempt"), ErrorCode.GUARDRAIL_FAILED, False),
    (GitHubVerificationUnavailable("api down"), ErrorCode.GITHUB_UNAVAILABLE, True),
    (ValueError("n'importe quoi"), ErrorCode.INTERNAL_ERROR, False),
])
def test_classify_exception(exc, code, retryable):
    info = classify_exception(exc)
    assert (info.code, info.retryable) == (code, retryable)
    # Cause reconnue → message lisible ; inconnue → None (l'appelant garde le texte d'origine).
    assert (info.message is None) == (code == ErrorCode.INTERNAL_ERROR)


def test_delivery_failure_has_its_own_non_retryable_code_even_with_quota_words():
    exc = DeliveryError("aucune PR trouvée.\n--- Rapport de l'agent ---\nquota 429 timeout")
    info = classify_exception(exc)
    assert (info.code, info.retryable, info.message) == (ErrorCode.DELIVERY_FAILED, False, None)
    outer = RuntimeError("quota 429")
    outer.__cause__ = exc
    assert classify_exception(outer).code == ErrorCode.DELIVERY_FAILED


def test_http_status_for_codes():
    assert http_status_for(ErrorCode.QUOTA_EXHAUSTED) == 429
    assert http_status_for(ErrorCode.LLM_UNAVAILABLE) == 503
    assert http_status_for(ErrorCode.LLM_TIMEOUT) == 504
    assert http_status_for(ErrorCode.INTERNAL_ERROR) == 500


@pytest.fixture()
def client(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=__import__("sqlalchemy.pool", fromlist=["StaticPool"]).StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)
    main.app.dependency_overrides[get_current_user] = lambda: {"id": "u1"}
    main.app.dependency_overrides[main.get_session] = lambda: Session(engine)

    @main.app.get("/_test/app-error")
    async def _app_error():
        raise AppError(429, ErrorCode.QUOTA_EXHAUSTED, "trop vite", retryable=True)

    @main.app.get("/_test/boom")
    async def _boom():
        raise RuntimeError("secret interne")

    yield TestClient(main.app, raise_server_exceptions=False)
    main.app.dependency_overrides.clear()
    main.app.router.routes[:] = [r for r in main.app.router.routes if not getattr(r, "path", "").startswith("/_test")]


def test_app_error_has_the_unified_format(client):
    res = client.get("/_test/app-error")
    assert res.status_code == 429
    assert res.json() == {"detail": "trop vite", "code": "QUOTA_EXHAUSTED", "retryable": True}


def test_http_exception_gets_a_code(client):
    res = client.delete("/api/history/9999")
    assert res.status_code == 404
    assert res.json() == {"detail": "Exécution introuvable.", "code": "NOT_FOUND", "retryable": False}


def test_unknown_route_uses_the_same_format(client):
    res = client.get("/nope")
    assert res.status_code == 404 and res.json()["code"] == "NOT_FOUND"


def test_validation_error_detail_is_a_readable_string(client):
    res = client.post("/api/history/bulk-delete", json={"ids": "abc"})
    body = res.json()
    assert res.status_code == 422 and body["code"] == "VALIDATION_ERROR"
    assert isinstance(body["detail"], str) and body["detail"].startswith("Requête invalide")
    assert body["errors"] and body["errors"][0]["field"] == "ids"


def test_unhandled_exception_never_leaks_its_message(client):
    res = client.get("/_test/boom")
    assert res.status_code == 500
    assert res.json() == {"detail": "Erreur interne du serveur.", "code": "INTERNAL_ERROR", "retryable": False}


def test_qualify_failure_is_classified_without_leaking_details(client, monkeypatch):
    async def boom(*args, **kwargs):
        raise RuntimeError("429 RESOURCE_EXHAUSTED api_key=SECRET")
    monkeypatch.setattr(main.crew_instance, "analyze_user_request", boom)
    res = client.post("/api/qualify", json={"user_request": "x"})
    assert res.status_code == 429
    assert res.json()["code"] == "QUOTA_EXHAUSTED" and res.json()["retryable"] is True
    assert "SECRET" not in res.text


def test_failed_execution_with_quota_error_stores_a_retryable_code(monkeypatch):
    from sqlalchemy.pool import StaticPool
    from crewquestion import CrewStepError
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(main, "engine", engine)

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        raise CrewStepError(2, 5, "Architecte ", RuntimeError("429 RESOURCE_EXHAUSTED"))

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(main, "AppDevelopmentCrew", FakeCrew)
    with Session(engine) as db:
        conversation = main.Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow="BUGFIX", status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = main.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX")
    asyncio.run(main._run_crew_and_persist(ids[0], ids[1], data, False, False, "", None, "prompt", "contexte"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, ids[0])
    assert saved.status == "failed"
    assert (saved.error_code, saved.error_retryable) == ("QUOTA_EXHAUSTED", True)
    assert saved.result.startswith("Échec à l'étape 2/5 (Architecte) : Le quota du modèle IA")
    assert "détail : 429 RESOURCE_EXHAUSTED" in saved.result  # le texte d'origine n'est pas perdu


def test_classify_follows_the_cause_chain_of_a_step_error():
    from crewquestion import CrewStepError
    try:
        try:
            raise asyncio.TimeoutError()
        except Exception as original:
            raise CrewStepError(1, 5, "Designer", original) from original
    except CrewStepError as step_error:
        assert classify_exception(step_error).code == ErrorCode.LLM_TIMEOUT


@pytest.mark.parametrize("text", [
    "FileNotFoundError: src/page_4290.tsx", "ModuleNotFoundError: GitHubVerificationUnavailable_x", "ligne 15031",
])
def test_markers_match_whole_words_only(text):
    assert classify_exception(RuntimeError(text)).code == ErrorCode.INTERNAL_ERROR


def test_structured_http_detail_is_preserved_and_gateway_statuses_have_codes(client):
    from fastapi import HTTPException

    @main.app.get("/_test/structured")
    async def _structured():
        raise HTTPException(status_code=502, detail={"upstream": "x"})

    res = client.get("/_test/structured")
    assert res.status_code == 502
    assert res.json()["code"] == "SERVICE_UNAVAILABLE" and res.json()["retryable"] is True
    assert res.json()["errors"] == {"upstream": "x"}
