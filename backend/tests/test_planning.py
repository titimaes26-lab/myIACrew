import warnings


import pytest

import crewquestion as cq
import crew_llms


def _agents():
    crew = cq.AppDevelopmentCrew()
    return {
        "qualification": crew.qualification_agent, "designer": crew.product_designer_agent,
        "architect": crew.architect_agent, "diagnostic": crew.diagnostic_agent,
        "developer": crew.developer_agent, "qa": crew.qa_agent,
    }


def test_diagnostic_plans_once_and_observes_without_extra_llm_calls(monkeypatch):
    monkeypatch.delenv("DIAGNOSTIC_REASONING_EFFORT", raising=False)
    agent = cq.AppDevelopmentCrew().diagnostic_agent()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)   # CrewAI lit lui-même le champ déprécié `reasoning`
        assert agent.reasoning is False                      # plus de `reasoning=True` (effort « medium » implicite)
    config = agent.planning_config
    assert config is not None
    assert config.reasoning_effort == "low"        # observation heuristique : aucun appel LLM par étape
    assert config.observe_steps is None            # la valeur « low » suffit à désactiver l'observation LLM
    assert config.max_attempts == 1
    assert config.max_steps == crew_llms.DIAGNOSTIC_PLAN_MAX_STEPS == 5
    assert config.max_step_iterations == crew_llms.DIAGNOSTIC_STEP_MAX_ITERATIONS == 8
    assert agent.max_iter == 7                      # inchangé


@pytest.mark.parametrize("value, expected", [
    ("", "low"), ("low", "low"), ("medium", "medium"), ("HIGH", "high"), (" medium ", "medium"),
    ("extreme", "low"), ("0", "low"),
])
def test_diagnostic_effort_is_configurable_and_defaults_to_low(monkeypatch, value, expected):
    monkeypatch.setenv("DIAGNOSTIC_REASONING_EFFORT", value)
    assert crew_llms._diagnostic_reasoning_effort() == expected
    assert crew_llms._diagnostic_planning_config().reasoning_effort == expected


@pytest.mark.parametrize("value, expected", [
    ("", 8), ("5", 5), (" 12 ", 12), ("1", 1), ("0", 8), ("-3", 8), ("beaucoup", 8), ("2.5", 8),
])
def test_step_iterations_are_configurable_with_a_safe_default(monkeypatch, value, expected):
    monkeypatch.setenv("DIAGNOSTIC_STEP_MAX_ITERATIONS", value)
    assert crew_llms._diagnostic_step_max_iterations() == expected
    assert crew_llms._diagnostic_planning_config().max_step_iterations == expected


def test_only_the_diagnostic_agent_plans():
    # Garde : un autre agent qui planifierait rajouterait un appel d'observation par étape (voir la config du diagnostic).
    planning = {name: getattr(agent(), "planning_config", None) is not None for name, agent in _agents().items()}
    assert planning == {name: name == "diagnostic" for name in planning}
