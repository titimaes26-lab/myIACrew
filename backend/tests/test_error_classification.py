"""Bornes de l'attribution d'un arrêt par délai au quota du modèle."""
from errors import QUOTA_DOMINANT_SHARE, ErrorCode, ExecutionTimeoutError, classify_exception


def test_the_quota_is_dominant_from_exactly_its_share_of_the_time_limit():
    limit = 100.0
    exactly = ExecutionTimeoutError("t", quota_wait_seconds=QUOTA_DOMINANT_SHARE * limit, limit_seconds=limit)
    below = ExecutionTimeoutError("t", quota_wait_seconds=QUOTA_DOMINANT_SHARE * limit - 0.1, limit_seconds=limit)
    assert exactly.quota_dominant and not below.quota_dominant
    assert not ExecutionTimeoutError("t", quota_wait_seconds=50.0, limit_seconds=0.0).quota_dominant


def test_a_timeout_is_blamed_on_the_quota_only_when_it_was_waited_for_and_dominant():
    dominant = ExecutionTimeoutError("t", quota_wait_seconds=60.0, limit_seconds=100.0)
    quota_message = classify_exception(dominant).message
    generic = classify_exception(ExecutionTimeoutError("t", quota_wait_seconds=1.0, limit_seconds=100.0))
    assert generic.code == ErrorCode.EXECUTION_TIMEOUT and generic.message != quota_message
    forced = ExecutionTimeoutError("t", quota_wait_seconds=0.0, limit_seconds=100.0)
    forced.quota_dominant = True        # état incohérent : aucune attente de quota => message générique
    assert classify_exception(forced).message == generic.message
