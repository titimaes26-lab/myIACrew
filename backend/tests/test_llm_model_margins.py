"""Marge par défaut PROPRE à un modèle (MODEL_MARGINS) : 3.8 utilise ses 10 RPM pleins, LLM_RATE_MARGIN l'emporte."""
import pytest

import llm_models


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("LLM_RATE_MARGIN", raising=False)
    monkeypatch.delenv("GEMINI_MODEL_LIMITS", raising=False)


def test_the_heavier_model_uses_its_full_rpm_by_default_and_the_env_margin_still_wins(monkeypatch):
    assert llm_models.limits_for("gemini-3.8-flash").rpm == 10                  # 45 appels d'Analyste : 8 RPM = plus de 5 minutes d'attente
    assert llm_models.limits_for("gemini-3.5-flash-lite").rpm == 12             # 0,8 × 15 : inchangé
    monkeypatch.setenv("LLM_RATE_MARGIN", "0.5")
    assert llm_models.limits_for("gemini-3.8-flash").rpm == 5 and llm_models.limits_for("gemini-3.5-flash-lite").rpm == 7


@pytest.mark.parametrize("raw", ["abc", " ", "0", "5", "-1"])
def test_an_unreadable_env_margin_falls_back_to_the_model_own_default_not_to_the_common_one(monkeypatch, raw):
    monkeypatch.setenv("LLM_RATE_MARGIN", raw)
    assert llm_models.limits_for("gemini-3.8-flash").rpm == 10 and llm_models.limits_for("gemini-3.5-flash-lite").rpm == 12
