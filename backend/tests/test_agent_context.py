"""Contexte transmis aux agents (vrais Crew et vraies tâches, LLM factices) : l'Analyste doit RECEVOIR la sortie de l'Architecte.
Exécution 137 (FEATURE sur PlatformGame) : l'Analyste a produit tout autre chose que le plan de l'Architecte."""
import asyncio
from typing import Any

import pytest
from crewai.llms.base_llm import BaseLLM

import crew_cache
import crew_retry
import crew_summary
import crew_plan
import crew_run
import crewquestion as cq

INPUTS = {name: "x" for name in (
    "conversation_context", "work_branch", "repo_instructions", "base_branch", "repo_owner", "repo_name", "repo_snapshot", "previous_plan",
)}
INPUTS["user_request"] = "DEMANDE_UNIQUE_DE_CETTE_EXECUTION"
ARCHITECT_PLAN = """PLAN_ARCHITECTE_UNIQUE : power-ups et niveaux

## Fichiers à créer ou modifier

- CRÉER src/engine/powerUpEngine.ts : moteur des power-ups
- CRÉER src/components/LevelSelector.tsx : choix du niveau
- MODIFIER src/App.tsx : intégration

## Risques

- Risque : timers | Parade : horodatages absolus"""
DESIGN_SPEC = "SPEC_DESIGNER_UNIQUE : système de power-ups"


class RecordingLLM(BaseLLM):
    """LLM hors ligne qui répond une phrase fixe et GARDE les messages reçus (le prompt réel de l'agent)."""

    def __init__(self, name: str, answer: str):
        super().__init__(model=f"fake-{name}", temperature=0)
        self.name, self.answer, self.prompts = name, answer, []

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None, available_functions: Any = None, from_task: Any = None, from_agent: Any = None, **kwargs: Any) -> Any:
        self.prompts.append(messages if isinstance(messages, str) else "\n".join(str(m.get("content", m)) for m in messages))
        return f"Final Answer: {self.answer}"

    def supports_function_calling(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 8000


@pytest.fixture
def run_workflow(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)
    monkeypatch.setattr(crew_cache, "evict_memoized_cache_entries", lambda crew: None)

    async def no_summary(*args, **kwargs):
        return None
    monkeypatch.setattr(crew_summary, "_generate_summary", no_summary)

    def run(request_type):
        crew = cq.AppDevelopmentCrew()
        llms = {
            "product_designer_agent": RecordingLLM("designer", DESIGN_SPEC),
            "architect_agent": RecordingLLM("architecte", ARCHITECT_PLAN),
            "diagnostic_agent": RecordingLLM("analyste", "plan de l'analyste"),
            "developer_agent": RecordingLLM("developpeur", "commit"),
            "qa_agent": RecordingLLM("qa", "VERDICT: GO"),
        }
        for name, llm in llms.items():
            getattr(crew, name)().llm = llm
        try:
            asyncio.run(crew.run_dynamic_crew(inputs=INPUTS, request_type=request_type, on_step_change=lambda step: None,
                                              on_task_output_complete=lambda *a, **k: None))
        except Exception as error:      # noqa: BLE001 : les gardes-fous des étapes suivantes peuvent refuser les réponses factices
            print(f"fin de l'exécution factice : {type(error).__name__}")
        return llms
    return run


@pytest.mark.parametrize("request_type", ["FEATURE", "DESIGN_AND_DEV"])
def test_the_analyst_executing_prompt_contains_the_whole_architect_output(run_workflow, request_type):
    analyst = run_workflow(request_type)["diagnostic_agent"]
    assert analyst.prompts, "l'Analyste n'a jamais été appelé"
    assert any(ARCHITECT_PLAN in prompt for prompt in analyst.prompts)


@pytest.mark.parametrize("request_type", ["FEATURE", "DESIGN_AND_DEV"])
def test_the_analyst_planning_prompt_has_the_architect_file_list(run_workflow, request_type):
    """La planification de CrewAI (1er appel de l'Analyste) ne lit que la DESCRIPTION de la tâche, jamais le contexte des tâches
    précédentes : sans le rappel du plan, un modèle léger planifie « à l'aveugle » et invente d'autres améliorations (exécution 137)."""
    analyst = run_workflow(request_type)["diagnostic_agent"]
    planning = analyst.prompts[0]
    assert planning.startswith("You are a strategic planning assistant")
    assert crew_plan.PLAN_BRIEFING_TITLE in planning and "src/engine/powerUpEngine.ts" in planning and "MODIFIER src/App.tsx" in planning
    assert "PLAN_ARCHITECTE_UNIQUE" not in planning                       # seul le rappel de la liste de fichiers, pas tout le plan


def test_the_architect_itself_gets_no_briefing(run_workflow):
    architect = run_workflow("FEATURE")["architect_agent"]
    assert all(crew_plan.PLAN_BRIEFING_TITLE not in prompt for prompt in architect.prompts)


def test_the_analyst_also_receives_the_designer_spec_when_the_workflow_has_a_design_step(run_workflow):
    analyst = run_workflow("DESIGN_AND_DEV")["diagnostic_agent"]
    assert any(DESIGN_SPEC in prompt for prompt in analyst.prompts)


# --- le rappel du plan et le contrôle de recoupement (unitaires) ----------------------------------------------------------

def test_the_briefing_lists_the_planned_files_without_braces_and_is_empty_without_a_file_section():
    brief = crew_plan.plan_briefing(ARCHITECT_PLAN + "\n- CRÉER src/x.ts : objet {a: 1}")
    assert brief.startswith(crew_plan.PLAN_BRIEFING_TITLE) and "- CRÉER src/engine/powerUpEngine.ts" in brief
    assert "{" not in brief and "}" not in brief
    assert crew_plan.plan_briefing("## Cible\n\nrien") == "" and crew_plan.plan_briefing("") == ""


def test_the_briefing_is_added_once_and_only_when_the_plan_has_files():
    crew = cq.AppDevelopmentCrew()
    task = crew.diagnostic_task()
    crew._diagnostic_task_ref = task
    before = task.description
    crew._brief_analyst("## Cible\n\nrien")
    assert task.description == before
    crew._brief_analyst(ARCHITECT_PLAN)
    crew._brief_analyst(ARCHITECT_PLAN)
    assert task.description.count(crew_plan.PLAN_BRIEFING_TITLE) == 1 and task.description.startswith(before)


def test_a_resumed_architecture_step_briefs_the_analyst_too():
    run = crew_run.CrewRun(cq.AppDevelopmentCrew(), INPUTS, "FEATURE", resume_outputs={"architecture": ARCHITECT_PLAN})
    run._apply_resume()
    assert crew_plan.PLAN_BRIEFING_TITLE in run.tasks_by_key["diagnostic"].description


@pytest.mark.parametrize("planned, delivered, flagged", [
    ({"src/App.tsx"}, {"src/App.tsx"}, False),
    ({"src/App.tsx"}, {"frontend/src/App.tsx"}, False),                    # même fin de chemin (racine différente)
    ({"src/App.tsx", "src/x.ts"}, {"src/x.ts", "src/autre.ts"}, False),    # au moins UN fichier du plan
    ({"src/engine/powerUpEngine.ts"}, {"frontend/src/Studio.tsx"}, True),  # exécution 137 : aucun recoupement
    (set(), {"src/x.ts"}, False), ({"src/x.ts"}, set(), False),            # pas de plan / rien de livré : rien à comparer ici
    ({"a.ts", "b.ts", "c.ts", "d.ts"}, {"a.ts", "b.ts"}, False),           # la moitié : suffisant
    ({"a.ts", "b.ts", "c.ts", "d.ts", "e.ts", "f.ts"}, {"e.ts"}, True),    # exécution 138 : 1 fichier sur 6
])
def test_the_plan_mismatch_is_flagged_only_when_no_delivered_file_is_in_the_plan(planned, delivered, flagged):
    issue = crew_plan.plan_mismatch_issue(planned, delivered)
    assert (issue is not None) is flagged
    if flagged:
        assert "plan de l'Architecte" in issue


def test_the_analyst_guardrail_sees_the_architect_plan_of_this_run():
    crew = cq.AppDevelopmentCrew()
    crew._architecture_task_ref = type("T", (), {"output": type("O", (), {"raw": ARCHITECT_PLAN})()})()
    assert "src/engine/powerUpEngine.ts" in crew._plan_mismatch({"frontend/src/Studio.tsx"})
    assert crew._plan_mismatch({"src/App.tsx", "src/engine/powerUpEngine.ts"}) is None
    assert "LevelSelector" in crew._plan_mismatch({"src/App.tsx"})                    # 1 fichier sur 3 : couverture insuffisante
    crew._architecture_task_ref = None                                      # BUGFIX : pas d'architecte, aucun contrôle
    assert crew._plan_mismatch({"frontend/src/Studio.tsx"}) is None


def _architect_ref():
    return type("T", (), {"output": type("O", (), {"raw": ARCHITECT_PLAN})()})()


def _plan_crew():
    from crew_support import new_crew
    crew = new_crew()
    crew._architecture_task_ref = _architect_ref()
    crew._base_readers = (lambda path: ("export const a = 1;\n", None), lambda d: None)
    return crew


def test_the_guardrail_refuses_once_an_analyst_output_that_ignores_the_architect_plan_then_accepts_it():
    from crew_support import output, _edit_block
    crew = _plan_crew()
    off_plan = output(_edit_block("frontend/src/Studio.tsx", "export const a = 1;", "export const a = 2;"))
    ok, message = crew._diagnostic_guardrail(off_plan)
    assert not ok and "plan de l'Architecte" in message and "src/engine/powerUpEngine.ts" in message
    ok, _ = crew._diagnostic_guardrail(off_plan)                           # 2e tentative : accepté (note ⚠️ gérée en aval)
    assert ok


def test_the_guardrail_accepts_an_analyst_output_that_follows_the_plan():
    from crew_support import output, _edit_block
    crew = _plan_crew()
    crew._base_readers = (lambda path: ("export const a = 1;\n", None), lambda d: None)
    both = _edit_block("src/App.tsx", "export const a = 1;", "export const a = 2;") + "\n\n" + _edit_block("src/engine/powerUpEngine.ts", "export const a = 1;", "export const a = 3;")
    ok, _ = crew._diagnostic_guardrail(output(both))
    assert ok


def test_a_partial_delivery_names_the_missing_files_and_withdrawn_files_count_as_handled():
    planned = {"a.ts", "b.ts", "c.ts", "d.ts"}
    issue = crew_plan.plan_mismatch_issue(planned, {"a.ts"})
    assert issue and "1 fichier(s) du plan" in issue and "b.ts, c.ts, d.ts" in issue and "NON réalisé" in issue
    assert crew_plan.plan_mismatch_issue(planned, {"a.ts"}, {"b.ts"}) is None                    # 2 sur 4 traités
    assert crew_plan.plan_mismatch_issue(planned, {"a.ts"}, {"zzz.ts"}) is not None             # un chemin hors plan ne compte pas


def test_the_guardrail_asks_once_for_the_missing_planned_files_then_accepts_and_a_withdrawal_in_the_answer_counts():
    from crew_support import output, _edit_block
    crew = _plan_crew()
    only_app = output(_edit_block("src/App.tsx", "export const a = 1;", "export const a = 2;"))
    ok, message = crew._diagnostic_guardrail(only_app)
    assert not ok and "1 fichier(s) du plan de l'Architecte sur 3" in message and "src/engine/powerUpEngine.ts" in message
    ok, _ = crew._diagnostic_guardrail(only_app)
    assert ok
    crew = _plan_crew()
    withdrawn = output(_edit_block("src/App.tsx", "export const a = 1;", "export const a = 2;")
                       + "\n\nsrc/engine/powerUpEngine.ts NON réalisé : hors périmètre.")
    ok, _ = crew._diagnostic_guardrail(withdrawn)
    assert ok                                                                               # 2 fichiers sur 3 traités


def test_the_briefing_survives_a_new_kickoff_of_the_crew():
    """CrewAI reconstruit la description depuis `_original_description` à chaque kickoff : un nouvel essai de l'équipe (Analyste en échec
    après l'Architecte) ne doit pas perdre le rappel du plan."""
    crew = cq.AppDevelopmentCrew()
    task = crew.diagnostic_task()
    crew._diagnostic_task_ref = task
    task.interpolate_inputs_and_add_conversation_history(INPUTS)            # 1er kickoff : l'original est figé SANS le rappel
    crew._brief_analyst(ARCHITECT_PLAN)                                         # l'Architecte finit pendant ce kickoff
    task.interpolate_inputs_and_add_conversation_history(INPUTS)            # nouvel essai de l'équipe
    assert crew_plan.PLAN_BRIEFING_TITLE in task.description and "src/engine/powerUpEngine.ts" in task.description


@pytest.mark.parametrize("planned, delivered, flagged", [
    ({"src/a/index.ts", "src/b/index.ts", "src/c/index.ts", "src/d/index.ts"}, {"index.ts"}, True),       # 1 fichier ne couvre pas 4 homonymes
    ({"src/a/index.ts", "src/b/index.ts"}, {"src/a/index.ts", "src/b/index.ts"}, False),
    ({"src/App.tsx", "src/admin/App.tsx"}, {"App.tsx"}, False),                                            # 1 sur 2 : la moitié
    ({"src/App.tsx"}, {"frontend/src/App.tsx"}, False),
])
def test_one_delivered_file_covers_only_one_planned_file(planned, delivered, flagged):
    assert (crew_plan.plan_mismatch_issue(planned, delivered) is not None) is flagged


def test_withdrawals_of_an_earlier_attempt_still_count_in_the_next_one():
    from crew_support import output, _edit_block
    crew = _plan_crew()
    first = output(_edit_block("src/App.tsx", "export const a = 1;", "export const a = 2;")
                   + "\n\nsrc/engine/powerUpEngine.ts NON réalisé : hors périmètre.")
    ok, _ = crew._diagnostic_guardrail(first)
    assert ok and "src/engine/powerUpEngine.ts" in crew._plan_withdrawn
    second = output(_edit_block("src/App.tsx", "export const a = 1;", "export const a = 3;"))      # ne répète pas le retrait
    ok, message = crew._diagnostic_guardrail(second)                                              # sans cumul : 1 fichier sur 3 → refus
    assert ok, message


def test_untagged_code_of_the_analyst_is_recovered_by_the_guardrail_then_checked_against_the_plan():
    """Exécution 139 : App.tsx dans un bloc ``` sans balises → plus « aucun fichier exploitable » ; 1 fichier sur 3 du plan → refus unique."""
    from crew_support import output
    crew = _plan_crew()
    code = "import React from 'react';\nconst a = 1;\nexport default a;"
    untagged = output(f"### Rapport\n\nVoici le code mis à jour de `src/App.tsx` :\n\n```tsx\n{code}\n```\n")
    ok, message = crew._diagnostic_guardrail(untagged)
    assert not ok and "Aucun fichier exploitable" not in message and "1 fichier(s) du plan de l'Architecte sur 3" in message
    assert [f["path"] for f in crew._analyst_files] == ["src/App.tsx"]
    ok, _ = crew._diagnostic_guardrail(untagged)
    assert ok and [f["path"] for f in crew._analyst_files] == ["src/App.tsx"]


def test_the_context_plan_gives_every_step_the_outputs_it_needs():
    import crew_run
    assert crew_run.CONTEXT_PLAN == {
        "architecture": ["design"], "diagnostic": ["design", "architecture"], "development": ["diagnostic"],
        "qa": ["design", "architecture", "diagnostic", "development"],
    }


@pytest.mark.parametrize("request_type", ["FEATURE", "DESIGN_AND_DEV", "BUGFIX"])
def test_the_developer_sees_the_current_request_and_the_qa_sees_the_request_and_the_architect_plan(run_workflow, request_type):
    llms = run_workflow(request_type)
    request = INPUTS["user_request"]
    developer, qa = llms["developer_agent"], llms["qa_agent"]
    assert developer.prompts and any(request in prompt for prompt in developer.prompts)
    assert qa.prompts and any(request in prompt for prompt in qa.prompts)
    if request_type == "BUGFIX":                                              # pas d'étape d'architecture : rien à transmettre, et aucune erreur
        assert all(ARCHITECT_PLAN not in prompt for prompt in qa.prompts)
    else:
        assert any(ARCHITECT_PLAN in prompt for prompt in qa.prompts)


def test_the_developer_does_not_receive_the_design_or_the_architect_plan():
    import crew_run
    assert crew_run.CONTEXT_PLAN["development"] == ["diagnostic"]               # le code de l'Analyste seul : ni design ni plan ne diluent le commit


def test_defective_specs_are_sent_back_to_the_designer_once_and_the_architect_reads_the_automatic_note(run_workflow):
    """Bout en bout (vrai crew, LLM enregistreurs) : specs sans titres → UN refus avec la liste exacte, puis acceptation avec une note lue en aval."""
    llms = run_workflow("DESIGN_AND_DEV")
    designer, architect = llms["product_designer_agent"], llms["architect_agent"]
    assert len(designer.prompts) == 2 and "Specs à corriger avant de continuer" in designer.prompts[1]
    assert any("Contrôle automatique des specs" in prompt for prompt in architect.prompts)
