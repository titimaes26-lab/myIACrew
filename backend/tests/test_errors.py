"""Format d'erreur unique de l'API, points d'accès synchrones, historique allégé."""


import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select

import main
import routes_execute
import database
from auth import get_current_user
from errors import AppError, ErrorCode


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


def test_structured_http_detail_is_preserved_and_gateway_statuses_have_codes(client):
    from fastapi import HTTPException

    @main.app.get("/_test/structured")
    async def _structured():
        raise HTTPException(status_code=502, detail={"upstream": "x"})

    res = client.get("/_test/structured")
    assert res.status_code == 502
    assert res.json()["code"] == "SERVICE_UNAVAILABLE" and res.json()["retryable"] is True
    assert res.json()["errors"] == {"upstream": "x"}


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
