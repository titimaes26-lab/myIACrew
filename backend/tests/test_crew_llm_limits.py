"""Les limites d'un appel LLM (délai, plafond de sortie) doivent être réellement posées sur le fournisseur, pas seulement passées en paramètre."""
import pytest

import crew_llms


def _http_timeout_ms(llm):
    return llm._client._api_client._http_options.timeout


@pytest.mark.parametrize("name, seconds", [
    ("qualification_llm", 90), ("designer_llm", 180), ("architect_llm", 180), ("diagnostic_llm", 300),
    ("developer_llm", 180), ("qa_llm", 180), ("summary_llm", 45),
])
def test_each_llm_applies_its_own_call_timeout_on_the_http_client(name, seconds):
    assert _http_timeout_ms(getattr(crew_llms, name)) == seconds * 1000


def test_make_llm_defaults_to_120_seconds_and_converts_to_milliseconds():
    assert _http_timeout_ms(crew_llms._make_llm("qa_agent", 0.1)) == 120_000
    assert _http_timeout_ms(crew_llms._make_llm("qa_agent", 0.1, request_timeout=7)) == 7_000


def test_google_enforces_the_call_timeout_so_it_must_leave_room_for_a_thinking_model():
    """Le client google-genai envoie ce délai à Google (X-Server-Timeout) : un 90 s coupait le designer en « 504 DEADLINE_EXCEEDED » sur le vrai modèle."""
    for name in ("designer_llm", "architect_llm", "developer_llm", "qa_llm"):
        assert _http_timeout_ms(getattr(crew_llms, name)) >= 180_000
    assert _http_timeout_ms(crew_llms.diagnostic_llm) >= 300_000        # génère le code complet : le plus long
    import google.genai._api_client as api_client
    source = open(api_client.__file__, encoding="utf-8").read()
    assert "X-Server-Timeout" in source                                    # le mécanisme décrit ci-dessus existe toujours dans la version épinglée


@pytest.mark.parametrize("raw,expected", [("", 1.0), ("2", 2.0), ("0.5", 0.5), ("10", 10.0), ("0.25", 0.25), ("abc", 1.0), ("0.1", 1.0), ("11", 1.0), ("-3", 1.0), ("nan", 1.0)])
def test_the_timeout_scale_comes_from_the_environment_within_safe_bounds(monkeypatch, raw, expected):
    monkeypatch.setenv("LLM_TIMEOUT_SCALE", raw)
    assert crew_llms._timeout_scale() == expected


def test_the_timeout_scale_multiplies_every_call_timeout(monkeypatch):
    monkeypatch.setenv("LLM_TIMEOUT_SCALE", "2")
    assert _http_timeout_ms(crew_llms._make_llm("qa_agent", 0.1)) == 240_000
    assert _http_timeout_ms(crew_llms._make_llm("qa_agent", 0.1, request_timeout=7)) == 14_000
    monkeypatch.setenv("LLM_TIMEOUT_SCALE", "0.5")
    assert _http_timeout_ms(crew_llms._make_llm("qa_agent", 0.1, request_timeout=100)) == 50_000


def _generation_cap(llm):
    return llm._prepare_generation_config().max_output_tokens


def test_the_architect_output_cap_reaches_the_generation_config_and_the_others_have_none():
    assert _generation_cap(crew_llms.architect_llm) == crew_llms._architect_max_tokens() == 8192
    for name in ("qualification_llm", "designer_llm", "diagnostic_llm", "developer_llm", "qa_llm", "summary_llm"):
        assert _generation_cap(getattr(crew_llms, name)) is None


def test_make_llm_applies_an_explicit_cap_to_the_generation_config():
    assert _generation_cap(crew_llms._make_llm("qa_agent", 0.1, max_tokens=123)) == 123
    assert _generation_cap(crew_llms._make_llm("qa_agent", 0.1)) is None


@pytest.mark.parametrize("raw,expected", [("", 1), ("0", 0), ("2", 2), ("3", 3), ("4", 1), ("-1", 1), ("abc", 1), ("1.5", 1)])
def test_agent_retries_come_from_the_environment_within_safe_bounds(monkeypatch, raw, expected):
    monkeypatch.setenv("AGENT_MAX_RETRIES", raw)
    assert crew_llms.agent_max_retries() == expected


def test_every_crew_agent_gets_the_reduced_retry_limit(monkeypatch):
    import crewquestion
    crew = crewquestion.AppDevelopmentCrew()
    names = ("qualification_agent", "product_designer_agent", "architect_agent", "diagnostic_agent", "developer_agent", "qa_agent")
    monkeypatch.delenv("AGENT_MAX_RETRIES", raising=False)
    assert [getattr(crew, n)().max_retry_limit for n in names] == [1] * 6
    monkeypatch.setenv("AGENT_MAX_RETRIES", "0")
    crew = crewquestion.AppDevelopmentCrew()                                # @agent mémorise l'agent par instance
    assert [getattr(crew, n)().max_retry_limit for n in names] == [0] * 6
