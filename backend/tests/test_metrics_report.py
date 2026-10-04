"""Agrégats purs : percentiles, synthèse, jours par fuseau, causes d'échec, coût et verdicts."""
from datetime import datetime, timedelta, timezone


import pytest

import crewquestion  # noqa: E402,F401  (enregistre les listeners d'événements)
import routes_metrics
from metrics_report import percentile, summarize


def test_percentile_interpolates_and_handles_empty():
    assert percentile([], 0.5) is None
    assert percentile([10], 0.95) == 10
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile([0, 100], 0.95) == pytest.approx(95)


def _run(execution_id, agent, duration, calls=2, tokens=100, usage_calls=2, status="completed"):
    return {"execution_id": execution_id, "agent": agent, "status": status, "duration_seconds": duration,
            "llm_calls": calls, "llm_errors": 0, "usage_calls": usage_calls, "prompt_tokens": tokens * 3 // 4,
            "completion_tokens": tokens // 4, "total_tokens": tokens, "tool_calls": 1, "tool_errors": 0}


def test_summarize_computes_percentiles_averages_and_daily_series():
    base = datetime(2026, 10, 1, 10, 0, 0)
    executions = [
        {"id": 1, "status": "success", "created_at": base, "updated_at": base + timedelta(seconds=60),
         "rate_limit_hits": 1, "total_wait_time_seconds": 15.0},
        {"id": 2, "status": "failed", "created_at": base + timedelta(days=1), "updated_at": base + timedelta(days=1, seconds=100),
         "rate_limit_hits": None, "total_wait_time_seconds": None},
    ]
    runs = [_run(1, "design", 10), _run(1, "qa", 20), _run(2, "design", 30, status="incomplete", usage_calls=0, tokens=0)]
    result = summarize(runs, executions, 30, "BUGFIX")
    assert result["executions"]["total"] == 2 and result["executions"]["success"] == 1 and result["executions"]["failed"] == 1
    assert result["executions"]["median_duration_seconds"] == 80.0
    assert result["executions"]["rate_limit_hits"] == 1 and result["executions"]["wait_seconds"] == 15.0
    assert result["executions"]["avg_llm_calls"] == 3.0 and result["executions"]["token_executions"] == 1
    design = next(a for a in result["agents"] if a["agent"] == "design")
    assert design["runs"] == 2 and design["incomplete"] == 1 and design["duration_p50"] == 20.0
    assert design["token_runs"] == 1 and design["avg_prompt_tokens"] == 75.0
    assert [a["agent"] for a in result["agents"]] == ["design", "qa"]
    assert result["daily"] == [
        {"date": "2026-10-01", "executions": 1, "failed": 0, "llm_calls": 4, "tokens": 200, "qa_total": 0, "qa_go": 0, "median_duration_seconds": 60.0},
        {"date": "2026-10-02", "executions": 1, "failed": 1, "llm_calls": 2, "tokens": 0, "qa_total": 0, "qa_go": 0, "median_duration_seconds": 100.0},
    ]
    assert result["workflow"] == "BUGFIX" and result["period_days"] == 30


def test_summarize_without_data_returns_empty_but_well_formed_values():
    result = summarize([], [], 7)
    assert result["executions"]["total"] == 0 and result["executions"]["median_duration_seconds"] is None
    assert result["agents"] == [] and result["daily"] == []
    assert result["executions"]["avg_tokens"] is None


def test_summarize_groups_days_in_the_users_time_zone():
    late = datetime(2026, 10, 1, 23, 30, tzinfo=timezone.utc)
    early = datetime(2026, 10, 2, 1, 0, tzinfo=timezone.utc)
    executions = [
        {"id": 1, "status": "success", "created_at": late, "updated_at": late + timedelta(seconds=30)},
        {"id": 2, "status": "success", "created_at": early, "updated_at": early + timedelta(seconds=30)},
    ]
    assert [d["date"] for d in summarize([], executions, 30)["daily"]] == ["2026-10-01", "2026-10-02"]
    # UTC+2 : 23 h 30 UTC est déjà le lendemain à 01 h 30 locale, les deux exécutions tombent le 2.
    plus_two = summarize([], executions, 30, tz_offset_minutes=120)["daily"]
    assert [(d["date"], d["executions"]) for d in plus_two] == [("2026-10-02", 2)]
    # UTC-5 : 01 h 00 UTC est encore la veille à 20 h locale, les deux tombent le 1er.
    minus_five = summarize([], executions, 30, tz_offset_minutes=-300)["daily"]
    assert [(d["date"], d["executions"]) for d in minus_five] == [("2026-10-01", 2)]


def test_failure_causes_group_sort_and_label_failed_executions_only():
    from metrics_report import failure_causes
    executions = [
        {"status": "failed", "error_code": "QUOTA_EXHAUSTED"},
        {"status": "failed", "error_code": "GUARDRAIL_FAILED"},
        {"status": "failed", "error_code": "QUOTA_EXHAUSTED"},
        {"status": "failed", "error_code": None},
        {"status": "failed", "error_code": "FUTURE_CODE"},
        {"status": "success", "error_code": "QUOTA_EXHAUSTED"},  # jamais compté : pas un échec
    ]
    assert failure_causes(executions) == [
        {"code": "QUOTA_EXHAUSTED", "label": "Quota du modèle épuisé", "count": 2},
        {"code": "FUTURE_CODE", "label": "FUTURE_CODE", "count": 1},
        {"code": "GUARDRAIL_FAILED", "label": "Contrôle de qualité non respecté", "count": 1},
        {"code": "UNCLASSIFIED", "label": "Cause non enregistrée", "count": 1},
    ]
    assert failure_causes([]) == []
    assert failure_causes([{"status": "failed", "error_code": "DELIVERY_FAILED"}])[0]["label"] == "Livraison GitHub non confirmée"


def test_every_code_the_classifier_can_emit_has_a_dashboard_label():
    import asyncio as _asyncio
    from metrics_report import FAILURE_LABELS
    from errors import ErrorCode, classify_exception
    samples = [
        RuntimeError("429 quota"), RuntimeError("503 unavailable"), _asyncio.TimeoutError(),
        RuntimeError("guardrail failed"), RuntimeError("inconnue"),
    ]
    emitted = {classify_exception(exc).code for exc in samples} | {ErrorCode.INTERRUPTED, ErrorCode.GITHUB_UNAVAILABLE}
    assert emitted <= set(FAILURE_LABELS)


def test_daily_median_duration_is_none_for_a_day_without_measurable_duration():
    base = datetime(2026, 10, 1, 10, 0, 0)
    result = summarize([], [{"id": 1, "status": "success", "created_at": base, "updated_at": None,
                             "rate_limit_hits": None, "total_wait_time_seconds": None}], 30)
    assert result["daily"][0]["median_duration_seconds"] is None


def test_final_verdict_takes_the_last_mention_and_none_when_absent():
    from qa_report import final_verdict
    assert final_verdict("Verdict : GO") == "GO"
    assert final_verdict("Verdict : GO\\n\\n... corrigé ...\\nVerdict : NO_GO") == "NO_GO"
    assert final_verdict("Verdict : GO_AVEC_RESERVES") == "GO_AVEC_RESERVES"
    assert final_verdict("aucun rapport") is None and final_verdict("") is None


def test_execution_cost_needs_a_configured_price():
    from metrics_report import execution_cost
    assert execution_cost(1_000_000, 500_000, (2.0, 8.0)) == 6.0
    assert execution_cost(1000, 1000, None) is None
    assert execution_cost(1000, 1000, (0.0, 0.0)) is None


def test_token_prices_are_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("TOKEN_PRICE_INPUT_PER_MILLION", raising=False)
    monkeypatch.delenv("TOKEN_PRICE_OUTPUT_PER_MILLION", raising=False)
    assert routes_metrics._token_prices() is None
    monkeypatch.setenv("TOKEN_PRICE_INPUT_PER_MILLION", "0.5")
    monkeypatch.setenv("TOKEN_PRICE_OUTPUT_PER_MILLION", "2")
    assert routes_metrics._token_prices() == (0.5, 2.0)
    monkeypatch.setenv("TOKEN_PRICE_INPUT_PER_MILLION", "abc")
    assert routes_metrics._token_prices() is None
