"""Reprise côté crew : étapes du pipeline, préfixe réutilisable, sorties reprises comme contexte."""
# ruff: noqa: F811  (les fixtures importées de resume_support sont reprises comme arguments des tests)
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest  # noqa: E402

import crewquestion as cq  # noqa: E402
import crew_workflow  # noqa: E402
import crew_retry  # noqa: E402
import crew_run  # noqa: E402
from resume_support import TaskOutputFactory  # noqa: E402,F401,F811  (fixtures reprises par nom)


def test_workflow_step_keys_are_the_single_source_of_the_pipeline():
    assert crew_workflow.workflow_step_keys("BUGFIX") == ["diagnostic", "development", "qa"]
    assert crew_workflow.workflow_step_keys("ANALYSE_ONLY") == ["design", "architecture"]
    assert crew_workflow.workflow_step_keys("INCONNU") == crew_workflow.WORKFLOW_STEP_KEYS["DESIGN_AND_DEV"]


@pytest.mark.parametrize("saved, expected", [
    ({"design": "d", "architecture": "a", "diagnostic": "x"}, ["design", "architecture", "diagnostic"]),
    ({"design": "d", "diagnostic": "x"}, ["design"]),  # trou : architecture manque, diagnostic non réutilisable
    ({"architecture": "a"}, []),                       # ne commence pas par la première étape
    ({"design": "  ", "architecture": "a"}, []),       # sortie vide ignorée
    ({"design": "d", "architecture": "a", "development": "dev"}, ["design", "architecture"]),  # development jamais reprise
])
def test_resumable_prefix_is_a_contiguous_prefix_of_resumable_steps(saved, expected):
    assert crew_workflow.resumable_prefix(crew_workflow.workflow_step_keys("DESIGN_AND_DEV"), saved) == expected


class _FakeCrew:
    """Remplace crewai.Crew : exécute chaque tâche reçue en appelant task_callback, sans LLM."""
    received: list[str] = []

    def __init__(self, agents, tasks, task_callback=None, **kwargs):
        self.tasks, self.task_callback = tasks, task_callback

    async def kickoff_async(self, inputs=None):
        for task in self.tasks:
            type(self).received.append(task.agent.role.strip())
            task.output = TaskOutputFactory(f"sortie de {task.agent.role.strip()}")
            self.task_callback(task.output)


def _run(monkeypatch, resume_outputs, request_type="DESIGN_AND_DEV", scope=None):
    _FakeCrew.received = []
    monkeypatch.setattr(crew_run, "Crew", _FakeCrew)
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)
    persisted, steps = [], []

    async def go():
        crew = cq.AppDevelopmentCrew()

        async def fake_summary(*args, **kwargs):
            return "résumé"
        monkeypatch.setattr(crew, "_generate_summary", fake_summary, raising=False)
        try:
            await crew.run_dynamic_crew(
                inputs={"user_request": "x"}, request_type=request_type,
                on_step_change=steps.append,
                on_task_output_complete=lambda role, raw, duration: persisted.append((role, raw)),
                resume_outputs=resume_outputs,
                **({"scope": scope} if scope else {}),
            )
        except Exception as e:  # la fin (résumé, mise en forme) n'est pas l'objet de ce test
            return e
    asyncio.run(go())
    return persisted, steps


def test_resumed_steps_are_not_run_again_and_are_reported_as_persisted(monkeypatch):
    saved = {"design": "conception sauvegardée", "architecture": "architecture sauvegardée"}
    persisted, steps = _run(monkeypatch, saved)
    # Seules les étapes restantes passent par le crew…
    assert len(_FakeCrew.received) == 3 and not any("Designer" in r or "Architecte" in r for r in _FakeCrew.received)
    # …les étapes reprises sont déclarées terminées avec leur sortie d'origine, dans l'ordre…
    assert [raw for _, raw in persisted[:2]] == ["conception sauvegardée", "architecture sauvegardée"]
    # …et la progression repart à la première étape RESTANTE (pas à la première du workflow).
    assert steps[0] == "diagnostic"


def test_a_small_feature_runs_without_the_architecture_step(monkeypatch):
    persisted, steps = _run(monkeypatch, None, request_type="FEATURE", scope="PETIT")
    assert len(_FakeCrew.received) == 3 and not any("Architecte" in r for r in _FakeCrew.received)
    assert steps[0] == "diagnostic"


def test_a_large_or_unscoped_feature_still_runs_the_architecture_step(monkeypatch):
    _run(monkeypatch, None, request_type="FEATURE", scope="GRAND")
    assert len(_FakeCrew.received) == 4 and any("Architecte" in r for r in _FakeCrew.received)


def test_without_resume_outputs_every_step_runs(monkeypatch):
    persisted, steps = _run(monkeypatch, None)
    assert len(_FakeCrew.received) == 5 and steps[0] == "design"


def test_reused_output_becomes_the_context_of_the_next_step(monkeypatch):
    seen = {}

    class _Spy(_FakeCrew):
        async def kickoff_async(self, inputs=None):
            first = self.tasks[0]
            seen["context_raw"] = [getattr(t.output, "raw", None) for t in (first.context or [])]
            await super().kickoff_async(inputs)

    monkeypatch.setattr(crew_run, "Crew", _Spy)
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)

    async def go():
        crew = cq.AppDevelopmentCrew()
        try:
            await crew.run_dynamic_crew(
                inputs={"user_request": "x"}, request_type="FEATURE",
                resume_outputs={"architecture": "plan d'architecture sauvegardé"},
            )
        except Exception:
            pass
    asyncio.run(go())
    # diagnostic (1re étape restante) reçoit en contexte la sortie réutilisée de l'architecture.
    assert "plan d'architecture sauvegardé" in seen["context_raw"]
