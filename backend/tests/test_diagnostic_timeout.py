"""Délai d'un appel de l'Analyste (DIAGNOSTIC_TIMEOUT_S) : 150 s par défaut, borné, réglable sans code."""
import pytest

import crew_llms


@pytest.mark.parametrize("value, expected", [(None, 150), ("90", 90), ("600", 600), ("60", 60), ("59", 150), ("601", 150), ("abc", 150), ("", 150)])
def test_the_analyst_timeout_is_bounded_and_defaults_to_150_seconds(monkeypatch, value, expected):
    monkeypatch.delenv("DIAGNOSTIC_TIMEOUT_S", raising=False)
    if value is not None:
        monkeypatch.setenv("DIAGNOSTIC_TIMEOUT_S", value)
    assert crew_llms._diagnostic_timeout() == expected


def test_the_analyst_llm_really_gets_the_shorter_timeout():
    options = crew_llms.diagnostic_llm.client_params["http_options"] if hasattr(crew_llms.diagnostic_llm, "client_params") else None
    assert options is not None and options.timeout == int(150 * crew_llms._timeout_scale() * 1000)
