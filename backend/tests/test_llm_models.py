"""Carte agent -> modèle et limites de débit par modèle (configuration centralisée de llm_models)."""
import pytest

import crew_llms
import llm_models
from llm_models import GEMINI_3_5_FLASH_LITE, GEMINI_3_8_FLASH, ModelLimits


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("GEMINI_MODEL", "GEMINI_MODEL_LIMITS", "LLM_RATE_MARGIN"):
        monkeypatch.delenv(name, raising=False)


def test_each_agent_uses_the_requested_model():
    assert llm_models.AGENT_MODELS == {
        "qualification_agent": "gemini-3.5-flash-lite",
        "product_designer_agent": "gemini-3.8-flash",
        "architect_agent": "gemini-3.8-flash",
        "diagnostic_agent": "gemini-3.8-flash",
        "developer_agent": "gemini-3.5-flash-lite",
        "qa_agent": "gemini-3.8-flash",
    }


def test_official_limits_per_model():
    assert llm_models.MODEL_LIMITS == {
        "gemini-3.8-flash": ModelLimits(rpm=10, tpm=250_000, rpd=1_500),
        "gemini-3.5-flash-lite": ModelLimits(rpm=15, tpm=250_000, rpd=1_000),
    }


def test_the_crew_llms_really_use_their_agent_model():
    expected = [
        (crew_llms.qualification_llm, GEMINI_3_5_FLASH_LITE), (crew_llms.designer_llm, GEMINI_3_8_FLASH),
        (crew_llms.architect_llm, GEMINI_3_8_FLASH), (crew_llms.diagnostic_llm, GEMINI_3_8_FLASH),
        (crew_llms.developer_llm, GEMINI_3_5_FLASH_LITE), (crew_llms.qa_llm, GEMINI_3_8_FLASH),
        (crew_llms.summary_llm, GEMINI_3_5_FLASH_LITE),
    ]
    for llm, model in expected:
        assert llm.model == model


def test_the_crew_agents_are_wired_to_those_llms():
    import crewquestion
    wiring = {
        "qualification_agent": crew_llms.qualification_llm, "product_designer_agent": crew_llms.designer_llm,
        "architect_agent": crew_llms.architect_llm, "diagnostic_agent": crew_llms.diagnostic_llm,
        "developer_agent": crew_llms.developer_llm, "qa_agent": crew_llms.qa_llm,
    }
    crew = crewquestion.AppDevelopmentCrew()
    for name, llm in wiring.items():
        assert getattr(crew, name)().llm is llm, name


@pytest.mark.parametrize("name, key", [
    ("gemini/gemini-3.8-flash", "gemini-3.8-flash"), ("models/Gemini-3.8-Flash", "gemini-3.8-flash"),
    ("  gemini-3.5-flash-lite ", "gemini-3.5-flash-lite"), (None, ""), ("", ""),
])
def test_model_key_drops_the_provider_prefix(name, key):
    assert llm_models.model_key(name) == key


def test_effective_limits_apply_the_default_safety_margin():
    assert llm_models.limits_for("gemini/gemini-3.8-flash") == ModelLimits(rpm=8, tpm=200_000, rpd=1_200)
    assert llm_models.limits_for("gemini-3.5-flash-lite") == ModelLimits(rpm=12, tpm=200_000, rpd=800)
    assert llm_models.limits_for("gemini-unknown") is None and llm_models.limits_for(None) is None


@pytest.mark.parametrize("value, rpm", [("1", 10), ("0.5", 5), ("0.75", 7), ("abc", 8), ("0", 8), ("2", 8), ("0.01", 8)])
def test_margin_is_configurable_and_invalid_values_fall_back(monkeypatch, value, rpm):
    monkeypatch.setenv("LLM_RATE_MARGIN", value)
    assert llm_models.limits_for("gemini-3.8-flash").rpm == rpm


def test_limits_can_be_overridden_for_a_paid_plan(monkeypatch):
    monkeypatch.setenv("LLM_RATE_MARGIN", "1")
    monkeypatch.setenv("GEMINI_MODEL_LIMITS", '{"gemini/gemini-3.8-flash": {"rpm": 60, "tpm": 1000000, "rpd": 10000}, "gemini-x": {"rpm": 5, "tpm": 100, "rpd": 7}}')
    assert llm_models.limits_for("gemini-3.8-flash") == ModelLimits(rpm=60, tpm=1_000_000, rpd=10_000)
    assert llm_models.limits_for("gemini-x") == ModelLimits(rpm=5, tpm=100, rpd=7)
    assert llm_models.limits_for("gemini-3.5-flash-lite").rpm == 15          # les autres gardent leurs plafonds


@pytest.mark.parametrize("raw", ["pas du json", "[1, 2]", '{"gemini-x": {"rpm": 5}}', '{"gemini-x": {"rpm": 0, "tpm": 1, "rpd": 1}}'])
def test_an_unreadable_override_is_ignored(monkeypatch, raw):
    monkeypatch.setenv("GEMINI_MODEL_LIMITS", raw)
    assert llm_models.limits_for("gemini-3.8-flash") == ModelLimits(rpm=8, tpm=200_000, rpd=1_200)
    assert llm_models.limits_for("gemini-x") is None


def test_model_names_use_the_crewai_provider_prefix_and_gemini_model_forces_one_model(monkeypatch):
    assert llm_models.model_for_agent("architect_agent") == "gemini/gemini-3.8-flash"
    assert llm_models.model_for_agent("developer_agent") == "gemini/gemini-3.5-flash-lite"
    assert llm_models.summary_model() == "gemini/gemini-3.5-flash-lite"
    monkeypatch.setenv("GEMINI_MODEL", "gemini/gemini-3.5-flash-lite")
    assert llm_models.model_for_agent("architect_agent") == llm_models.summary_model() == "gemini/gemini-3.5-flash-lite"
