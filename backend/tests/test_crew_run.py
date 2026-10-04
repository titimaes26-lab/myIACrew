"""CrewRun (crew_run.py) : retry résumable sur quota, échec attribué à une étape, annonces d'étapes, nettoyage final."""
import asyncio


import pytest
from crewai.tasks.task_output import TaskOutput

import crew_cache
import crew_retry
import crew_run
import crew_summary
import crew_workflow
import crewquestion as cq

DESIGN_AND_DEV = ["design", "architecture", "diagnostic", "development", "qa"]


def _output(task):
    role = task.agent.role.strip()
    return TaskOutput(description="d", raw=f"sortie de {role}", agent=role)


class _Script:
    """Comportement d'un faux Crew, tentative par tentative : `done` tâches terminées (callback appelé) puis `error`."""

    def __init__(self, *attempts):
        self.attempts = list(attempts)
        self.received = []        # rôles reçus par tentative
        self.crews = []

    def crew_class(self, spy=None):
        script = self

        class _ScriptedCrew:
            def __init__(self, agents, tasks, task_callback=None, **kwargs):
                self.tasks, self.task_callback, self.kwargs = tasks, task_callback, kwargs
                for task in tasks:  # comme Crew.set_private_attrs : le callback du crew est posé sur chaque tâche
                    task.callback = task.callback or task_callback
                script.crews.append(self)

            async def kickoff_async(self, inputs=None):
                script.received.append([t.agent.role.strip() for t in self.tasks])
                if spy:
                    spy(self)
                step = script.attempts.pop(0) if script.attempts else {}
                for task in self.tasks[:step.get("done", len(self.tasks))]:
                    task.output = _output(task)
                    self.task_callback(task.output)
                for _ in range(step.get("extra_callbacks", 0)):
                    self.task_callback(_output(self.tasks[-1]))
                if "error" in step:
                    raise step["error"]

        return _ScriptedCrew


@pytest.fixture
def harness(monkeypatch):
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)
    monkeypatch.setattr(crew_retry, "_compute_backoff_wait", lambda *a, **k: 0)

    async def no_summary(*args, **kwargs):
        return None
    monkeypatch.setattr(crew_summary, "_generate_summary", no_summary)
    evictions = []
    monkeypatch.setattr(crew_cache, "evict_memoized_cache_entries", lambda crew: evictions.append(crew))

    def run(script, request_type="DESIGN_AND_DEV", spy=None, **kwargs):
        monkeypatch.setattr(crew_run, "Crew", script.crew_class(spy))
        steps, persisted = [], []
        crew = cq.AppDevelopmentCrew()

        async def go():
            return await crew.run_dynamic_crew(
                inputs={"user_request": "x"}, request_type=request_type,
                on_step_change=steps.append,
                on_task_output_complete=lambda role, raw, duration: persisted.append(role),
                **kwargs,
            )
        try:
            result, error = asyncio.run(go()), None
        except Exception as e:
            result, error = None, e
        return result, error, steps, persisted, crew, evictions

    return run


def test_every_step_is_announced_once_in_order_and_nothing_after_the_last(harness):
    result, error, steps, persisted, _, _ = harness(_Script({}))
    assert error is None
    assert steps == DESIGN_AND_DEV
    assert len(persisted) == 5
    # Le résultat combiné suit l'ordre des étapes, une section par agent.
    assert result.count("\n\n---\n\n## ") == 4


def test_a_quota_error_replays_only_the_remaining_tasks(harness):
    script = _Script({"done": 2, "error": RuntimeError("429 quota exceeded")}, {})
    result, error, steps, persisted, _, _ = harness(script)
    assert error is None
    assert [len(r) for r in script.received] == [5, 3]
    assert not any("Designer" in r or "Architecte" in r for r in script.received[1])
    # Les deux premières sorties sont conservées et chaque agent n'est persisté qu'une fois.
    assert len(persisted) == 5 and len(set(persisted)) == 5
    # Plus aucune étape en cours pendant l'attente, puis reprise à la première étape restante.
    assert None in steps and steps[steps.index(None) + 1] == "diagnostic"
    assert [r for r in persisted] and result.count("\n\n---\n\n## ") >= 4
    assert all(f"sortie de {role}" in result for role in persisted)


def test_retries_are_bounded_then_the_failure_is_attributed_to_the_running_step(harness, monkeypatch):
    monkeypatch.setattr(crew_run, "MAX_RETRIES", 2)
    quota = RuntimeError("429 quota exceeded")
    script = _Script({"done": 1, "error": quota}, {"done": 0, "error": quota}, {"done": 0, "error": quota}, {"done": 0, "error": quota})
    _, error, steps, _, _, _ = harness(script)
    assert isinstance(error, crew_workflow.CrewStepError) and error.__cause__ is quota
    assert len(script.received) == 3          # 1 tentative + 2 reprises, pas une de plus
    assert (error.step_index, error.total_steps) == (2, 5)
    assert error.agent_role == script.received[-1][0]
    assert steps[-1] is None                  # plus rien n'est « en cours » après l'échec


def test_a_non_retryable_error_fails_at_the_step_after_the_last_completed_one(harness):
    script = _Script({"done": 2, "error": ValueError("boom")})
    _, error, steps, persisted, crew, evictions = harness(script)
    assert isinstance(error, crew_workflow.CrewStepError)
    assert len(script.received) == 1
    assert (error.step_index, error.total_steps) == (3, 5)
    assert error.agent_role == script.received[0][2]
    assert str(error) == "boom" and len(persisted) == 2 and steps[-1] is None


def test_a_failure_after_every_task_completed_is_attributed_to_the_finalization(harness):
    script = _Script({"error": ValueError("agrégation impossible")})
    _, error, _, persisted, _, _ = harness(script)
    assert isinstance(error, crew_workflow.CrewStepError)
    assert (error.step_index, error.total_steps) == (5, 5)
    assert error.agent_role == crew_workflow.FINALIZATION_ROLE and len(persisted) == 5


def test_a_callback_beyond_the_number_of_tasks_is_ignored(harness):
    script = _Script({"extra_callbacks": 2})
    result, error, _, persisted, _, _ = harness(script)
    assert error is None and len(persisted) == 5
    assert all(result.count(f"## {role}\n") == 1 for role in persisted)  # aucune section dupliquée par le callback en trop


def test_the_memoized_cache_is_purged_and_callbacks_cleared_once_even_on_failure(harness):
    created = []

    def spy(fake):
        created.extend(fake.tasks)

    script = _Script({"done": 1, "error": RuntimeError("429 quota")}, {"done": 1, "error": ValueError("boom")})
    _, error, _, _, crew, evictions = harness(script, spy=spy)
    assert isinstance(error, crew_workflow.CrewStepError)
    assert evictions == [crew]                # une seule purge pour toute l'exécution (2 tentatives)
    assert created and all(t.callback is None for t in created)


def test_a_retry_that_replays_the_diagnostic_starts_with_a_fresh_guardrail_budget(harness):
    seen = []

    def spy(fake):
        crew = seen_crew[0]
        seen.append((crew._diagnostic_guardrail_failures, list(crew._analyst_files), dict(crew._not_extracted)))
        crew._diagnostic_guardrail_failures = 2
        crew._analyst_files = [{"path": "a.ts", "content": "x"}]
        crew._not_extracted = {"b.ts": "tronqué"}

    seen_crew = []
    original = cq.AppDevelopmentCrew.__init__

    def capture(self, *a, **k):
        original(self, *a, **k)
        seen_crew.append(self)
    cq.AppDevelopmentCrew.__init__ = capture
    try:
        script = _Script({"done": 0, "error": RuntimeError("429 quota")}, {})
        _, error, _, _, _, _ = harness(script, request_type="BUGFIX", spy=spy)
    finally:
        cq.AppDevelopmentCrew.__init__ = original
    assert error is None
    assert seen == [(0, [], {}), (0, [], {})]


def test_the_crewai_log_file_is_off_by_default_and_opt_in_through_the_environment(harness, monkeypatch):
    monkeypatch.delenv("CREW_LOG_FILE", raising=False)
    script = _Script({})
    harness(script)
    assert script.crews[0].kwargs["output_log_file"] is None
    monkeypatch.setenv("CREW_LOG_FILE", "/tmp/crew.log")
    script = _Script({})
    harness(script)
    assert script.crews[0].kwargs["output_log_file"] == "/tmp/crew.log"
