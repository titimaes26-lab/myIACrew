import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine, select  # noqa: E402

import main  # noqa: E402
import schemas  # noqa: E402
import routes_execute  # noqa: E402
import execution  # noqa: E402
import database  # noqa: E402
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
    monkeypatch.setattr(database, "engine", engine)
    main.app.dependency_overrides[get_current_user] = lambda: {"id": "u1"}
    main.app.dependency_overrides[database.get_session] = lambda: Session(engine)

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
    monkeypatch.setattr(routes_execute.crew_instance, "analyze_user_request", boom)
    res = client.post("/api/qualify", json={"user_request": "x"})
    assert res.status_code == 429
    assert res.json()["code"] == "QUOTA_EXHAUSTED" and res.json()["retryable"] is True
    assert "SECRET" not in res.text


def test_failed_execution_with_quota_error_stores_a_retryable_code(monkeypatch):
    from sqlalchemy.pool import StaticPool
    from crewquestion import CrewStepError
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


# --- Points d'accès synchrones : exécutés dans le pool de threads, jamais sur la boucle d'événements -------------

SYNC_ENDPOINTS = (
    "create_conversation", "list_conversations", "get_conversation_messages", "get_conversation_progress",
    "list_repo_targets", "metrics_summary", "metrics_executions", "execution_agent_runs", "get_history",
    "delete_history_entry", "bulk_delete_history",
)


def test_database_endpoints_are_plain_functions_so_fastapi_runs_them_in_a_thread():
    import inspect
    # Lu sur les routes réellement enregistrées (peu importe le module qui définit chaque point d'accès).
    def flatten(routes):
        for route in routes:
            original = getattr(route, "original_router", None)   # routeur inclus (APIRouter) : on descend dedans
            if original is not None:
                yield from flatten(original.routes)
            elif hasattr(route, "endpoint"):
                yield route

    endpoints = {route.endpoint.__name__: route.endpoint for route in flatten(main.app.routes)}
    for name in SYNC_ENDPOINTS:
        assert name in endpoints, f"{name} n'est plus une route de l'application"
        assert not inspect.iscoroutinefunction(endpoints[name]), f"{name} bloquerait la boucle d'événements"
    # Ceux qui attendent GitHub ou le LLM restent asynchrones.
    assert inspect.iscoroutinefunction(endpoints["execute_workflow"]) and inspect.iscoroutinefunction(endpoints["qualify_request"])


def test_endpoints_serve_requests_from_the_thread_pool(client):
    assert client.get("/api/history").json() == []
    summary = client.get("/api/metrics/summary").json()
    assert summary["executions"]["total"] == 0
    assert client.get("/api/metrics/executions").json() == {"total": 0, "items": []}
    assert client.get("/api/conversations").json() == []
    created = client.post("/api/conversations", json={"title": "t"})
    assert created.status_code == 200 and created.json()["title"] == "t"
    assert client.get(f"/api/conversations/{created.json()['id']}/messages").json() == []


def test_deleting_someone_elses_execution_is_logged_without_user_ids_or_request_text(client, caplog):
    from database import ExecutionHistory
    caplog.set_level("DEBUG", logger="myiacrew")
    with Session(database.engine) as db:
        other = ExecutionHistory(user_request="DEMANDE-CONFIDENTIELLE", workflow="BUGFIX", status="success", user_id="u2")
        db.add(other)
        db.commit()
        db.refresh(other)
        other_id = other.id
    assert client.delete(f"/api/history/{other_id}").status_code == 404
    assert "n'appartient pas" in caplog.text
    assert "u1" not in caplog.text and "u2" not in caplog.text and "DEMANDE-CONFIDENTIELLE" not in caplog.text


def test_a_successful_deletion_logs_the_id_only(client, caplog):
    from database import ExecutionHistory
    caplog.set_level("DEBUG", logger="myiacrew")
    with Session(database.engine) as db:
        mine = ExecutionHistory(user_request="MA-DEMANDE-PRIVÉE", workflow="BUGFIX", status="success", user_id="u1")
        db.add(mine)
        db.commit()
        db.refresh(mine)
        mine_id = mine.id
    assert client.delete(f"/api/history/{mine_id}").status_code == 200
    assert f"exécution {mine_id} supprimée" in caplog.text and "MA-DEMANDE-PRIVÉE" not in caplog.text


# --- Liste d'historique allégée et lecture d'une exécution -----------------------------------------------------------

def _add_execution(user="u1", result="RÉSULTAT-VOLUMINEUX " * 50, **fields):
    from database import ExecutionHistory
    with Session(database.engine) as db:
        entry = ExecutionHistory(
            user_request="demande", workflow="FEATURE", status="success", user_id=user, result=result,
            clarifications="PRÉCISIONS", conversation_id=7, **fields,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry.id


def test_history_list_never_carries_the_result_nor_internal_fields(client):
    entry_id = _add_execution(scope="PETIT", qa_verdict="GO")
    body = client.get("/api/history").json()
    assert [row["id"] for row in body] == [entry_id]
    row = body[0]
    assert {"result", "clarifications", "user_id", "work_branch", "base_branch"}.isdisjoint(row)
    assert (row["user_request"], row["workflow"], row["status"], row["conversation_id"]) == ("demande", "FEATURE", "success", 7)
    assert (row["scope"], row["qa_verdict"]) == ("PETIT", "GO")
    assert "RÉSULTAT-VOLUMINEUX" not in client.get("/api/history").text


def test_history_list_does_not_even_read_the_result_column_from_the_database(client):
    from sqlalchemy import event
    _add_execution()
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(database.engine, "before_cursor_execute", record)
    try:
        assert client.get("/api/history").status_code == 200
    finally:
        event.remove(database.engine, "before_cursor_execute", record)
    selects = [s for s in statements if "FROM executionhistory" in s]
    assert selects and not any("executionhistory.result" in s or "executionhistory.clarifications" in s for s in selects)


def test_one_execution_comes_with_its_full_result_and_only_for_its_owner(client):
    mine = _add_execution()
    theirs = _add_execution(user="u2")
    full = client.get(f"/api/executions/{mine}")
    assert full.status_code == 200 and full.json()["result"].startswith("RÉSULTAT-VOLUMINEUX")
    assert client.get(f"/api/executions/{theirs}").status_code == 404
    assert client.get("/api/executions/99999").status_code == 404


def test_conversation_messages_still_carry_full_results_to_reload_a_conversation(client):
    from database import Conversation
    with Session(database.engine) as db:
        conversation = Conversation(user_id="u1", title="t")
        db.add(conversation)
        db.commit()
        db.refresh(conversation)
        conversation_id = conversation.id
    _add_execution(result="TEXTE-COMPLET")
    with Session(database.engine) as db:
        from database import ExecutionHistory
        entry = db.exec(select(ExecutionHistory)).first()
        entry.conversation_id = conversation_id
        db.add(entry)
        db.commit()
    messages = client.get(f"/api/conversations/{conversation_id}/messages").json()
    assert messages[0]["result"] == "TEXTE-COMPLET"
