"""Modèle de repli sur indisponibilité (Gemini surchargé) : détection des erreurs, compteur, durée, réglages (module pur).
Les scénarios « dans les appels » (wrapper du limiteur, rejeu sur le repli) sont dans test_llm_fallback_calls.py."""
import logging

import pytest

import llm_fallback
from llm_limiter import GeminiRateLimiter
from llm_quota_errors import is_unavailable_error


FLASH, LITE = "gemini-3.8-flash", "gemini-3.5-flash-lite"


OVERLOAD = "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand.', 'status': 'UNAVAILABLE'}}"


class CodedError(Exception):
    def __init__(self, code, message=""):
        super().__init__(message)
        self.code = code


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEMINI_MODEL", "GEMINI_MODEL_LIMITS", "LLM_RATE_MARGIN", "GEMINI_FALLBACK_MODEL", "LLM_FALLBACK_TTL_S", "LLM_UNAVAILABLE_PAUSE_S"):
        monkeypatch.delenv(name, raising=False)
    # Les scénarios de ce fichier décrivent le repli « à la deuxième erreur » : réglage EXPLICITE (le défaut est 1, voir la fin du fichier).
    monkeypatch.setenv("LLM_FALLBACK_THRESHOLD", "2")


@pytest.mark.parametrize("error, expected", [
    (CodedError(503), True), (CodedError(500), True), (CodedError(502), True), (CodedError(504, "DEADLINE_EXCEEDED"), False),
    (CodedError(429, "RESOURCE_EXHAUSTED"), False), (RuntimeError(OVERLOAD), True), (RuntimeError("model overloaded"), True),
    (RuntimeError("ligne 503 du rapport"), False), (RuntimeError("504 DEADLINE_EXCEEDED"), False), (ValueError("autre"), False),
])
def test_unavailable_errors_are_recognised_by_code_or_text(error, expected):
    assert is_unavailable_error(error) is expected


def test_the_fallback_starts_at_the_second_consecutive_unavailable_error_and_ends_after_its_ttl():
    assert llm_fallback.note_unavailable(FLASH, now=0) is False
    assert llm_fallback.replacement_for(FLASH, now=1) is None
    assert llm_fallback.note_unavailable(FLASH, now=2) is True
    assert llm_fallback.replacement_for(FLASH, now=3) == LITE
    assert llm_fallback.replacement_for(FLASH, now=601) == LITE
    assert llm_fallback.replacement_for(FLASH, now=602.1) is None            # 600 s après le déclenchement : le principal est réessayé


def test_a_success_between_two_errors_resets_the_count():
    llm_fallback.note_unavailable(FLASH, now=0)
    llm_fallback.note_success(FLASH)
    assert llm_fallback.note_unavailable(FLASH, now=1) is False
    assert llm_fallback.replacement_for(FLASH, now=2) is None


def test_the_fallback_is_per_model_and_never_replaces_the_fallback_by_itself():
    llm_fallback.note_unavailable(FLASH, now=0)
    llm_fallback.note_unavailable(FLASH, now=1)
    assert llm_fallback.replacement_for(LITE, now=2) is None
    assert llm_fallback.note_unavailable(LITE, now=3) is False and llm_fallback.note_unavailable(LITE, now=4) is False


@pytest.mark.parametrize("raw", ["off", "OFF", "none", "", "  "])
def test_the_fallback_can_be_disabled(monkeypatch, raw):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", raw)
    assert llm_fallback.fallback_model() is None
    assert llm_fallback.note_unavailable(FLASH, now=0) is False and llm_fallback.note_unavailable(FLASH, now=1) is False
    assert llm_fallback.replacement_for(FLASH, now=2) is None


def test_the_fallback_model_and_ttl_are_configurable(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini-x")
    monkeypatch.setenv("LLM_FALLBACK_TTL_S", "30")
    llm_fallback.note_unavailable(FLASH, now=0)
    llm_fallback.note_unavailable(FLASH, now=1)
    assert llm_fallback.replacement_for(FLASH, now=2) == "gemini-x" and llm_fallback.replacement_for(FLASH, now=32) is None
    monkeypatch.setenv("LLM_FALLBACK_TTL_S", "abc")
    assert llm_fallback._ttl() == llm_fallback.DEFAULT_TTL_S


def test_switching_to_the_fallback_is_logged_without_any_text(caplog):
    with caplog.at_level(logging.WARNING):
        llm_fallback.note_unavailable(FLASH, now=0)
        llm_fallback.note_unavailable(FLASH, now=1)
    (message,) = [r.getMessage() for r in caplog.records if "repli" in r.getMessage()]
    assert FLASH in message and LITE in message


def test_the_unavailable_pause_is_configurable_and_bounded(monkeypatch):
    assert llm_fallback.unavailable_pause() == 30.0
    for raw, expected in (("0", 0.0), ("5", 5.0), ("300", 300.0), ("301", 30.0), ("-1", 30.0), ("abc", 30.0)):
        monkeypatch.setenv("LLM_UNAVAILABLE_PAUSE_S", raw)
        assert llm_fallback.unavailable_pause() == expected


def test_an_active_fallback_never_redirects_the_fallback_model_itself():
    llm_fallback._active_until[LITE] = 10_000.0
    assert llm_fallback.replacement_for(LITE, now=1) is None


def test_the_wait_reason_names_the_cause_of_the_pause_429_or_503():
    after_503 = GeminiRateLimiter(100, 60)
    after_503.note_unavailable(30, now=0)
    assert after_503.reserve_ticket(now=1)[1][2] == "pause après un 503 (modèle surchargé)"
    after_429 = GeminiRateLimiter(100, 60)
    after_429.note_rate_limited(RuntimeError("429 retry in 30s"), now=0)
    assert after_429.reserve_ticket(now=1)[1][2] == "pause après un 429"
    after_503.note_rate_limited(RuntimeError("429 retry in 60s"), now=2)           # une pause 429 plus longue reprend la main
    assert after_503.reserve_ticket(now=3)[1][2] == "pause après un 429"


DEADLINE = "504 DEADLINE_EXCEEDED. {'error': {'code': 504, 'message': 'Deadline expired before operation could complete.', 'status': 'DEADLINE_EXCEEDED'}}"


@pytest.mark.parametrize("error, expected", [
    (CodedError(504), True), (RuntimeError(DEADLINE), True), (RuntimeError("deadline exceeded"), True),
    (CodedError(503), False), (RuntimeError("ligne 504 du rapport"), False), (ValueError("autre"), False),
])
def test_deadline_errors_are_recognised_by_code_or_text(error, expected):
    from llm_quota_errors import is_deadline_error
    assert is_deadline_error(error) is expected


def test_an_immediate_deadline_starts_the_fallback_at_the_first_error_and_says_why(caplog):
    with caplog.at_level(logging.WARNING):
        assert llm_fallback.note_unavailable(FLASH, now=0, immediate=True) is True
    assert llm_fallback.replacement_for(FLASH, now=1) == LITE
    assert "délai dépassé" in caplog.text


@pytest.mark.parametrize("raw", ["gemini/gemini-3.5-flash-lite", "Models/Gemini-3.5-Flash-Lite", "  gemini-3.5-flash-lite  ", "GEMINI/GEMINI-3.5-FLASH-LITE"])
def test_the_fallback_model_is_normalised_without_provider_prefix_or_case(monkeypatch, raw):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", raw)
    assert llm_fallback.fallback_model() == LITE


def test_a_prefixed_fallback_equal_to_the_requested_model_never_redirects_a_model_to_itself(monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini/gemini-3.5-flash-lite")
    assert llm_fallback.note_unavailable(LITE, now=0) is False and llm_fallback.note_unavailable(LITE, now=1) is False
    llm_fallback._active_until[LITE] = 10_000.0
    assert llm_fallback.replacement_for(LITE, now=1) is None


@pytest.mark.parametrize("raw", ["gemini/", "models/", "GEMINI/"])
def test_a_fallback_made_only_of_a_prefix_disables_the_fallback(monkeypatch, raw):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", raw)
    assert llm_fallback.fallback_model() is None


@pytest.mark.parametrize("raw, expected", [("", 1), ("1", 1), ("2", 2), ("5", 5), ("0", 1), ("6", 1), ("-1", 1), ("abc", 1), ("1.5", 1)])
def test_the_fallback_threshold_defaults_to_one_and_is_bounded(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_FALLBACK_THRESHOLD", raw)
    assert llm_fallback.threshold() == expected


def test_by_default_the_first_unavailable_error_starts_the_fallback_and_says_so(monkeypatch, caplog):
    monkeypatch.delenv("LLM_FALLBACK_THRESHOLD", raising=False)
    with caplog.at_level(logging.WARNING):
        assert llm_fallback.note_unavailable(FLASH, now=0) is True
    assert llm_fallback.replacement_for(FLASH, now=1) == LITE
    assert "première erreur" in caplog.text and LITE in caplog.text


def test_a_deadline_starts_the_fallback_at_once_whatever_the_raised_threshold(monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_THRESHOLD", "5")
    assert llm_fallback.note_unavailable(FLASH, now=0) is False                  # un 503 isolé : pas encore
    assert llm_fallback.note_unavailable(FLASH, now=1, immediate=True) is True   # un délai dépassé coûte tout le délai : repli tout de suite
