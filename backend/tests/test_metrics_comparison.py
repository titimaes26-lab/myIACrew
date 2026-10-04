"""Synthèse du tableau de bord : comparaison à la période précédente, exactitude au-delà de 1 000 exécutions, coût et qualité."""
# ruff: noqa: F811  (la fixture `session` importée de metrics_support est reprise comme argument des tests)
import os
import sys
from datetime import timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import routes_metrics  # noqa: E402
from database import ExecutionHistory  # noqa: E402
from metrics_support import DESIGNER, _agent_run, _execution, session  # noqa: E402,F401


def _summary(session, days=30, workflow=None, user="u1"):
    return routes_metrics.metrics_summary(days=days, workflow=workflow, tz_offset=0, session=session, user={"id": user})


def test_summary_compares_with_the_previous_period_of_the_same_length(session):
    _execution(session, "u1", status="success", age_days=5, seconds=100)
    _execution(session, "u1", status="failed", age_days=10, seconds=300)
    _execution(session, "u1", status="success", age_days=40, seconds=50)   # période précédente
    _execution(session, "u1", status="success", age_days=45, seconds=70)   # période précédente
    _execution(session, "u1", status="failed", age_days=90, seconds=10)    # trop ancienne : hors des deux
    result = _summary(session)
    assert result["executions"]["total"] == 2 and result["executions"]["median_duration_seconds"] == 200.0
    previous = result["previous"]
    assert previous["total"] == 2 and previous["success"] == 2 and previous["failed"] == 0
    assert previous["median_duration_seconds"] == 60.0


def test_summary_has_no_comparison_without_a_previous_period(session):
    _execution(session, "u1", age_days=2)
    assert _summary(session)["previous"] is None


def test_previous_period_is_scoped_to_the_user_and_the_workflow(session):
    _execution(session, "u1", workflow="BUGFIX", age_days=2)
    _execution(session, "u1", workflow="BUGFIX", age_days=40)
    _execution(session, "u1", workflow="FEATURE", age_days=40)
    _execution(session, "u2", workflow="BUGFIX", age_days=40)
    assert _summary(session)["previous"]["total"] == 2           # BUGFIX + FEATURE de u1
    assert _summary(session, workflow="BUGFIX")["previous"]["total"] == 1


def test_no_comparison_when_the_limit_cuts_the_previous_period(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 3)
    for age in (1, 2, 40, 41):
        _execution(session, "u1", age_days=age)
    result = _summary(session)
    assert result["previous"] is None            # 3 lignes lues : la période précédente est incomplète
    assert result["truncated"] is False          # …mais la période courante, elle, est complète
    assert result["comparison_limited"] is True  # et l'interface peut dire POURQUOI il n'y a pas de comparaison
    assert result["executions"]["total"] == 2


def test_summary_is_exact_beyond_the_old_thousand_executions_limit(session):
    assert routes_metrics._METRICS_EXECUTION_LIMIT > 1000
    from datetime import datetime as dt
    stamp = dt.now(timezone.utc) - timedelta(days=3)
    session.add_all([
        ExecutionHistory(user_request="r", workflow="BUGFIX", status="success", user_id="u1",
                         created_at=stamp, updated_at=stamp + timedelta(seconds=60))
        for _ in range(1200)
    ])
    session.commit()
    result = _summary(session)
    assert result["executions"]["total"] == 1200 and result["truncated"] is False
    assert result["daily"][0]["executions"] == 1200


def test_comparison_limited_is_false_when_nothing_is_cut(session):
    _execution(session, "u1", age_days=2)
    _execution(session, "u1", age_days=40)
    result = _summary(session)
    assert result["comparison_limited"] is False and result["previous"] is not None


def test_current_period_cut_by_the_limit_is_reported_as_truncated(session, monkeypatch):
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 2)
    for age in (1, 2, 3):
        _execution(session, "u1", age_days=age)
    result = _summary(session)
    assert result["truncated"] is True and result["previous"] is None and result["comparison_limited"] is False


def test_measures_of_the_discarded_previous_period_are_not_loaded(session, monkeypatch):
    from sqlalchemy import event
    monkeypatch.setattr(routes_metrics, "_METRICS_EXECUTION_LIMIT", 3)
    monkeypatch.setattr(routes_metrics, "_IN_CLAUSE_CHUNK", 1)  # une requête de mesures par exécution chargée
    for age in (1, 2, 40, 41):
        _agent_run(session, _execution(session, "u1", age_days=age))
    queries = []
    engine = session.get_bind()

    def count(conn, cursor, statement, *args):
        if "FROM agentrun" in statement:
            queries.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        _summary(session)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(queries) == 2  # seulement les 2 exécutions de la période courante, pas les 3 lues


def test_summary_counts_reasoning_quality_and_cost(session, monkeypatch):
    monkeypatch.setenv("TOKEN_PRICE_INPUT_PER_MILLION", "1")
    monkeypatch.setenv("TOKEN_PRICE_OUTPUT_PER_MILLION", "4")
    monkeypatch.setenv("COST_CURRENCY", "€")
    retried = _execution(session, "u1", age_days=1)
    retried.attempts, retried.qa_verdict = 2, "GO"
    resumed = _execution(session, "u1", age_days=2)
    resumed.reused_steps, resumed.qa_verdict = 2, "NO_GO"
    plain = _execution(session, "u1", age_days=3)
    plain.attempts, plain.qa_verdict = 1, "GO_AVEC_RESERVES"
    for entry in (retried, resumed, plain):
        session.add(entry)
    session.commit()
    _agent_run(session, retried)  # 30 tokens d'entrée, 10 de sortie
    result = _summary(session)
    e = result["executions"]
    assert e["auto_retried"] == 1 and e["resumed"] == 1
    assert e["qa_verdicts"] == {"GO": 1, "GO_AVEC_RESERVES": 1, "NO_GO": 1}
    assert e["total_cost"] == pytest.approx(0.00007, abs=1e-6)  # 30 × 1/1M + 10 × 4/1M
    assert result["currency"] == "€"
    assert result["daily"][-1]["qa_total"] >= 1


def test_cost_is_hidden_without_a_price_or_without_known_tokens(session, monkeypatch):
    monkeypatch.delenv("TOKEN_PRICE_INPUT_PER_MILLION", raising=False)
    monkeypatch.delenv("TOKEN_PRICE_OUTPUT_PER_MILLION", raising=False)
    _agent_run(session, _execution(session, "u1", age_days=1))
    result = _summary(session)
    assert result["executions"]["total_cost"] is None and result["currency"] is None
