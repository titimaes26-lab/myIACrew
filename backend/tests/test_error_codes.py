"""Codes d'erreur : classification des exceptions, statuts HTTP, mots-clés, échec avec erreur de quota."""
import asyncio


import pytest
from sqlmodel import Session, SQLModel, create_engine

import schemas
import execution
import database
from database import ExecutionHistory
from errors import DeliveryError, ErrorCode, classify_exception, http_status_for
from github_snapshot import GitHubVerificationUnavailable


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


def test_failed_execution_with_quota_error_stores_a_retryable_code(monkeypatch):
    from sqlalchemy.pool import StaticPool
    from crew_workflow import CrewStepError
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(database, "engine", engine)

    async def fake_run(self, inputs, request_type, on_step_change=None, on_task_output_complete=None, resume_outputs=None):
        raise CrewStepError(2, 5, "Architecte ", RuntimeError("429 RESOURCE_EXHAUSTED"))

    class FakeCrew:
        run_dynamic_crew = fake_run

    monkeypatch.setattr(execution, "AppDevelopmentCrew", FakeCrew)
    with Session(engine) as db:
        conversation = database.Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        entry = ExecutionHistory(user_request="x", workflow="BUGFIX", status="running", user_id="u1", conversation_id=conversation.id)
        db.add(entry)
        db.commit()
        db.refresh(entry)
        ids = (entry.id, conversation.id)
    data = schemas.WorkflowExecutionInput(user_request="x", target_workflow="BUGFIX")
    asyncio.run(execution.run_crew_and_persist(ids[0], ids[1], data, False, False, "", None, "prompt", "contexte"))
    with Session(engine) as db:
        saved = db.get(ExecutionHistory, ids[0])
    assert saved.status == "failed"
    assert (saved.error_code, saved.error_retryable) == ("QUOTA_EXHAUSTED", True)
    assert saved.result.startswith("Échec à l'étape 2/5 (Architecte) : Le quota du modèle IA")
    assert "détail : 429 RESOURCE_EXHAUSTED" in saved.result  # le texte d'origine n'est pas perdu


def test_classify_follows_the_cause_chain_of_a_step_error():
    from crew_workflow import CrewStepError
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
