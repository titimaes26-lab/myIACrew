"""Limiteurs de débit par modèle : les ENVELOPPES du client google-genai (appels, flux, async) appliquent le limiteur du modèle de chaque appel.
Suite de test_llm_limiter_models.py."""
import asyncio
import logging
import pytest
import llm_limiter
from llm_limiter import DailyQuotaExceeded, GeminiRateLimiter, install, limiter_for


FLASH, LITE = "gemini-3.8-flash", "gemini-3.5-flash-lite"


QUOTA_TEXT = "429 RESOURCE_EXHAUSTED. Please retry in 30s."


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEMINI_MODEL", "GEMINI_MODEL_LIMITS", "LLM_RATE_MARGIN"):
        monkeypatch.delenv(name, raising=False)


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

    async def generate_content_stream(self, *, model, contents, config=None):
        async def chunks():
            yield _Response(None)
            yield _Response(150_000)
        return chunks()


@pytest.fixture()
def wrapped(monkeypatch):
    slept: list[float] = []
    monkeypatch.setattr(llm_limiter.time, "sleep", slept.append)
    monkeypatch.setattr(llm_limiter, "_installed", set())
    monkeypatch.setattr(llm_limiter, "limiter", GeminiRateLimiter(100, 60))
    FakeModels.seen, FakeModels.total, FakeModels.fail = [], 50, None
    targets = [(FakeModels, "generate_content", "sync"), (FakeModels, "generate_content_stream", "stream"),
               (FakeAsyncModels, "generate_content", "async"), (FakeAsyncModels, "generate_content_stream", "astream")]
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


def test_async_streams_settle_with_the_last_reported_usage(wrapped):
    async def scenario():
        stream = await FakeAsyncModels().generate_content_stream(model=FLASH, contents="x")
        return [chunk async for chunk in stream]
    assert len(asyncio.run(scenario())) == 2
    assert limiter_for(FLASH)._entries[-1][1] == 150_000     # le total du dernier morceau, pas l'estimation d'entrée


@pytest.mark.parametrize("scenario, expected", [
    ("rpm", "8 requêtes par minute"), ("tpm", "200000 tokens par minute"), ("429", "pause après un 429"),
])
def test_the_wait_log_names_the_constraint_that_caused_it(wrapped, caplog, scenario, expected):
    models = FakeModels()
    if scenario == "rpm":
        for _ in range(8):
            models.generate_content(model=FLASH, contents="x")
        call = "x"
    elif scenario == "tpm":
        models.total = 199_000
        models.generate_content(model=FLASH, contents="x")
        call = "y" * 80_000
    else:
        llm_limiter._note_error(RuntimeError(QUOTA_TEXT), limiter_for(FLASH))
        call = "x"
    with caplog.at_level(logging.INFO, logger="myiacrew"):
        models.generate_content(model=FLASH, contents=call)
    assert "retardée" in caplog.text and expected in caplog.text and FLASH in caplog.text
