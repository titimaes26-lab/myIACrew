"""Le modèle utilisé par chaque agent est écrit dans sa section du rapport (marqueur lu par le frontend)."""
from types import SimpleNamespace

import pytest
from sqlmodel import Session

import crew_workflow
import execution_context
import execution_persistence
import llm_models
from database import ExecutionHistory
from run_support import _new_execution

ROLES = {
    "Senior Product Owner / Specialist en Qualification": "gemini-3.5-flash-lite",
    "Lead Product / Game Designer": "gemini-3.8-flash",
    "Architecte Logiciel React / TypeScript": "gemini-3.8-flash",
    "Analyste Diagnostic Technique": "gemini-3.8-flash",
    "Développeur Fullstack React / TypeScript": "gemini-3.5-flash-lite",
    "QA Engineer / Automated Tester": "gemini-3.8-flash",
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


@pytest.mark.parametrize("role, model", ROLES.items())
def test_each_agent_role_maps_to_its_model(role, model):
    assert llm_models.model_for_role(role) == model
    assert llm_models.model_for_role("  " + role.lower().replace(" / ", "  /  ") + " ") == model   # casse et espaces ignorés


def test_unknown_or_missing_roles_have_no_model():
    assert [llm_models.model_for_role(role) for role in ("Agent", "", None, "Résumé")] == [None] * 4


def test_the_forced_model_is_the_one_reported(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini/gemini-3.5-flash-lite")
    assert llm_models.model_for_role("Lead Product / Game Designer") == "gemini-3.5-flash-lite"


def test_a_section_carries_the_duration_and_the_model_markers_after_its_heading():
    section = crew_workflow.format_agent_section("Lead Product / Game Designer", "texte", 12.5)
    assert section == "## Lead Product / Game Designer\n<!--agent-duration:12.50-->\n<!--agent-model:gemini-3.8-flash-->\n\ntexte"
    assert crew_workflow.format_agent_section("Lead Product / Game Designer", "texte") == (
        "## Lead Product / Game Designer\n<!--agent-model:gemini-3.8-flash-->\n\ntexte")


def test_a_section_without_a_known_agent_keeps_the_previous_format():
    assert crew_workflow.format_agent_section("Agent", "texte", 1.0) == "## Agent\n<!--agent-duration:1.00-->\n\ntexte"
    assert crew_workflow.format_agent_section("Agent", "texte") == "## Agent\n\ntexte"


def test_the_final_result_gives_every_agent_section_its_model():
    def output(role):
        return SimpleNamespace(agent=role, raw=f"sortie {role}")

    result = SimpleNamespace(
        raw="x", tasks_output=[output("Lead Product / Game Designer"), output("Développeur Fullstack React / TypeScript")],
    )
    text = crew_workflow._format_crew_result(result, {"Lead Product / Game Designer": 3.0})
    assert "## Lead Product / Game Designer\n<!--agent-duration:3.00-->\n<!--agent-model:gemini-3.8-flash-->" in text
    assert "## Développeur Fullstack React / TypeScript\n<!--agent-model:gemini-3.5-flash-lite-->" in text


def test_the_progressive_persistence_writes_the_model_marker(engine):
    execution_id, _ = _new_execution(engine)
    execution_persistence.persist_completed_agent(execution_id, "Architecte Logiciel React / TypeScript", "plan", 4.0)
    with Session(engine) as db:
        result = db.get(ExecutionHistory, execution_id).result
    assert result == "## Architecte Logiciel React / TypeScript\n<!--agent-duration:4.00-->\n<!--agent-model:gemini-3.8-flash-->\n\nplan"
    sections = execution_context.parse_completed_agents(result)
    assert "<!--agent-model:gemini-3.8-flash-->" in sections["Architecte Logiciel React / TypeScript"]


def test_the_model_marker_never_reaches_the_plan_given_to_the_next_turn():
    assert execution_context.AGENT_MODEL_MARKER.sub("", "<!--agent-model:gemini-3.8-flash-->\n\nplan") == "plan"
    assert execution_context.AGENT_MODEL_MARKER.sub("", "plan sans marqueur") == "plan sans marqueur"
