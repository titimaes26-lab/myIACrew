"""Suppression d'historique (unitaire et par lot) : les mesures par agent partent avec l'exécution, et seulement la sienne."""
# ruff: noqa: F811  (la fixture `session` importée de metrics_support est reprise comme argument des tests)
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402
from sqlmodel import select  # noqa: E402

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import routes_history  # noqa: E402
from database import AgentRun, ExecutionHistory  # noqa: E402
from metrics_support import DESIGNER, _agent_run, _execution, session  # noqa: E402,F401


def test_deleting_an_execution_removes_its_agent_runs_and_only_its_own(session):
    from fastapi import HTTPException
    doomed = _execution(session, "u1")
    kept = _execution(session, "u1")
    for entry in (doomed, kept):
        _agent_run(session, entry, "design")
        _agent_run(session, entry, "qa")
    result = routes_history.delete_history_entry(execution_id=doomed.id, session=session, user={"id": "u1"})
    assert result == {"status": "deleted", "id": doomed.id}
    remaining = list(session.exec(select(AgentRun)))
    assert {r.execution_id for r in remaining} == {kept.id} and len(remaining) == 2
    running = _execution(session, "u1", status="running")
    _agent_run(session, running, "design")
    with pytest.raises(HTTPException) as excinfo:
        routes_history.delete_history_entry(execution_id=running.id, session=session, user={"id": "u1"})
    assert excinfo.value.status_code == 409
    assert any(r.execution_id == running.id for r in session.exec(select(AgentRun)))


def _bulk(session, ids, user="u1"):
    return routes_history.bulk_delete_history(
        payload=routes_history.BulkDeleteInput(ids=ids), session=session, user={"id": user},
    )


def test_bulk_delete_removes_only_own_non_running_executions_and_their_agent_runs(session):
    a, b = _execution(session, "u1"), _execution(session, "u1", status="failed")
    running = _execution(session, "u1", status="running")
    foreign = _execution(session, "u2")
    kept = _execution(session, "u1")
    for entry in (a, b, running, foreign, kept):
        _agent_run(session, entry, "design")
    result = _bulk(session, [a.id, b.id, running.id, foreign.id, 9999])
    assert result["deleted"] == [a.id, b.id]
    assert result["skipped"] == [
        {"id": running.id, "reason": "running"},
        {"id": foreign.id, "reason": "not_found"},
        {"id": 9999, "reason": "not_found"},
    ]
    left = {e.id for e in session.exec(select(ExecutionHistory))}
    assert left == {running.id, foreign.id, kept.id}
    assert {r.execution_id for r in session.exec(select(AgentRun))} == {running.id, foreign.id, kept.id}


def test_bulk_delete_ignores_duplicates_and_accepts_an_empty_list(session):
    a = _execution(session, "u1")
    assert _bulk(session, [a.id, a.id, a.id]) == {"deleted": [a.id], "skipped": []}
    assert _bulk(session, []) == {"deleted": [], "skipped": []}


def test_bulk_delete_treats_out_of_range_ids_as_not_found(session):
    a = _execution(session, "u1")
    huge = 10**20
    assert _bulk(session, [a.id, huge, -5, 0]) == {
        "deleted": [a.id],
        "skipped": [{"id": huge, "reason": "not_found"}, {"id": -5, "reason": "not_found"}, {"id": 0, "reason": "not_found"}],
    }


def test_bulk_delete_rejects_more_than_the_maximum(session):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as excinfo:
        _bulk(session, list(range(1, routes_history.BULK_DELETE_MAX + 2)))
    assert excinfo.value.status_code == 422
