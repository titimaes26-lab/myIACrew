"""Points d'accès du tableau de bord : synthèse, comparaison de périodes, liste des exécutions, mesures d'une exécution."""
from datetime import datetime, timedelta, timezone


import pytest

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import routes_metrics
from database import AgentRun, ExecutionHistory
from metrics_support import _agent_run, _execution


def test_metrics_summary_endpoint_is_scoped_to_the_user_period_and_workflow(session):
    mine = _execution(session, "u1")
    _agent_run(session, mine, "design", 10.0)
    _agent_run(session, mine, "qa", 20.0)
    other_workflow = _execution(session, "u1", workflow="FEATURE")
    _agent_run(session, other_workflow, "design", 99.0)
    old = _execution(session, "u1", age_days=60)
    _agent_run(session, old, "design", 500.0)
    foreign = _execution(session, "u2")
    _agent_run(session, foreign, "design", 700.0)
    running = _execution(session, "u1", status="running")
    _agent_run(session, running, "design", 800.0)

    everything = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "u1"})
    assert everything["executions"]["total"] == 2  # ni l'ancienne, ni celle d'un autre, ni la « running »
    design = next(a for a in everything["agents"] if a["agent"] == "design")
    assert design["runs"] == 2 and design["duration_p95"] < 100
    only_bugfix = routes_metrics.metrics_summary(days=30, workflow="BUGFIX", session=session, user={"id": "u1"})
    assert only_bugfix["executions"]["total"] == 1 and only_bugfix["workflow"] == "BUGFIX"
    wide = routes_metrics.metrics_summary(days=90, workflow=None, session=session, user={"id": "u1"})
    assert wide["executions"]["total"] == 3
    clamped = routes_metrics.metrics_summary(days=100000, workflow=None, session=session, user={"id": "u1"})
    assert clamped["period_days"] == 365


def test_metrics_summary_with_no_executions_is_empty(session):
    result = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "nobody"})
    assert result["executions"]["total"] == 0 and result["agents"] == []


def test_execution_agent_runs_endpoint_orders_by_pipeline_and_checks_ownership(session):
    from fastapi import HTTPException
    entry = _execution(session, "u1")
    _agent_run(session, entry, "qa", 5.0)
    _agent_run(session, entry, "design", 3.0)
    rows = routes_metrics.execution_agent_runs(execution_id=entry.id, session=session, user={"id": "u1"})
    assert [r["agent"] for r in rows] == ["design", "qa"] and rows[0]["label"] == "Conception"
    assert rows[0]["tokens_known"] is True and rows[0]["duration_seconds"] == 3.0
    with pytest.raises(HTTPException) as excinfo:
        routes_metrics.execution_agent_runs(execution_id=entry.id, session=session, user={"id": "u2"})
    assert excinfo.value.status_code == 404
    with pytest.raises(HTTPException):
        routes_metrics.execution_agent_runs(execution_id=9999, session=session, user={"id": "u1"})


def test_endpoint_applies_and_clamps_the_time_zone_offset(session):
    created = (datetime.now(timezone.utc) - timedelta(days=3)).replace(hour=23, minute=30, second=0, microsecond=0)
    entry = ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                             created_at=created, updated_at=created + timedelta(seconds=40))
    session.add(entry)
    session.commit()
    utc_day = created.date().isoformat()
    next_day = (created + timedelta(days=1)).date().isoformat()

    def days(offset):
        result = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=offset, session=session, user={"id": "u1"})
        return [d["date"] for d in result["daily"]]

    assert days(0) == [utc_day] and days(120) == [next_day]
    assert days(100000) == [next_day]  # borné à +14 h : 23 h 30 UTC passe bien au lendemain, sans erreur
    assert days(-100000) == [utc_day]


def test_endpoint_reads_all_agent_runs_across_several_chunks(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_IN_CLAUSE_CHUNK", 7)
    now = datetime.now(timezone.utc)
    entries = [ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                                created_at=now - timedelta(minutes=i), updated_at=now - timedelta(minutes=i) + timedelta(seconds=10))
               for i in range(23)]
    session.add_all(entries)
    session.commit()
    for entry in entries:
        session.refresh(entry)
    session.add_all([AgentRun(execution_id=e.id, user_id="u1", workflow="BUGFIX", agent="design", duration_seconds=5.0,
                              llm_calls=2, usage_calls=2, total_tokens=10, created_at=e.created_at) for e in entries])
    session.commit()
    result = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "u1"})
    design = next(a for a in result["agents"] if a["agent"] == "design")
    assert result["executions"]["total"] == 23 and design["runs"] == 23


def test_endpoint_handles_the_maximum_number_of_executions_with_the_real_chunk_size(session):
    now = datetime.now(timezone.utc)
    entries = [ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                                created_at=now - timedelta(seconds=i), updated_at=now - timedelta(seconds=i) + timedelta(seconds=5))
               for i in range(1000)]
    session.add_all(entries)
    session.commit()
    for entry in entries:
        session.refresh(entry)
    session.add_all([AgentRun(execution_id=e.id, user_id="u1", workflow="BUGFIX", agent="qa", duration_seconds=1.0,
                              llm_calls=1, created_at=e.created_at) for e in entries])
    session.commit()
    result = routes_metrics.metrics_summary(days=30, workflow=None, session=session, user={"id": "u1"})
    assert result["executions"]["total"] == 1000
    assert next(a for a in result["agents"] if a["agent"] == "qa")["runs"] == 1000


def test_summary_endpoint_exposes_failure_causes_for_the_user_and_period_only(session):
    mine = _execution(session, "u1", status="failed")
    mine.error_code = "LLM_TIMEOUT"
    other = _execution(session, "u2", status="failed")
    other.error_code = "QUOTA_EXHAUSTED"
    old = _execution(session, "u1", status="failed", age_days=60)
    old.error_code = "QUOTA_EXHAUSTED"
    for entry in (mine, other, old):
        session.add(entry)
    session.commit()
    result = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=0, session=session, user={"id": "u1"})
    assert result["failures"] == [{"code": "LLM_TIMEOUT", "label": "Délai du modèle dépassé", "count": 1}]


def test_summary_flags_truncation_when_the_execution_limit_is_reached(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 2)
    for _ in range(3):
        _execution(session, "u1")
    result = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=0, session=session, user={"id": "u1"})
    assert result["truncated"] is True and result["executions"]["total"] == 2
    fewer = routes_metrics.metrics_summary(days=30, workflow=None, tz_offset=0, session=session, user={"id": "u9"})
    assert fewer["truncated"] is False


def _list(session, **params):
    defaults = dict(days=30, workflow=None, status=None, sort="created_at", order="desc", limit=20, offset=0, user="u1")
    defaults.update(params)
    user = defaults.pop("user")
    return routes_metrics.metrics_executions(session=session, user={"id": user}, **defaults)


def test_executions_list_is_scoped_filtered_and_carries_the_measures(session):
    mine = _execution(session, "u1", age_days=1, seconds=100)
    failed = _execution(session, "u1", status="failed", age_days=2, seconds=50)
    _execution(session, "u2", age_days=1)                   # autre utilisateur
    _execution(session, "u1", age_days=60)                  # hors période
    _agent_run(session, mine, calls=3)
    page = _list(session)
    assert page["total"] == 2 and [i["id"] for i in page["items"]] == [mine.id, failed.id]
    first = page["items"][0]
    assert first["duration_seconds"] == 100.0 and first["llm_calls"] == 3 and first["tokens"] == 40
    assert page["items"][1]["llm_calls"] is None and page["items"][1]["tokens"] is None  # aucune mesure
    assert [i["id"] for i in _list(session, status="failed")["items"]] == [failed.id]


def test_executions_list_sorts_with_missing_values_always_last(session):
    slow = _execution(session, "u1", age_days=1, seconds=300)
    fast = _execution(session, "u1", age_days=2, seconds=20)
    unmeasured = _execution(session, "u1", age_days=3, seconds=60)
    _agent_run(session, slow, calls=9)
    _agent_run(session, fast, calls=2)
    by_calls = [i["id"] for i in _list(session, sort="llm_calls")["items"]]
    assert by_calls == [slow.id, fast.id, unmeasured.id]
    ascending = [i["id"] for i in _list(session, sort="llm_calls", order="asc")["items"]]
    assert ascending == [fast.id, slow.id, unmeasured.id]      # manquante toujours en dernier
    assert [i["id"] for i in _list(session, sort="duration")["items"]] == [slow.id, unmeasured.id, fast.id]


def test_executions_list_paginates_and_clamps_its_limits(session):
    ids = [_execution(session, "u1", age_days=1 + n).id for n in range(5)]
    page = _list(session, limit=2, offset=2)
    assert page["total"] == 5 and [i["id"] for i in page["items"]] == ids[2:4]
    assert len(_list(session, limit=10_000)["items"]) == 5          # borné à 100
    assert _list(session, offset=-5)["items"][0]["id"] == ids[0]   # offset négatif ramené à 0
    assert len(_list(session, sort="n_importe_quoi")["items"]) == 5  # tri inconnu : par date


def test_executions_list_truncates_long_requests(session):
    entry = _execution(session, "u1", age_days=1)
    entry.user_request = "x" * 5000
    session.add(entry)
    session.commit()
    text = _list(session)["items"][0]["user_request"]
    assert len(text) <= 141 and text.endswith("…")


def test_executions_endpoint_rejects_unknown_filter_and_sort_values():
    import inspect
    from typing import get_args
    hints = inspect.signature(routes_metrics.metrics_executions).parameters
    assert set(get_args(hints["order"].annotation)) == {"asc", "desc"}
    assert set(get_args(hints["sort"].annotation)) == {"created_at", "duration", "llm_calls", "tokens"}
    assert "failed" in str(hints["status"].annotation) and "success" in str(hints["status"].annotation)


def test_status_filter_total_counts_only_the_filtered_rows(session):
    _execution(session, "u1", age_days=1)
    _execution(session, "u1", status="failed", age_days=2)
    _execution(session, "u1", status="failed", age_days=3)
    page = _list(session, status="failed")
    assert page["total"] == 2 and all(i["status"] == "failed" for i in page["items"])
