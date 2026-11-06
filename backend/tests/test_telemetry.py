"""Traces OpenTelemetry : inactives par défaut (aucun import), hiérarchie exécution → tâche → appel LLM, contenu absent par défaut,
secrets masqués, et une panne de trace ne casse jamais une exécution."""
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest
from crewai.tasks.task_output import TaskOutput
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

import crew_retry
import crew_run
import crewquestion as cq
import llm_limiter
import run_context
import telemetry
from metrics_collect import record_served_model, track_execution_metrics

BACKEND = Path(__file__).resolve().parent.parent
SECRET = "ghp_" + "a1B2c3D4e5" * 4


@pytest.fixture()
def spans(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_ENDPOINT", "LANGSMITH_PROJECT", "OTEL_CAPTURE_CONTENT"):
        monkeypatch.delenv(name, raising=False)
    telemetry.reset()
    exporter = InMemorySpanExporter()
    assert telemetry.setup(SimpleSpanProcessor(exporter))
    yield exporter
    telemetry.reset()


def by_name(exporter, name):
    return [s for s in exporter.get_finished_spans() if s.name == name]


# --- inactif par défaut -------------------------------------------------------------------------------------------------

def test_inactive_without_configuration(monkeypatch):
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    telemetry.reset()
    assert telemetry.setup() is False and telemetry.active() is False
    with telemetry.span("x", a=1):
        telemetry.record_span("y", 1.0, content="c")


@pytest.mark.parametrize("env", [{"LANGSMITH_TRACING": "true"}, {"LANGSMITH_API_KEY": "k"}, {"LANGSMITH_TRACING": "false", "LANGSMITH_API_KEY": "k"}])
def test_both_the_flag_and_the_key_are_required(monkeypatch, env):
    for name in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    telemetry.reset()
    assert telemetry.setup() is False


def test_nothing_from_opentelemetry_is_imported_while_inactive():
    code = ("import sys, telemetry\n"
            "with telemetry.span('x'): telemetry.record_span('y', 1.0)\n"
            "telemetry.setup()\n"
            "print(any(m.startswith('opentelemetry') for m in sys.modules))")
    out = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(BACKEND)!r}); {code}"],
                         capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}, cwd="/tmp")
    assert out.stdout.strip() == "False", out.stderr


def test_setup_builds_the_langsmith_exporter_from_the_environment(monkeypatch):
    seen = {}

    class FakeExporter:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def export(self, spans):
            from opentelemetry.sdk.trace.export import SpanExportResult
            return SpanExportResult.SUCCESS

        def shutdown(self):
            pass

        def force_flush(self, timeout_millis=0):
            return True

    import opentelemetry.exporter.otlp.proto.http.trace_exporter as module
    monkeypatch.setattr(module, "OTLPSpanExporter", FakeExporter)
    monkeypatch.setenv("LANGSMITH_TRACING", "TRUE")
    monkeypatch.setenv("LANGSMITH_API_KEY", " cle ")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://eu.example/")
    monkeypatch.setenv("LANGSMITH_PROJECT", "mon-projet")
    telemetry.reset()
    try:
        assert telemetry.setup() is True and telemetry.setup() is True      # idempotent
        assert seen["endpoint"] == "https://eu.example/otel/v1/traces"
        assert seen["headers"] == {"x-api-key": "cle", "Langsmith-Project": "mon-projet"}
    finally:
        telemetry.reset()


def test_setup_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    import opentelemetry.exporter.otlp.proto.http.trace_exporter as module
    monkeypatch.setattr(module, "OTLPSpanExporter", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    telemetry.reset()
    assert telemetry.setup() is False and telemetry.active() is False


# --- spans ----------------------------------------------------------------------------------------------------------------

def test_recorded_spans_are_children_of_the_enclosing_span_and_carry_the_run_id(spans):
    with run_context.track_run(12), telemetry.span("crew.run", workflow="DESIGN_AND_DEV"):
        telemetry.record_span("agent.task", 2.0, agent="Architecte")
    (parent,), (child,) = by_name(spans, "crew.run"), by_name(spans, "agent.task")
    assert child.parent.span_id == parent.context.span_id and child.context.trace_id == parent.context.trace_id
    assert parent.attributes["run_id"] == child.attributes["run_id"] == "run-12" and parent.attributes["workflow"] == "DESIGN_AND_DEV"
    assert child.end_time - child.start_time == pytest.approx(2e9, rel=0.01)


def test_only_simple_attributes_are_kept(spans):
    telemetry.record_span("s", 0, ok=True, n=3, r=1.5, text="t", none=None, bad=["x"], obj=object())
    assert set(by_name(spans, "s")[0].attributes) == {"ok", "n", "r", "text"}


def test_an_exception_inside_a_span_propagates_and_is_recorded(spans):
    with pytest.raises(ValueError, match="x"):
        with telemetry.span("crew.run"):
            raise ValueError("x")
    (recorded,) = by_name(spans, "crew.run")
    assert recorded.status.status_code.name == "ERROR" and recorded.end_time is not None


def test_a_tracer_failure_never_reaches_the_execution(spans, monkeypatch):
    class Broken:
        def start_as_current_span(self, *a, **k):
            raise RuntimeError("panne")

        def start_span(self, *a, **k):
            raise RuntimeError("panne")

    monkeypatch.setattr(telemetry, "_tracer", Broken())
    with telemetry.span("crew.run"):
        telemetry.record_span("agent.task", 1.0)
    with pytest.raises(KeyError):                                  # l'exception DU CORPS, elle, remonte toujours
        with telemetry.span("crew.run"):
            raise KeyError("corps")


# --- contenu --------------------------------------------------------------------------------------------------------------

def test_no_content_is_sent_by_default(spans):
    telemetry.record_span("agent.task", 1.0, content=f"sortie secrète {SECRET}")
    assert "output" not in by_name(spans, "agent.task")[0].attributes


def test_content_is_masked_and_truncated_when_enabled(spans, monkeypatch):
    monkeypatch.setenv("OTEL_CAPTURE_CONTENT", "true")
    telemetry.record_span("agent.task", 1.0, content=f"début {SECRET} " + "x" * 10_000)
    output = by_name(spans, "agent.task")[0].attributes["output"]
    assert SECRET not in output and "[SECRET MASQUÉ]" in output and len(output) <= telemetry.MAX_CONTENT_CHARS


# --- appels LLM -----------------------------------------------------------------------------------------------------------

class _Usage:
    total_token_count = 321


class _Response:
    usage_metadata = _Usage()
    model_version = "gemini-3.8-flash-001"


class _Models:
    fail = False

    def generate_content(self, *, model, contents, config=None):
        if type(self).fail:
            raise RuntimeError("échec")
        return _Response()


class _AsyncModels:
    async def generate_content(self, *, model, contents, config=None):
        return _Response()

    async def generate_content_stream(self, *, model, contents, config=None):
        async def chunks():
            yield _Response()
        return chunks()


@pytest.fixture()
def hooked(monkeypatch):
    monkeypatch.setattr(llm_limiter, "_installed", set())
    targets = [(_Models, "generate_content", "sync"), (_AsyncModels, "generate_content", "async"),
               (_AsyncModels, "generate_content_stream", "astream")]
    originals = {(cls, name): getattr(cls, name) for cls, name, _ in targets}
    llm_limiter.install(targets=targets)
    yield
    for (cls, name), original in originals.items():
        setattr(cls, name, original)
    _Models.fail = False


def test_each_llm_call_is_a_span_with_model_and_tokens(spans, hooked):
    _Models().generate_content(model="gemini/gemini-3.8-flash", contents="x")

    async def go():
        await _AsyncModels().generate_content(model="gemini-3.5-flash-lite", contents="x")
        async for _ in await _AsyncModels().generate_content_stream(model="gemini-3.5-flash-lite", contents="x"):
            pass
    asyncio.run(go())
    calls = by_name(spans, "llm.call")
    assert [c.attributes["model"] for c in calls] == ["gemini-3.8-flash", "gemini-3.5-flash-lite", "gemini-3.5-flash-lite"]
    assert all(c.attributes["tokens"] == 321 and "error" not in c.attributes for c in calls)


def test_a_failed_llm_call_records_the_error_class_and_still_raises(spans, hooked):
    _Models.fail = True
    with pytest.raises(RuntimeError):
        _Models().generate_content(model="gemini-3.8-flash", contents="x")
    (call,) = by_name(spans, "llm.call")
    assert call.attributes["error"] == "RuntimeError" and "tokens" not in call.attributes


def test_llm_calls_work_the_same_with_traces_off(hooked):
    telemetry.reset()
    assert _Models().generate_content(model="gemini-3.8-flash", contents="x").model_version == "gemini-3.8-flash-001"


# --- tâches d'une exécution -----------------------------------------------------------------------------------------------

class _FakeCrew:
    def __init__(self, agents, tasks, task_callback=None, **kwargs):
        self.tasks, self.task_callback = tasks, task_callback

    async def kickoff_async(self, inputs=None):
        for task in self.tasks:
            record_served_model("srv")
            role = task.agent.role.strip()
            task.output = TaskOutput(description="d", raw=f"sortie de {role}", agent=role)
            self.task_callback(task.output)


def test_each_finished_task_is_a_span_with_agent_and_served_model(spans, monkeypatch):
    monkeypatch.setattr(crew_run, "Crew", _FakeCrew)
    monkeypatch.setattr(crew_retry.quota_mgr, "adaptive_pause", lambda *a, **k: None)

    async def no_summary(*args, **kwargs):
        return None
    import crew_summary
    monkeypatch.setattr(crew_summary, "_generate_summary", no_summary)

    async def go():
        with telemetry.span("crew.run"):
            await cq.AppDevelopmentCrew().run_dynamic_crew(
                inputs={"user_request": "x"}, request_type="DESIGN_AND_DEV", on_step_change=lambda k: None,
                on_task_output_complete=lambda *a, **k: None,
            )
    with track_execution_metrics():
        asyncio.run(go())
    tasks, (root,) = by_name(spans, "agent.task"), by_name(spans, "crew.run")
    assert len(tasks) == 5 and all(t.attributes["models"] == "srv" and t.attributes["agent"] for t in tasks)
    assert all(t.parent.span_id == root.context.span_id for t in tasks)
    assert "output" not in tasks[0].attributes


# --- exécution complète ---------------------------------------------------------------------------------------------------

def test_the_whole_run_is_one_root_span_with_the_workflow_and_the_run_id(spans):
    import execution
    import execution_context

    class Crew:
        async def run_dynamic_crew(self, **kwargs):
            telemetry.record_span("agent.task", 1.0)
            return "résultat"

    async def go():
        with run_context.track_run(9):
            return await execution.run_crew(Crew(), execution_context.RunState(), 9, "BUGFIX", {}, None)
    assert asyncio.run(go()) == "résultat"
    (root,), (task,) = by_name(spans, "crew.run"), by_name(spans, "agent.task")
    assert root.attributes["workflow"] == "BUGFIX" and root.attributes["execution_id"] == 9 and root.attributes["run_id"] == "run-9"
    assert root.attributes["resumed"] is False and task.parent.span_id == root.context.span_id
