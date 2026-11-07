"""Modèle de repli sur indisponibilité dans les APPELS : le wrapper du limiteur rejoue l'appel en échec sur le modèle de repli
(503 dès la première erreur, 504 tout de suite), pause commune, nom du modèle propre, seuil réglable (client google-genai factice)."""
import asyncio

import pytest

import llm_fallback
import llm_limiter
from llm_limiter import GeminiRateLimiter, install


FLASH, LITE = "gemini-3.8-flash", "gemini-3.5-flash-lite"


OVERLOAD = "503 UNAVAILABLE. {'error': {'code': 503, 'message': 'This model is currently experiencing high demand.', 'status': 'UNAVAILABLE'}}"


DEADLINE = "504 DEADLINE_EXCEEDED. {'error': {'code': 504, 'message': 'Deadline expired before operation could complete.', 'status': 'DEADLINE_EXCEEDED'}}"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEMINI_MODEL", "GEMINI_MODEL_LIMITS", "LLM_RATE_MARGIN", "GEMINI_FALLBACK_MODEL", "LLM_FALLBACK_TTL_S", "LLM_UNAVAILABLE_PAUSE_S"):
        monkeypatch.delenv(name, raising=False)
    # Les scénarios de ce fichier décrivent le repli « à la deuxième erreur » : réglage EXPLICITE (le défaut est 1, voir la fin du fichier).
    monkeypatch.setenv("LLM_FALLBACK_THRESHOLD", "2")


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


def test_the_api_receives_the_clean_model_name_even_when_the_variable_carries_a_prefix(wrapped, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "gemini/Gemini-3.5-Flash-Lite")
    monkeypatch.setenv("LLM_UNAVAILABLE_PAUSE_S", "0")
    models = FakeModels()
    with pytest.raises(RuntimeError):
        models.generate_content(model=FLASH, contents="x")
    assert models.generate_content(model=FLASH, contents="x")                    # 2e 503 : rejoué sur le repli
    models.generate_content(model=FLASH, contents="x")                           # repli actif : appel direct
    assert FakeModels.seen == [FLASH, FLASH, LITE, LITE]                         # jamais « gemini/gemini-3.5-… » envoyé à l'API


def test_by_default_the_first_503_is_replayed_on_the_fallback_at_once(wrapped, monkeypatch):
    monkeypatch.delenv("LLM_FALLBACK_THRESHOLD", raising=False)
    models = FakeModels()
    assert models.generate_content(model=FLASH, contents="x")                    # 1er 503 : servi par le repli, rien ne remonte
    assert FakeModels.seen == [FLASH, LITE]
    models.generate_content(model=FLASH, contents="x")
    assert FakeModels.seen[-1] == LITE


def test_a_success_still_resets_the_count_when_the_threshold_is_raised(wrapped, monkeypatch):
    monkeypatch.setenv("LLM_FALLBACK_THRESHOLD", "3")
    monkeypatch.setenv("LLM_UNAVAILABLE_PAUSE_S", "0")
    models = FakeModels()
    for _ in range(2):
        with pytest.raises(RuntimeError):
            models.generate_content(model=FLASH, contents="x")
    FakeModels.failing = set()
    models.generate_content(model=FLASH, contents="x")                           # succès : compteur à zéro
    FakeModels.failing = {FLASH}
    for _ in range(2):
        with pytest.raises(RuntimeError):
            models.generate_content(model=FLASH, contents="x")                   # nouvelle série : 2 erreurs seulement, pas encore de repli
    assert LITE not in FakeModels.seen


MISSING = "404 NOT_FOUND. {'error': {'code': 404, 'message': 'models/gemini-3.8-flash is not found for API version v1beta', 'status': 'NOT_FOUND'}}"


def test_a_missing_model_is_replayed_on_the_fallback_at_once_and_for_much_longer_than_an_overload(wrapped, caplog):
    FakeModels.message = MISSING
    models = FakeModels()
    assert models.generate_content(model=FLASH, contents="x")                  # servi par le repli dès le premier 404
    assert FakeModels.seen == [FLASH, LITE]
    assert "INTROUVABLE" in caplog.text and "GEMINI_DIAGNOSTIC_MODEL" in caplog.text
    wait, _ = llm_limiter.limiter_for(FLASH).reserve_ticket(tokens=0)
    assert wait == 0                                                           # pas de pause de 30 s : le modèle n'existe plus
    assert llm_fallback.replacement_for(FLASH, now=__import__("time").monotonic() + 1_800) == LITE     # un surcharge aurait expiré à 600 s


def test_a_near_miss_text_with_another_code_is_not_replayed(wrapped):
    FakeModels.message = "400 INVALID_ARGUMENT. model foo not found in the tuned models list"
    with pytest.raises(RuntimeError):
        FakeModels().generate_content(model=FLASH, contents="x")
    assert FakeModels.seen == [FLASH]


def test_a_missing_model_never_triggers_the_fallback_when_it_is_disabled(wrapped, monkeypatch):
    monkeypatch.setenv("GEMINI_FALLBACK_MODEL", "off")
    FakeModels.message = MISSING
    with pytest.raises(RuntimeError):
        FakeModels().generate_content(model=FLASH, contents="x")
    assert FakeModels.seen == [FLASH]
