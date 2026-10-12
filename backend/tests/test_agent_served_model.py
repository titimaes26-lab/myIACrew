"""Le modèle indiqué dans le rapport d'un agent est celui que l'API a réellement servi (et pas seulement le modèle configuré),
y compris pour une étape reprise : son point de reprise garde le modèle d'origine."""
import asyncio

import pytest
from sqlmodel import Session

import crew_retry
import crew_run
import crewquestion as cq
import execution_persistence
import llm_limiter
import llm_usage
from execution_persistence import ResumeOutputs
from metrics_collect import ExecutionMetrics, record_served_model, track_execution_metrics
from crewai.tasks.task_output import TaskOutput
from run_support import _new_execution
from database import ExecutionCheckpoint, ExecutionHistory


@pytest.fixture(autouse=True)
def fresh_persistence():
    execution_persistence.persisted_agents.clear()      # idempotence par identifiant d'exécution : repart de zéro à chaque test
    yield
    execution_persistence.persisted_agents.clear()


# --- lecture du modèle servi et collecte -----------------------------------------------------------------------------

class _Response:
    def __init__(self, version):
        self.model_version = version


def test_the_served_model_is_read_from_the_response_version():
    assert llm_usage.served_model(_Response(" gemini-3.8-flash ")) == "gemini-3.8-flash"
    assert [llm_usage.served_model(x) for x in (_Response(None), _Response(""), _Response(3), object(), None)] == [None] * 5


def test_metrics_hand_over_the_models_served_since_the_last_agent_then_start_again():
    metrics = ExecutionMetrics()
    for model in ("a", "b", "a"):
        metrics.record_served_model(model)
    assert metrics.take_served_models() == ["a", "b"] and metrics.take_served_models() == []


def test_the_served_model_is_recorded_only_inside_a_tracked_execution():
    record_served_model("hors exécution")                      # sans effet, sans erreur
    with track_execution_metrics() as metrics:
        record_served_model("gemini-x")
        assert metrics.take_served_models() == ["gemini-x"]


# --- enveloppes du client google-genai -------------------------------------------------------------------------------

class _Models:
    def generate_content(self, *, model, contents, config=None):
        return _Response("gemini-3.8-flash-001")

    def generate_content_stream(self, *, model, contents, config=None):
        yield _Response(None)
        yield _Response("gemini-3.8-flash-002")


class _AsyncModels:
    async def generate_content(self, *, model, contents, config=None):
        return _Response("gemini-async")

    async def generate_content_stream(self, *, model, contents, config=None):
        async def chunks():
            yield _Response("gemini-astream")
            yield _Response(None)
        return chunks()


@pytest.fixture()
def hooked(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(llm_limiter, "_installed", set())
    monkeypatch.setattr(llm_limiter, "_served_hook", None)
    targets = [(_Models, "generate_content", "sync"), (_Models, "generate_content_stream", "stream"),
               (_AsyncModels, "generate_content", "async"), (_AsyncModels, "generate_content_stream", "astream")]
    originals = {(cls, name): getattr(cls, name) for cls, name, _ in targets}
    llm_limiter.install(targets=targets, on_served=seen.append)
    yield seen
    for (cls, name), original in originals.items():
        setattr(cls, name, original)


def test_every_kind_of_call_reports_the_model_the_api_served(hooked):
    _Models().generate_content(model="gemini-3.8-flash", contents="x")
    assert list(_Models().generate_content_stream(model="gemini-3.8-flash", contents="x"))
    async def scenario():
        await _AsyncModels().generate_content(model="gemini-3.8-flash", contents="x")
        stream = await _AsyncModels().generate_content_stream(model="gemini-3.8-flash", contents="x")
        return [chunk async for chunk in stream]
    asyncio.run(scenario())
    assert hooked == ["gemini-3.8-flash-001", "gemini-3.8-flash-002", "gemini-async", "gemini-astream"]


def test_a_failing_hook_never_breaks_the_call(hooked, monkeypatch):
    def boom(model):
        raise ValueError("mesure cassée")
    monkeypatch.setattr(llm_limiter, "_served_hook", boom)
    assert _Models().generate_content(model="gemini-3.8-flash", contents="x").model_version == "gemini-3.8-flash-001"


# --- rapport de chaque agent : modèle servi, puis configuré ----------------------------------------------------------

class _FakeCrew:
    received: list[str] = []
    served: list[str] = []          # modèle que l'API « sert » à chaque tâche, dans l'ordre

    def __init__(self, agents, tasks, task_callback=None, **kwargs):
        self.tasks, self.task_callback = tasks, task_callback

    async def kickoff_async(self, inputs=None):
        for task in self.tasks:
            type(self).received.append(task.agent.role.strip())
            if type(self).served:
                record_served_model(type(self).served.pop(0))
            role = task.agent.role.strip()
            task.output = TaskOutput(description="d", raw=f"sortie de {role}", agent=role)
            self.task_callback(task.output)


def _run(monkeypatch, served, resume_outputs=None):
    _FakeCrew.received, _FakeCrew.served = [], list(served)
    monkeypatch.setattr(crew_run, "Crew", _FakeCrew)
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)

    async def no_summary(*args, **kwargs):
        return None
    import crew_summary
    monkeypatch.setattr(crew_summary, "_generate_summary", no_summary)
    calls, steps = [], []

    async def go():
        crew = cq.AppDevelopmentCrew()
        return await crew.run_dynamic_crew(
            inputs={"user_request": "x"}, request_type="DESIGN_AND_DEV", on_step_change=steps.append,
            on_task_output_complete=lambda role, raw, duration, **extra: calls.append((role, extra)),
            resume_outputs=resume_outputs,
        )
    with track_execution_metrics():
        result = asyncio.run(go())
    return calls, result


def test_each_agent_reports_the_model_served_during_its_own_task(monkeypatch):
    calls, result = _run(monkeypatch, ["srv-design", "srv-archi", "srv-diag", "srv-dev", "srv-qa"])
    assert [extra for _, extra in calls] == [{"model": m} for m in ("srv-design", "srv-archi", "srv-diag", "srv-dev", "srv-qa")]
    for model in ("srv-design", "srv-archi", "srv-diag", "srv-dev", "srv-qa"):
        assert f"<!--agent-model:{model}-->" in result
    assert "gemini-3.8-flash-->" not in result and "flash-lite-->" not in result   # plus le modèle configuré quand l'API en a dit un


def test_without_any_reported_model_the_configured_one_is_used(monkeypatch):
    calls, result = _run(monkeypatch, [])
    assert all(extra == {} for _, extra in calls)
    assert result.count("<!--agent-model:gemini-3.8-flash-->") == 4 and result.count("<!--agent-model:gemini-3.5-flash-lite-->") == 1


def test_a_resumed_step_keeps_the_model_that_produced_it(monkeypatch):
    saved = ResumeOutputs(
        {"design": "conception sauvegardée", "architecture": "architecture sauvegardée"},
        {"design": "srv-design-old", "architecture": "srv-archi-old"},
    )
    calls, result = _run(monkeypatch, ["srv-diag", "srv-dev", "srv-qa"], saved)
    assert [extra for _, extra in calls[:2]] == [{"model": "srv-design-old"}, {"model": "srv-archi-old"}]
    assert "<!--agent-model:srv-design-old-->" in result and "<!--agent-model:srv-qa-->" in result


def test_a_resumed_step_without_a_saved_model_falls_back_to_the_configured_one(monkeypatch):
    calls, result = _run(monkeypatch, ["srv-diag", "srv-dev", "srv-qa"], {"design": "a", "architecture": "b"})
    assert calls[0][1] == {} and calls[1][1] == {}
    assert result.count("<!--agent-model:gemini-3.8-flash-->") == 2          # design et architecture, modèle configuré


# --- points de reprise -------------------------------------------------------------------------------------------------

def test_checkpoints_store_the_served_model_and_give_it_back(engine):
    execution_id, _ = _new_execution(engine)
    execution_persistence.persist_completed_agent(execution_id, "Lead Product / Game Designer", "conception", 2.0, model="srv-design")
    execution_persistence.persist_completed_agent(execution_id, "Architecte Logiciel React / TypeScript", "plan", 3.0)   # modèle configuré
    with Session(engine) as db:
        loaded = execution_persistence.load_checkpoints(db, execution_id)
    assert dict(loaded) == {"design": "conception", "architecture": "plan"}
    assert loaded.models == {"design": "srv-design", "architecture": "gemini-3.8-flash"}
    chosen = execution_persistence.select_outputs(loaded, ["design"])
    assert dict(chosen) == {"design": "conception"} and chosen.models == {"design": "srv-design"}
    assert execution_persistence.select_outputs({"design": "x"}, ["design"]).models == {}      # dict ordinaire accepté


def test_the_progressive_section_carries_the_served_model(engine):
    execution_id, _ = _new_execution(engine)
    execution_persistence.persist_completed_agent(execution_id, "Lead Product / Game Designer", "conception", 2.0, model="srv-design")
    with Session(engine) as db:
        assert "<!--agent-model:srv-design-->" in db.get(ExecutionHistory, execution_id).result
        assert db.get(ExecutionCheckpoint, 1).model == "srv-design"
