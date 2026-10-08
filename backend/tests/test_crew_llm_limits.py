"""Les limites d'un appel LLM (délai, plafond de sortie) doivent être réellement posées sur le fournisseur, pas seulement passées en paramètre."""
import pytest

import crew_llms


def _http_timeout_ms(llm):
    return llm._client._api_client._http_options.timeout


@pytest.mark.parametrize("name, seconds", [
    ("qualification_llm", 45), ("designer_llm", 90), ("architect_llm", 90), ("diagnostic_llm", 120),
    ("developer_llm", 90), ("qa_llm", 90), ("summary_llm", 25),
])
def test_each_llm_applies_its_own_call_timeout_on_the_http_client(name, seconds):
    assert _http_timeout_ms(getattr(crew_llms, name)) == seconds * 1000


def test_make_llm_defaults_to_120_seconds_and_converts_to_milliseconds():
    assert _http_timeout_ms(crew_llms._make_llm(0.1)) == 120_000
    assert _http_timeout_ms(crew_llms._make_llm(0.1, request_timeout=7)) == 7_000


def _generation_cap(llm):
    return llm._prepare_generation_config().max_output_tokens


def test_the_architect_output_cap_reaches_the_generation_config_and_the_others_have_none():
    assert _generation_cap(crew_llms.architect_llm) == crew_llms._architect_max_tokens() == 8192
    for name in ("qualification_llm", "designer_llm", "diagnostic_llm", "developer_llm", "qa_llm", "summary_llm"):
        assert _generation_cap(getattr(crew_llms, name)) is None


def test_make_llm_applies_an_explicit_cap_to_the_generation_config():
    assert _generation_cap(crew_llms._make_llm(0.1, max_tokens=123)) == 123
    assert _generation_cap(crew_llms._make_llm(0.1)) is None
