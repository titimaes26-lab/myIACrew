"""Le délai d'un appel LLM doit être réellement posé sur le client HTTP du fournisseur (et pas seulement passé en paramètre)."""
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
