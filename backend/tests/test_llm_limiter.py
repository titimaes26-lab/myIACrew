import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

import llm_limiter  # noqa: E402
from llm_limiter import GeminiRateLimiter, install, is_quota_error, retry_delay_seconds  # noqa: E402

QUOTA_TEXT = (
    "429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota.\\n"
    "Please retry in 44.012534086s.', 'details': [{'retryDelay': '44s'}]}}"
)


def test_reserve_never_exceeds_the_per_minute_cap():
    limiter = GeminiRateLimiter(3, 60)
    assert [limiter.reserve(now=0) for _ in range(3)] == [0, 0, 0]
    assert [round(limiter.reserve(now=0)) for _ in range(5)] == [60, 60, 60, 120, 120]


def test_reserve_frees_slots_as_the_window_slides():
    limiter = GeminiRateLimiter(3, 60)
    waits = [round(limiter.reserve(now=t)) for t in (0, 1, 2, 3, 70, 71, 72, 73)]
    assert waits == [0, 0, 0, 57, 0, 0, 48, 57]


def test_no_window_of_60s_ever_holds_more_than_the_cap():
    limiter = GeminiRateLimiter(4, 60)
    starts = sorted(t + limiter.reserve(now=t) for t in [i * 0.7 for i in range(60)])
    for index, start in enumerate(starts):
        assert sum(1 for other in starts if start - 60 + 1e-6 < other <= start) <= 4, (index, start)


def test_retry_delay_is_read_from_both_google_formats():
    assert retry_delay_seconds(RuntimeError("Please retry in 8.375195008s.")) == pytest.approx(8.375, abs=1e-3)
    assert retry_delay_seconds(RuntimeError("{'retryDelay': '5s'}")) == 5
    assert retry_delay_seconds(RuntimeError('"retryDelay": "11s"')) == 11
    assert retry_delay_seconds(RuntimeError("boom")) is None


def test_quota_errors_are_recognised_without_matching_other_errors():
    assert is_quota_error(RuntimeError(QUOTA_TEXT)) and is_quota_error(RuntimeError("RESOURCE_EXHAUSTED"))
    assert not is_quota_error(RuntimeError("500 internal error"))


def test_a_429_sets_a_shared_pause_that_every_following_call_waits_for():
    limiter = GeminiRateLimiter(12, 60)
    pause = limiter.note_rate_limited(RuntimeError(QUOTA_TEXT), now=100)
    assert pause == pytest.approx(45.0125, abs=1e-3)   # délai demandé + 1 s de marge
    assert limiter.reserve(now=100) == pytest.approx(pause, abs=1e-3)
    assert limiter.reserve(now=110) == pytest.approx(pause - 10, abs=1e-3)
    assert limiter.reserve(now=100 + pause + 1) == 0
    # sans délai lisible : pause par défaut ; une pause plus courte ne raccourcit jamais la pause en cours
    other = GeminiRateLimiter(12, 60)
    assert other.note_rate_limited(RuntimeError("429"), now=0) == llm_limiter.DEFAULT_COOLDOWN_SECONDS + 1
    other.note_rate_limited(RuntimeError("retry in 1s"), now=0)
    assert other.reserve(now=0) == llm_limiter.DEFAULT_COOLDOWN_SECONDS + 1


class FakeModels:
    def __init__(self):
        self.calls = 0
        self.fail = None

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise self.fail
        return "ok"

    def generate_content_stream(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise self.fail
        yield "a"
        yield "b"


class FakeAsyncModels:
    calls = 0

    async def generate_content(self, **kwargs):
        FakeAsyncModels.calls += 1
        return "async-ok"


@pytest.fixture()
def fresh(monkeypatch):
    monkeypatch.setattr(llm_limiter, "limiter", GeminiRateLimiter(100, 60))
    monkeypatch.setattr(llm_limiter, "_installed", set())
    monkeypatch.setattr(llm_limiter.time, "sleep", lambda seconds: slept.append(seconds))
    slept: list[float] = []
    waits: list[float] = []
    targets = [(FakeModels, "generate_content", "sync"), (FakeModels, "generate_content_stream", "stream"),
               (FakeAsyncModels, "generate_content", "async")]
    originals = {(cls, name): getattr(cls, name) for cls, name, _ in targets}
    install(on_wait=waits.append, targets=targets)
    yield slept, waits
    for (cls, name), original in originals.items():
        setattr(cls, name, original)


def test_install_wraps_sync_stream_and_async_calls_and_is_idempotent(fresh):
    targets = [(FakeModels, "generate_content", "sync")]
    assert install(targets=targets) == 0   # déjà enveloppé : pas de double enveloppe
    models = FakeModels()
    assert models.generate_content() == "ok"
    assert list(models.generate_content_stream()) == ["a", "b"]
    assert asyncio.run(FakeAsyncModels().generate_content()) == "async-ok"
    assert models.calls == 2


def test_calls_over_the_cap_wait_and_report_the_wait(fresh, monkeypatch):
    slept, waits = fresh
    monkeypatch.setattr(llm_limiter, "limiter", GeminiRateLimiter(2, 60))
    models = FakeModels()
    for _ in range(3):
        models.generate_content()
    assert len(slept) == 1 and 0 < slept[0] <= 60 and waits == slept


def test_a_429_from_the_api_pauses_every_later_call_and_is_still_raised(fresh):
    slept, _ = fresh
    models = FakeModels()
    models.fail = RuntimeError(QUOTA_TEXT)
    with pytest.raises(RuntimeError):
        models.generate_content()
    models.fail = None
    assert models.generate_content() == "ok"
    assert len(slept) == 1 and slept[0] == pytest.approx(45, abs=1.5)   # pause commune imposée au 2e appel


def test_other_errors_do_not_create_a_pause(fresh):
    slept, _ = fresh
    models = FakeModels()
    models.fail = ValueError("500 boom")
    with pytest.raises(ValueError):
        models.generate_content()
    models.fail = None
    models.generate_content()
    assert slept == []


def test_the_real_client_methods_are_wrapped_once_crewquestion_is_imported():
    import crewquestion  # noqa: F401
    from google.genai import models
    assert hasattr(models.Models.generate_content, "__wrapped__")
    assert hasattr(models.AsyncModels.generate_content, "__wrapped__")
    assert time.monotonic() > 0
