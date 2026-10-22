"""Budget de tokens d'une exécution : refus AVANT l'appel quand il est atteint, comptage réel (ou estimé), message sans nombre
(sinon crew_retry le prendrait pour un quota), classement non réessayable, et durée maximale de 30 min par défaut."""
import asyncio
import re

import pytest

import crew_retry
import execution
import execution_context
import execution_state
import llm_limiter
import run_budget
from errors import ErrorCode, classify_exception, http_status_for
from run_budget import TokenBudgetExceeded, track_budget

MODEL = "gemini-3.8-flash"


class _Usage:
    def __init__(self, total):
        self.total_token_count = total


class _Response:
    def __init__(self, total):
        self.usage_metadata = _Usage(total) if total is not None else None


class _Models:
    calls = 0
    tokens: object = 60
    fail = False

    def generate_content(self, *, model, contents, config=None):
        type(self).calls += 1
        if type(self).fail:
            raise RuntimeError("échec")
        return _Response(type(self).tokens)


@pytest.fixture()
def hooked(monkeypatch):
    monkeypatch.setattr(llm_limiter, "_installed", set())
    monkeypatch.setattr(llm_limiter.time, "sleep", lambda seconds: None)    # le débit du modèle (8 requêtes/min) ne fait pas partie du test
    original = _Models.generate_content
    llm_limiter.install(targets=[(_Models, "generate_content", "sync")])
    _Models.calls, _Models.tokens, _Models.fail = 0, 60, False
    yield
    _Models.generate_content = original


def call():
    return _Models().generate_content(model=MODEL, contents="x")


# --- réglage -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [("", 1_000_000), ("abc", 1_000_000), ("9999", 1_000_000), ("10000", 10_000), ("2500000", 2_500_000)])
def test_the_limit_comes_from_the_environment_with_a_safe_default(monkeypatch, raw, expected):
    monkeypatch.setenv("RUN_MAX_TOKENS", raw)
    assert run_budget.max_tokens() == expected


def test_default_limits_are_one_million_tokens_and_thirty_minutes(monkeypatch):
    monkeypatch.delenv("RUN_MAX_TOKENS", raising=False)
    assert run_budget.max_tokens() == 1_000_000
    import os
    if not os.getenv("EXECUTION_TIMEOUT_S"):
        assert execution_state.EXECUTION_TIMEOUT_S == 1800


# --- compteur ----------------------------------------------------------------------------------------------------------

def test_outside_an_execution_nothing_is_counted_or_refused():
    run_budget.add(10**9)
    run_budget.enforce()


def test_enforce_refuses_only_once_the_limit_is_reached():
    with track_budget(100) as budget:
        run_budget.add(99)
        run_budget.enforce()
        run_budget.add(1)
        with pytest.raises(TokenBudgetExceeded):
            run_budget.enforce()
        assert budget.used == 100
    run_budget.enforce()                                    # budget retiré à la sortie


def test_negative_amounts_never_lower_the_count():
    with track_budget(100) as budget:
        run_budget.add(50)
        run_budget.add(-30)
        assert budget.used == 50


def test_each_execution_has_its_own_budget():
    async def one(n):
        with track_budget(100):
            run_budget.add(n)
            await asyncio.sleep(0.01)
            try:
                run_budget.enforce()
            except TokenBudgetExceeded:
                return "stop"
            return "ok"

    async def main():
        return await asyncio.gather(one(150), one(10))
    assert asyncio.run(main()) == ["stop", "ok"]


def test_the_budget_is_shared_with_worker_threads():
    async def go():
        with track_budget(100):
            await asyncio.to_thread(run_budget.add, 100)
            with pytest.raises(TokenBudgetExceeded):
                await asyncio.to_thread(run_budget.enforce)
    asyncio.run(go())


# --- appels au modèle --------------------------------------------------------------------------------------------------

def test_calls_are_counted_then_refused_before_reaching_the_model(hooked):
    with track_budget(100) as budget:
        call()
        call()                                              # 120 : atteint
        assert budget.used == 120 and _Models.calls == 2
        with pytest.raises(TokenBudgetExceeded):
            call()
        assert _Models.calls == 2                           # le modèle n'est PAS appelé


def test_a_refused_call_reserves_no_rate_slot(hooked):
    lim = llm_limiter.limiter_for(MODEL)
    with track_budget(10) as budget:
        call()
        before = len(lim._entries)
        with pytest.raises(TokenBudgetExceeded):
            call()
        assert len(lim._entries) == before and budget.used == 60


def test_without_usage_the_input_estimate_is_counted(hooked):
    _Models.tokens = None
    with track_budget(10**6) as budget:
        _Models().generate_content(model=MODEL, contents="x" * 4000)
        assert budget.used >= 1000


def test_a_failed_call_counts_nothing(hooked):
    _Models.fail = True
    with track_budget(100) as budget:
        with pytest.raises(RuntimeError):
            call()
        assert budget.used == 0


def test_calls_are_unrestricted_outside_a_tracked_execution(hooked):
    for _ in range(50):
        call()
    assert _Models.calls == 50


# --- message et classement ---------------------------------------------------------------------------------------------

def test_the_message_has_no_digit_and_no_retry_marker():
    """crew_retry rejoue toute erreur dont le texte contient « 429 », « quota »... : un compteur dans le message le déclencherait."""
    assert not re.search(r"\d", run_budget.MESSAGE)
    assert not crew_retry._is_retryable_error(run_budget.MESSAGE.lower())
    with track_budget(10_000) as budget:
        budget.add(1_042_900)
        with pytest.raises(TokenBudgetExceeded) as caught:
            budget.enforce()
    assert not crew_retry._is_retryable_error(str(caught.value).lower())


def test_the_budget_error_is_classified_and_never_retried():
    info = classify_exception(TokenBudgetExceeded(run_budget.MESSAGE))
    assert info.code == ErrorCode.TOKEN_BUDGET_EXCEEDED and info.retryable is False and "budget de tokens" in info.message
    try:
        try:
            raise TokenBudgetExceeded(run_budget.MESSAGE)
        except TokenBudgetExceeded as budget_error:
            raise RuntimeError("Agent execution failed after 3 retries") from budget_error
    except RuntimeError as wrapped:
        assert classify_exception(wrapped).code == ErrorCode.TOKEN_BUDGET_EXCEEDED     # CrewAI enveloppe l'erreur d'origine
    assert http_status_for(ErrorCode.TOKEN_BUDGET_EXCEEDED) == 500


# --- exécution ---------------------------------------------------------------------------------------------------------

def test_run_crew_gives_each_execution_a_budget_that_stops_it(monkeypatch):
    monkeypatch.setenv("RUN_MAX_TOKENS", "10000")

    class Crew:
        async def run_dynamic_crew(self, **kwargs):
            run_budget.add(10_000)
            run_budget.enforce()

    async def go():
        await execution.run_crew(Crew(), execution_context.RunState(), 1, "BUGFIX", {}, None)
    with pytest.raises(TokenBudgetExceeded):
        asyncio.run(go())
    run_budget.enforce()                                    # rien ne fuit hors de l'exécution
