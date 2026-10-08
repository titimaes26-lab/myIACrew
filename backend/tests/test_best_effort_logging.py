"""Les filets « best-effort » ne font jamais échouer l'appelant, mais laissent une trace quand ils échouent."""
import logging

import database
import execution_outcomes
import execution_state
import llm_limiter


class _BrokenEngine:
    def __getattr__(self, name):
        raise RuntimeError("base coupée")


def test_a_failure_that_cannot_be_recorded_is_logged_not_raised(monkeypatch, caplog):
    monkeypatch.setattr(database, "engine", _BrokenEngine())
    with caplog.at_level(logging.WARNING):
        execution_outcomes.mark_startup_failure(41, RuntimeError("démarrage"))
        execution_state.fail_execution(42, "interne")
    text = caplog.text
    assert "execution_id=41" in text and "exécution 42 non marquée" in text and "base coupée" in text


def test_a_failing_wait_callback_is_logged_and_does_not_break_the_announcement(caplog):
    def boom(wait):
        raise ValueError("callback cassé")

    with caplog.at_level(logging.DEBUG, logger="myiacrew"):
        llm_limiter._announce(1.0, boom)
    assert "callback d'attente en échec" in caplog.text and "callback cassé" in caplog.text
