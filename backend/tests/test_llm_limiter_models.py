"""Limiteurs de débit par modèle : RPM, TPM et RPD indépendants d'un modèle à l'autre."""
import asyncio
from datetime import date

import pytest

import crew_retry
import llm_limiter
import llm_models
import llm_usage
from errors import ErrorCode, classify_exception
from llm_limiter import DailyQuotaExceeded, GeminiRateLimiter, install, limiter_for

FLASH, LITE = "gemini-3.8-flash", "gemini-3.5-flash-lite"
QUOTA_TEXT = "429 RESOURCE_EXHAUSTED. Please retry in 30s."


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEMINI_MODEL", "GEMINI_MODEL_LIMITS", "LLM_RATE_MARGIN"):
        monkeypatch.delenv(name, raising=False)


# --- le limiteur seul ---------------------------------------------------------------------------------------------

def test_the_token_budget_delays_a_request_until_enough_tokens_leave_the_window():
    limiter = GeminiRateLimiter(100, 60, max_tokens_per_minute=1000)
    assert limiter.reserve(now=0, tokens=600) == 0
    assert limiter.reserve(now=10, tokens=300) == 0                      # 900 / 1000
    assert limiter.reserve(now=20, tokens=200) == pytest.approx(40)      # attend la sortie des 600 tokens du temps 0


def test_the_token_window_never_holds_more_than_the_budget():
    limiter = GeminiRateLimiter(100, 60, max_tokens_per_minute=1000)
    sizes = [400, 400, 400, 300, 700, 250, 900, 100]
    starts = []
    for index, size in enumerate(sizes):
        now = index * 2.0
        starts.append((now + limiter.reserve(now=now, tokens=size), size))
    for start, _ in starts:
        assert sum(size for other, size in starts if start - 60 + 1e-6 < other <= start) <= 1000


def test_a_request_bigger_than_the_whole_budget_is_capped_instead_of_waiting_forever():
    limiter = GeminiRateLimiter(100, 60, max_tokens_per_minute=1000)
    assert limiter.reserve(now=0, tokens=5000) == 0
    assert limiter.reserve(now=0, tokens=1) == pytest.approx(60)


def test_settle_replaces_the_estimate_by_the_real_consumption():
    limiter = GeminiRateLimiter(100, 60, max_tokens_per_minute=1000)
    wait, ticket = limiter.reserve_ticket(now=0, tokens=900)
    assert wait == 0
    limiter.settle(ticket, 100)                                          # la réponse a coûté bien moins que prévu
    assert limiter.reserve(now=1, tokens=800) == 0
    wait, ticket = limiter.reserve_ticket(now=2, tokens=100)
    limiter.settle(ticket, None)                                         # usage inconnu : l'estimation reste
    assert ticket[1] == 100


def test_requests_per_minute_and_tokens_per_minute_apply_together():
    limiter = GeminiRateLimiter(2, 60, max_tokens_per_minute=1000)
    assert [limiter.reserve(now=0, tokens=100) for _ in range(2)] == [0, 0]
    assert limiter.reserve(now=0, tokens=100) == pytest.approx(60)       # RPM plein malgré un budget de tokens libre


def test_the_daily_cap_raises_instead_of_waiting_and_resets_the_next_day():
    today = [date(2026, 10, 12)]
    limiter = GeminiRateLimiter(100, 60, max_per_day=3, name=FLASH, day_fn=lambda: today[0])
    for _ in range(3):
        limiter.reserve(now=0)
    with pytest.raises(DailyQuotaExceeded) as error:
        limiter.reserve(now=0)
    assert FLASH in str(error.value) and "3" in str(error.value)
    today[0] = date(2026, 10, 13)
    assert limiter.reserve(now=1) == 0                                   # nouveau jour : compteur remis à zéro


def test_the_daily_error_is_a_final_quota_error_that_the_retries_do_not_replay():
    limiter = GeminiRateLimiter(1, max_per_day=1, name=LITE, day_fn=lambda: date(2026, 1, 1))
    limiter.reserve(now=0)
    with pytest.raises(DailyQuotaExceeded) as raised:
        limiter.reserve(now=0)
    info = classify_exception(raised.value)
    assert (info.code, info.retryable) == (ErrorCode.QUOTA_EXHAUSTED, False) and "journalier" in info.message
    assert not crew_retry._is_retryable_error(str(raised.value).lower())     # aucune des marques de quota ou de surcharge
    assert classify_exception(RuntimeError("autre")).code == ErrorCode.INTERNAL_ERROR


def test_no_daily_or_token_cap_by_default():
    limiter = GeminiRateLimiter(10_000, 60)
    for _ in range(50):
        assert limiter.reserve(now=0, tokens=10**9) == 0


# --- le registre par modèle ---------------------------------------------------------------------------------------

def test_each_model_gets_its_own_limiter_with_its_effective_limits():
    flash, lite = limiter_for("gemini/" + FLASH), limiter_for(LITE)
    assert (flash.max_per_minute, flash.max_tokens_per_minute, flash.max_per_day) == (8, 200_000, 1_200)
    assert (lite.max_per_minute, lite.max_tokens_per_minute, lite.max_per_day) == (12, 200_000, 800)
    assert limiter_for(FLASH) is flash and limiter_for("models/" + FLASH) is flash   # même instance, quel que soit le préfixe
    assert flash is not lite


def test_unknown_or_missing_models_use_the_default_requests_only_limiter():
    assert limiter_for("gemini-unknown") is llm_limiter.limiter and limiter_for(None) is llm_limiter.limiter
    assert llm_limiter.limiter.max_tokens_per_minute is None and llm_limiter.limiter.max_per_day is None


def test_a_busy_model_does_not_delay_the_other():
    flash, lite = limiter_for(FLASH), limiter_for(LITE)
    assert [flash.reserve(now=0) for _ in range(8)] == [0] * 8
    assert flash.reserve(now=0) == pytest.approx(60)                     # 9e requête : attend
    assert [lite.reserve(now=0) for _ in range(12)] == [0] * 12          # l'autre modèle n'est pas touché
    assert lite.reserve(now=0) == pytest.approx(60)


def test_a_429_pauses_only_the_model_that_was_limited():
    llm_limiter._note_error(RuntimeError(QUOTA_TEXT), limiter_for(FLASH))
    assert limiter_for(FLASH).reserve() > 25
    assert limiter_for(LITE).reserve() == 0


def test_reset_forgets_the_windows_and_the_daily_counters():
    for _ in range(8):
        limiter_for(FLASH).reserve(now=0)
    llm_limiter.reset_model_limiters()
    assert limiter_for(FLASH).reserve(now=0) == 0


# --- les enveloppes du client google-genai --------------------------------------------------------------------------

class _Usage:
    def __init__(self, total):
        self.total_token_count = total


class _Response:
    def __init__(self, total=None):
        self.usage_metadata = _Usage(total) if total is not None else None


class FakeModels:
    total = 50
    fail = None
    seen: list = []

    def generate_content(self, *, model, contents, config=None):
        self.seen.append(model)
        if self.fail:
            raise self.fail
        return _Response(self.total)

    def generate_content_stream(self, *, model, contents, config=None):
        self.seen.append(model)
        yield _Response(None)
        yield _Response(self.total)


class FakeAsyncModels:
    async def generate_content(self, *, model, contents, config=None):
        return _Response(70_000)


@pytest.fixture()
def wrapped(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(llm_limiter.time, "sleep", slept.append)
    monkeypatch.setattr(llm_limiter, "_installed", set())
    monkeypatch.setattr(llm_limiter, "limiter", GeminiRateLimiter(100, 60))
    FakeModels.seen, FakeModels.total, FakeModels.fail = [], 50, None
    targets = [(FakeModels, "generate_content", "sync"), (FakeModels, "generate_content_stream", "stream"),
               (FakeAsyncModels, "generate_content", "async")]
    originals = {(cls, name): getattr(cls, name) for cls, name, _ in targets}
    waits: list[float] = []
    install(on_wait=waits.append, targets=targets)
    yield slept, waits
    for (cls, name), original in originals.items():
        setattr(cls, name, original)


def test_every_call_is_throttled_by_the_limiter_of_its_own_model(wrapped):
    slept, waits = wrapped
    models = FakeModels()
    for _ in range(8):
        models.generate_content(model="gemini-3.8-flash", contents="x")
    for _ in range(12):
        models.generate_content(model="gemini-3.5-flash-lite", contents="x")
    assert slept == []                                                   # 8 sur 8 et 12 sur 12 : aucun n'attend
    models.generate_content(model="gemini-3.8-flash", contents="x")
    assert len(slept) == 1 and 0 < slept[0] <= 60 and waits == slept     # 9e du premier modèle seulement


def test_real_token_usage_makes_the_next_call_wait(wrapped):
    slept, _ = wrapped
    models = FakeModels()
    models.total = 190_000                                               # la réponse a consommé presque tout le budget (200 000)
    models.generate_content(model=FLASH, contents="court")
    assert slept == []
    models.generate_content(model=FLASH, contents="y" * 80_000)          # ~20 000 tokens estimés : dépasse 200 000 au total
    assert len(slept) == 1 and slept[0] > 50


def test_the_prompt_estimate_counts_when_the_response_has_no_usage(wrapped):
    slept, _ = wrapped
    models = FakeModels()
    models.total = None
    for _ in range(3):
        models.generate_content(model=FLASH, contents="z" * 320_000)     # ~80 000 tokens estimés chacun
    assert len(slept) == 1                                               # la 3e dépasse 200 000 : elle attend


def test_streams_settle_with_the_last_reported_usage(wrapped):
    slept, _ = wrapped
    models = FakeModels()
    models.total = 195_000
    assert len(list(models.generate_content_stream(model=FLASH, contents="x"))) == 2
    models.generate_content(model=FLASH, contents="y" * 80_000)
    assert len(slept) == 1


def test_async_calls_use_the_model_limiter_and_report_usage(wrapped):
    slept, _ = wrapped
    asyncio.run(FakeAsyncModels().generate_content(model=FLASH, contents="x"))
    assert limiter_for(FLASH)._entries[-1][1] == 70_000


def test_the_daily_cap_stops_the_call_before_it_is_sent(wrapped, monkeypatch):
    models = FakeModels()
    monkeypatch.setenv("GEMINI_MODEL_LIMITS", '{"gemini-tiny": {"rpm": 5, "tpm": 1000, "rpd": 2}}')
    monkeypatch.setenv("LLM_RATE_MARGIN", "1")
    models.generate_content(model="gemini/gemini-tiny", contents="x")
    models.generate_content(model="gemini-tiny", contents="x")
    with pytest.raises(DailyQuotaExceeded):
        models.generate_content(model="gemini-tiny", contents="x")
    assert models.seen == ["gemini/gemini-tiny", "gemini-tiny"]          # la 3e requête n'est jamais partie


def test_a_429_from_one_model_pauses_only_that_model(wrapped):
    slept, _ = wrapped
    models = FakeModels()
    models.fail = RuntimeError(QUOTA_TEXT)
    with pytest.raises(RuntimeError):
        models.generate_content(model=FLASH, contents="x")
    models.fail = None
    models.generate_content(model=LITE, contents="x")
    assert slept == []
    models.generate_content(model=FLASH, contents="x")
    assert len(slept) == 1 and slept[0] == pytest.approx(31, abs=1.5)


def test_calls_without_a_model_keep_using_the_default_limiter(wrapped):
    slept, _ = wrapped
    FakeModels().generate_content(model="gemini-unknown", contents="x")
    assert slept == [] and llm_limiter.limiter._entries


# --- estimation des tokens ------------------------------------------------------------------------------------------

def test_the_prompt_estimate_reads_text_from_nested_contents_and_config():
    from google.genai import types
    contents = [types.Content(role="user", parts=[types.Part.from_text(text="a" * 400)]), {"text": "b" * 400}, "c" * 400]
    config = types.GenerateContentConfig(system_instruction="d" * 400)
    assert 400 <= llm_usage.estimate_prompt_tokens(contents, config) <= 405   # 1 600 caractères de texte, plus le rôle
    assert llm_usage.estimate_prompt_tokens("abcd") == 1 and llm_usage.estimate_prompt_tokens(None) == 0
    assert llm_usage.estimate_prompt_tokens(b"\x00" * 1000, 5) == 0


def test_total_tokens_is_only_read_from_a_valid_usage():
    assert llm_usage.total_tokens(_Response(12)) == 12
    assert llm_usage.total_tokens(_Response(None)) is None and llm_usage.total_tokens(object()) is None
    assert llm_usage.total_tokens(type("R", (), {"usage_metadata": _Usage("x")})()) is None


def test_the_limits_are_those_of_llm_models():
    assert llm_models.limits_for(FLASH).rpm == limiter_for(FLASH).max_per_minute
