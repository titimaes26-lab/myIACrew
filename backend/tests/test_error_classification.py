"""Bornes de l'attribution d'un arrêt par délai au quota du modèle, et classement des erreurs de délai du fournisseur."""
import pytest

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


# --- 504 DEADLINE_EXCEEDED (le délai posé sur l'appel est écoulé côté Google) --------------------------------------------------------------

DEADLINE_504 = ("504 DEADLINE_EXCEEDED. {'error': {'code': 504, 'message': 'Deadline expired before operation could complete.', "
                "'status': 'DEADLINE_EXCEEDED'}}")


@pytest.mark.parametrize("message", [DEADLINE_504, DEADLINE_504.lower(), "Deadline expired before operation could complete.", "deadline exceeded after 90s",
                                     "DEADLINE_EXCEEDED"])
def test_a_deadline_error_is_a_retryable_model_timeout_not_an_internal_error(message):
    info = classify_exception(RuntimeError(message))
    assert info.code == ErrorCode.LLM_TIMEOUT and info.retryable is True and info.message


def test_a_deadline_error_wrapped_by_crewai_is_still_recognised():
    try:
        try:
            raise RuntimeError(DEADLINE_504)
        except RuntimeError as inner:
            raise ValueError("Agent execution failed") from inner
    except ValueError as wrapped:
        assert classify_exception(wrapped).code == ErrorCode.LLM_TIMEOUT


@pytest.mark.parametrize("message", ["Critère KO : ligne 504 du fichier", "page_504.tsx introuvable", "HTTP 504 Gateway du proxy du dépôt cible"])
def test_a_bare_504_in_a_message_is_not_taken_for_a_model_timeout(message):
    assert classify_exception(RuntimeError(message)).code == ErrorCode.INTERNAL_ERROR


# --- résultat incohérent (garde-fou de crew_result.check_outputs) ----------------------------------------------------------

def test_an_inconsistent_result_has_its_own_code_message_and_is_retryable():
    from errors import ResultIntegrityError, http_status_for
    info = classify_exception(ResultIntegrityError("Résultat incohérent : étape « architecture » sans sortie"))
    assert info.code == ErrorCode.RESULT_INCONSISTENT and info.retryable is True
    assert "incomplet" in info.message and "architecture" not in info.message          # le détail reste dans le journal
    assert http_status_for(ErrorCode.RESULT_INCONSISTENT) == 500


def test_an_inconsistent_result_is_recognised_inside_a_wrapped_exception_chain():
    from errors import ResultIntegrityError
    try:
        try:
            raise ResultIntegrityError("Résultat incohérent : x")
        except ResultIntegrityError as inner:
            raise RuntimeError("enveloppe") from inner
    except RuntimeError as wrapped:
        assert classify_exception(wrapped).code == ErrorCode.RESULT_INCONSISTENT
