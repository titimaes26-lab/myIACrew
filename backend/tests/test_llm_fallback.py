"""Modèle de repli et pause sur 503 (Gemini surchargé)."""
import asyncio
import logging

import pytest

import llm_fallback
import llm_limiter
from llm_limiter import GeminiRateLimiter, install
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


# --- détection ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("error, expected", [
    (CodedError(503), True), (CodedError(500), True), (CodedError(502), True), (CodedError(504, "DEADLINE_EXCEEDED"), False),
    (CodedError(429, "RESOURCE_EXHAUSTED"), False), (RuntimeError(OVERLOAD), True), (RuntimeError("model overloaded"), True),
    (RuntimeError("ligne 503 du rapport"), False), (RuntimeError("504 DEADLINE_EXCEEDED"), False), (ValueError("autre"), False),
])
def test_unavailable_errors_are_recognised_by_code_or_text(error, expected):
    assert is_unavailable_error(error) is expected


# --- compteur, seuil, durée ---------------------------------------------------------------------------------------

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


# --- dans les appels ----------------------------------------------------------------------------------------------

class _Response:
    usage_metadata = None


class FakeModels:
    seen: list = []
    failing: set = set()
    message = OVERLOAD

    def generate_content(self, *, model, contents, config=None):
        self.seen.append(model)
        if model in self.failing:
            raise RuntimeError(self.message)
        return _Response()


class FakeAsyncModels:
    async def generate_content(self, *, model, contents, config=None):
        FakeModels.seen.append(model)
        if model in FakeModels.failing:
            raise RuntimeError(OVERLOAD)
        return _Response()


@pytest.fixture()
def wrapped(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(llm_limiter.time, "sleep", slept.append)
    monkeypatch.setattr(llm_limiter, "_installed", set())
    monkeypatch.setattr(llm_limiter, "limiter", GeminiRateLimiter(100, 60))
    FakeModels.seen, FakeModels.failing, FakeModels.message = [], {FLASH}, OVERLOAD
    targets = [(FakeModels, "generate_content", "sync"), (FakeAsyncModels, "generate_content", "async")]
    originals = {(cls, name): getattr(cls, name) for cls, name, _ in targets}
    install(targets=targets)
    yield slept
    for (cls, name), original in originals.items():
        setattr(cls, name, original)


def test_the_second_overload_is_replayed_on_the_fallback_and_the_next_calls_go_straight_there(wrapped):
    models = FakeModels()
    with pytest.raises(RuntimeError):
        models.generate_content(model=FLASH, contents="x")                      # 1er 503 : remonte (CrewAI réessaiera)
    assert models.generate_content(model=FLASH, contents="x")                   # 2e 503 : rejoué sur le repli et servi
    assert FakeModels.seen == [FLASH, FLASH, LITE]
    models.generate_content(model=FLASH, contents="x")
    assert FakeModels.seen[-1] == LITE                                          # repli actif : plus d'appel au modèle surchargé


def test_the_first_overload_pauses_the_model_for_the_next_calls(wrapped):
    models = FakeModels()
    with pytest.raises(RuntimeError):
        models.generate_content(model=FLASH, contents="x")
    wait, _ = llm_limiter.limiter_for(FLASH).reserve_ticket(tokens=0)
    assert 25 <= wait <= 31                                                     # LLM_UNAVAILABLE_PAUSE_S = 30 par défaut


def test_other_errors_are_never_replayed_and_do_not_count(wrapped):
    FakeModels.failing = set()
    models = FakeModels()
    models.generate_content(model=FLASH, contents="x")
    assert llm_fallback.note_unavailable(FLASH, now=0) is False                 # le succès a remis le compteur à zéro
    assert FakeModels.seen == [FLASH]


def test_async_calls_get_the_same_fallback(wrapped, monkeypatch):
    monkeypatch.setenv("LLM_UNAVAILABLE_PAUSE_S", "0")                          # sinon le 2e appel attendrait VRAIMENT 30 s (asyncio.sleep)
    async def scenario():
        models = FakeAsyncModels()
        with pytest.raises(RuntimeError):
            await models.generate_content(model=FLASH, contents="x")
        return await models.generate_content(model=FLASH, contents="x")
    assert asyncio.run(scenario())
    assert FakeModels.seen == [FLASH, FLASH, LITE]


def test_with_the_fallback_disabled_the_overload_always_reaches_the_caller(wrapped, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "off")
    models = FakeModels()
    for _ in range(3):
        with pytest.raises(RuntimeError):
            models.generate_content(model=FLASH, contents="x")
    assert FakeModels.seen == [FLASH] * 3


def test_the_unavailable_pause_is_configurable_and_bounded(monkeypatch):
    assert llm_limiter._unavailable_pause() == 30.0
    for raw, expected in (("0", 0.0), ("5", 5.0), ("300", 300.0), ("301", 30.0), ("-1", 30.0), ("abc", 30.0)):
        monkeypatch.setenv("LLM_UNAVAILABLE_PAUSE_S", raw)
        assert llm_limiter._unavailable_pause() == expected


def test_a_successful_call_between_two_overloads_resets_the_count(wrapped, monkeypatch):
    monkeypatch.setenv("LLM_UNAVAILABLE_PAUSE_S", "0")
    models = FakeModels()
    with pytest.raises(RuntimeError):
        models.generate_content(model=FLASH, contents="x")
    FakeModels.failing = set()
    models.generate_content(model=FLASH, contents="x")                          # succès : compteur à zéro
    FakeModels.failing = {FLASH}
    with pytest.raises(RuntimeError):
        models.generate_content(model=FLASH, contents="x")                      # 1er 503 d'une nouvelle série : pas de repli
    assert FakeModels.seen == [FLASH, FLASH, FLASH]


def test_a_model_unknown_to_the_limits_table_never_gets_a_fallback(wrapped):
    FakeModels.failing = {"gemini-inconnu"}
    models = FakeModels()
    for _ in range(3):
        with pytest.raises(RuntimeError):
            models.generate_content(model="gemini-inconnu", contents="x")
    assert FakeModels.seen == ["gemini-inconnu"] * 3


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


# --- délai dépassé (504 DEADLINE_EXCEEDED) : le repli démarre dès le premier -------------------------------------------

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


def test_a_504_replays_the_failing_call_on_the_fallback_at_once_without_any_pause(wrapped):
    FakeModels.message = DEADLINE
    models = FakeModels()
    assert models.generate_content(model=FLASH, contents="x")                  # servi par le repli dès le premier 504
    assert FakeModels.seen == [FLASH, LITE]
    models.generate_content(model=FLASH, contents="x")
    assert FakeModels.seen[-1] == LITE
    wait, _ = llm_limiter.limiter_for(FLASH).reserve_ticket(tokens=0)
    assert wait == 0                                                           # pas de pause de 30 s : le modèle ne répond pas, attendre ne sert à rien


def test_a_504_never_triggers_the_fallback_when_it_is_disabled(wrapped, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "off")
    FakeModels.message = DEADLINE
    with pytest.raises(RuntimeError):
        FakeModels().generate_content(model=FLASH, contents="x")
    assert FakeModels.seen == [FLASH]
