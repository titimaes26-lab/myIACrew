"""Retry dans l'exécution : CrewAI garde le callback de la tentative précédente sur les tâches déjà utilisées et appelle les DEUX à
chaque fin de tâche. Exécution 135 (Render) : le Designer apparaissait deux fois, l'Architecte avait disparu, le résumé annonçait
« 2 étapes terminées »."""
import asyncio
from typing import Any

import pytest
from crewai import Agent, Crew, Process, Task
from crewai.llms.base_llm import BaseLLM
from crewai.tasks.task_output import TaskOutput

import crew_cache
import crew_result
import crew_retry
import crew_run
import crew_summary
import crew_workflow
import crewquestion as cq
from errors import ErrorCode, classify_exception


INPUTS = {name: "x" for name in (
    "user_request", "conversation_context", "work_branch", "repo_instructions", "base_branch", "repo_owner", "repo_name", "repo_snapshot", "previous_plan",
)}


class FakeLLM(BaseLLM):
    """LLM hors ligne : répond « Final Answer: … » ; échoue (503) aux appels numérotés dans `fail_on`."""

    def __init__(self, name: str, fail_on: tuple[int, ...] = ()):
        super().__init__(model=f"fake-{name}", temperature=0)
        self.name, self.fail_on, self.n = name, set(fail_on), 0

    def call(self, messages: Any, tools: Any = None, callbacks: Any = None, available_functions: Any = None, from_task: Any = None, from_agent: Any = None, **kwargs: Any) -> Any:
        self.n += 1
        if self.n in self.fail_on:
            raise RuntimeError("503 UNAVAILABLE. This model is currently experiencing high demand.")
        return f"Final Answer: sortie de {self.name} (appel {self.n})"

    def supports_function_calling(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 8000


def _plain_agent(role: str, llm: BaseLLM) -> Agent:
    return Agent(role=role, goal="g", backstory="b", llm=llm, max_iter=3, max_retry_limit=0, verbose=False)


# --- la prémisse (comportement de CrewAI, version épinglée) ---------------------------------------------------------------

def test_crewai_keeps_the_previous_callback_on_a_reused_task_and_calls_both():
    designer, architect = FakeLLM("designer"), FakeLLM("architecte", fail_on=(1,))
    t1 = Task(description="design", expected_output="spec", agent=_plain_agent("Designer", designer))
    t2 = Task(description="architecture", expected_output="archi", agent=_plain_agent("Architecte", architect), context=[t1])
    seen: list[tuple[str, int]] = []

    def kick(tag, tasks):
        crew = Crew(agents=list({t.agent for t in tasks}), tasks=tasks, process=Process.sequential, task_callback=lambda out: seen.append((tag, id(out))))
        return asyncio.run(crew.kickoff_async(inputs={}))

    with pytest.raises(RuntimeError):
        kick("A1", [t1, t2])                                     # le Designer termine, l'Architecte échoue (503)
    kick("A2", [t2])
    second = [entry for entry in seen if entry[1] == seen[-1][1]]
    assert [tag for tag, _ in second] == ["A1", "A2"]            # DEUX appels pour UNE seule fin de tâche, le premier du callback périmé
    t2.callback = None                                           # la remise à zéro de CrewRun._run_attempts
    seen.clear()
    t2.output = None
    kick("A3", [t2])
    assert [tag for tag, _ in seen] == ["A3"]


# --- CrewRun de bout en bout avec de vrais Crew et des LLM factices -------------------------------------------------------

@pytest.fixture
def fake_crew(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)          # les tâches écrivent leur sortie dans `docs/…` (output_file) : jamais dans le dépôt
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)
    monkeypatch.setattr(crew_retry, "_compute_backoff_wait", lambda *a, **k: 0)
    monkeypatch.setattr(crew_cache, "evict_memoized_cache_entries", lambda crew: None)

    async def no_summary(*args, **kwargs):
        return None
    monkeypatch.setattr(crew_summary, "_generate_summary", no_summary)

    def build(designer_fail=(), architect_fail=()):
        crew = cq.AppDevelopmentCrew()
        crew.product_designer_agent().llm = FakeLLM("designer", designer_fail)
        crew.architect_agent().llm = FakeLLM("architecte", architect_fail)
        return crew

    def run(crew):
        persisted: list[tuple[str, str]] = []
        result = asyncio.run(crew.run_dynamic_crew(
            inputs=INPUTS, request_type="ANALYSE_ONLY", on_step_change=lambda step: None,
            on_task_output_complete=lambda role, raw, duration, **kw: persisted.append((role, raw)),
        ))
        return result, persisted
    return build, run


def _headings(result: str) -> list[str]:
    return [line for line in result.splitlines() if line.startswith("## ") and ("Designer" in line or "Architecte" in line)]


def test_a_retry_after_a_failure_in_the_architect_keeps_each_step_output_once(fake_crew):
    build, run = fake_crew
    result, persisted = run(build(architect_fail=(1, 2)))        # le Designer termine, l'Architecte échoue 2 fois (essai de l'agent compris) puis réussit à la reprise
    assert _headings(result) == ["## Lead Product / Game Designer", "## Architecte Logiciel React / TypeScript"]
    assert "sortie de designer" in result and "sortie de architecte" in result
    assert [role for role, _ in persisted] == ["Lead Product / Game Designer", "Architecte Logiciel React / TypeScript"]


def test_a_retry_from_scratch_after_a_failure_in_the_designer_keeps_each_step_output_once(fake_crew):
    build, run = fake_crew
    result, persisted = run(build(designer_fail=(1, 2)))         # rien ne termine à la 1re tentative : la 2e rejoue les DEUX tâches
    assert _headings(result) == ["## Lead Product / Game Designer", "## Architecte Logiciel React / TypeScript"]
    sections = result.split(crew_summary.SUMMARY_SENTINEL)[0]       # hors résumé, qui cite lui-même les sorties
    assert sections.count("sortie de designer") == 1 and sections.count("sortie de architecte") == 1
    assert [role for role, _ in persisted] == ["Lead Product / Game Designer", "Architecte Logiciel React / TypeScript"]


def test_without_any_retry_the_result_is_unchanged(fake_crew):
    build, run = fake_crew
    result, persisted = run(build())
    assert _headings(result) == ["## Lead Product / Game Designer", "## Architecte Logiciel React / TypeScript"]
    assert len(persisted) == 2


# --- le garde-fou : jamais de faux « succès » -----------------------------------------------------------------------------

class _Task:
    def __init__(self, role):
        self.agent = type("A", (), {"role": role})()


def _out(role):
    return TaskOutput(description="d", raw="r", agent=role)


def test_the_integrity_check_accepts_one_distinct_output_per_step():
    crew_result.check_outputs(["design", "architecture"], {"design": _out("Designer"), "architecture": _out(" Architecte ")}, {"design": _Task("Designer"), "architecture": _Task("Architecte")})


def test_the_integrity_check_rejects_a_missing_step_and_an_output_from_another_role():
    tasks = {"design": _Task("Designer"), "architecture": _Task("Architecte")}
    with pytest.raises(crew_result.ResultIntegrityError, match="architecture.*sans sortie"):
        crew_result.check_outputs(["design", "architecture"], {"design": _out("Designer")}, tasks)
    with pytest.raises(crew_result.ResultIntegrityError, match="sortie de « Designer » au lieu de « Architecte »"):
        crew_result.check_outputs(["design", "architecture"], {"design": _out("Designer"), "architecture": _out("Designer")}, tasks)


def test_a_duplicated_output_fails_the_run_instead_of_succeeding(fake_crew, monkeypatch):
    """Garde-fou seul (sans la correction de fond) : si une sortie était rangée à la mauvaise étape, l'exécution échoue."""
    build, run = fake_crew
    monkeypatch.setattr(crew_run.CrewRun, "_on_task_complete", lambda self, output, remaining: self.completed_outputs.update(
        {"design": output, "architecture": output}) or self.completed_keys.extend(["design", "architecture"]))
    with pytest.raises(crew_result.ResultIntegrityError, match="Résultat incohérent") as raised:
        run(build())
    info = classify_exception(raised.value)                                  # jamais un « Erreur interne » générique
    assert info.code == ErrorCode.RESULT_INCONSISTENT and info.retryable is True
    assert "incomplet" in info.message and "Résultat incohérent" not in info.message   # le détail reste dans le journal


def test_workflow_steps_for_analysis_are_design_then_architecture():
    assert crew_workflow.workflow_step_keys("ANALYSE_ONLY", None) == ["design", "architecture"]


# --- chaque défense seule (la correction de fond ET la défense en profondeur) --------------------------------------------

def _run_object(persisted):
    return crew_run.CrewRun(cq.AppDevelopmentCrew(), INPUTS, "ANALYSE_ONLY", on_task_output_complete=lambda role, raw, duration, **kw: persisted.append(role))


def test_the_same_task_output_delivered_twice_is_recorded_once():
    persisted: list[str] = []
    run = _run_object(persisted)
    remaining = list(run.selected)
    designer_output = TaskOutput(description="d", raw="spec", agent="Lead Product / Game Designer")
    run._on_task_complete(designer_output, remaining)
    run._on_task_complete(designer_output, remaining)                      # second appel CrewAI pour la même fin de tâche
    assert run.completed_keys == ["design"] and list(run.completed_outputs) == ["design"] and run.attempt_completed == 1
    assert persisted == ["Lead Product / Game Designer"]
    architect_output = TaskOutput(description="d", raw="archi", agent="Architecte Logiciel React / TypeScript")
    run._on_task_complete(architect_output, remaining)
    assert run.completed_keys == ["design", "architecture"] and run.completed_outputs["architecture"] is architect_output


def test_every_attempt_starts_with_the_crew_callback_on_its_pending_tasks(monkeypatch):
    """Sans la remise à zéro, une tâche réutilisée garde le callback de la tentative précédente (voir la prémisse ci-dessus)."""
    monkeypatch.setattr(crew_retry, "_compute_backoff_wait", lambda *a, **k: 0)
    monkeypatch.setattr(crew_cache, "evict_memoized_cache_entries", lambda crew: None)
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)
    attempts: list[list[bool]] = []

    class _Crew:
        def __init__(self, agents, tasks, task_callback=None, **kwargs):
            self.tasks, self.task_callback = tasks, task_callback
            for task in tasks:                                             # comme Crew._set_tasks_callbacks
                if not task.callback:
                    task.callback = task_callback

        async def kickoff_async(self, inputs=None):
            attempts.append([task.callback is self.task_callback for task in self.tasks])
            if len(attempts) == 1:
                raise RuntimeError("503 UNAVAILABLE high demand")
            for task in self.tasks:
                task.output = TaskOutput(description="d", raw="r", agent=task.agent.role.strip())
                self.task_callback(task.output)

    monkeypatch.setattr(crew_run, "Crew", _Crew)

    async def no_summary(*args, **kwargs):
        return None
    monkeypatch.setattr(crew_summary, "_generate_summary", no_summary)
    run = _run_object([])
    asyncio.run(run.execute())
    assert attempts == [[True, True], [True, True]]                         # 2e tentative : le callback de la 2e tentative, pas celui de la 1re


def test_the_task_outputs_are_written_under_the_temporary_directory_not_the_repository(fake_crew, tmp_path):
    build, run = fake_crew
    run(build())
    assert (tmp_path / "docs" / "specs_design.md").exists() and (tmp_path / "docs" / "architecture_spec.md").exists()
