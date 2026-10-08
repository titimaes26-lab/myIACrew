"""Cas limites de metrics_report : dates naïves et avec fuseau, durée inconnue."""
from datetime import datetime, timedelta, timezone


import metrics_report

UTC = timezone.utc


def _execution(id, created_at, updated_at=None):
    return {"id": id, "created_at": created_at, "updated_at": updated_at, "workflow": "BUGFIX", "status": "success", "user_request": "x"}


def test_naive_dates_are_read_as_utc_and_aware_dates_keep_their_own_offset():
    since = datetime(2026, 1, 1, 13, 0, tzinfo=UTC)
    naive_after = _execution(1, datetime(2026, 1, 1, 14, 0))                                   # naïve = UTC : 14h UTC, après
    naive_before = _execution(2, datetime(2026, 1, 1, 12, 0))
    plus_two = _execution(3, datetime(2026, 1, 1, 14, 0, tzinfo=timezone(timedelta(hours=2))))  # 12h UTC : avant, pas 14h
    current, previous = metrics_report.split_by_period([naive_after, naive_before, plus_two], since)
    assert [e["id"] for e in current] == [1] and [e["id"] for e in previous] == [2, 3]


def test_the_duration_is_unknown_unless_both_dates_are_known():
    start = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    rows = metrics_report.list_executions(
        [_execution(1, start, start + timedelta(seconds=90)), _execution(2, start, None)], {}
    )["items"]
    by_id = {row["id"]: row for row in rows}
    assert by_id[1]["duration_seconds"] == 90 and by_id[2]["duration_seconds"] is None
