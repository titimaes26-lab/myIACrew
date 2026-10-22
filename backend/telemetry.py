"""Traces OpenTelemetry (export OTLP/HTTP vers LangSmith) : INACTIVES par défaut, et jamais bloquantes.

Activation : `LANGSMITH_TRACING=true` ET `LANGSMITH_API_KEY`. Sans cela, `span` et `record_span` ne font rien et aucun module
`opentelemetry` n'est importé. Variables : `LANGSMITH_ENDPOINT` (défaut https://api.smith.langchain.com), `LANGSMITH_PROJECT`
(défaut « myiacrew »), `OTEL_CAPTURE_CONTENT=true` (envoie aussi la sortie de chaque agent, secrets masqués ; sinon AUCUN texte
des prompts ni des sorties ne quitte le serveur : durées, modèles, tokens et codes d'erreur seulement).

Le fournisseur de traces n'est PAS posé comme fournisseur global : CrewAI a sa propre télémétrie OpenTelemetry, que nous ne
devons ni remplacer ni polluer. Une erreur de trace est journalisée au niveau debug et ne remonte jamais à l'exécution.
"""
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import input_guards
import run_context
from logs import get_logger

log = get_logger("telemetry")

DEFAULT_ENDPOINT = "https://api.smith.langchain.com"
MAX_CONTENT_CHARS = 4000
_tracer: Any = None


def _enabled_by_env() -> bool:
    return os.getenv("LANGSMITH_TRACING", "").strip().lower() == "true" and bool(os.getenv("LANGSMITH_API_KEY", "").strip())


def capture_content() -> bool:
    return os.getenv("OTEL_CAPTURE_CONTENT", "").strip().lower() == "true"


def active() -> bool:
    return _tracer is not None


def setup(processor: Any = None) -> bool:
    """Active les traces (idempotent) ; `processor` : processeur de spans fourni (tests). Renvoie True si elles sont actives."""
    global _tracer
    if _tracer is not None:
        return True
    if processor is None and not _enabled_by_env():
        return False
    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider

        if processor is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            base = os.getenv("LANGSMITH_ENDPOINT", "").strip().rstrip("/") or DEFAULT_ENDPOINT
            headers = {"x-api-key": os.environ["LANGSMITH_API_KEY"].strip(), "Langsmith-Project": os.getenv("LANGSMITH_PROJECT", "").strip() or "myiacrew"}
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/otel/v1/traces", headers=headers, timeout=10))
        provider = TracerProvider(resource=Resource.create({"service.name": "myiacrew"}))
        provider.add_span_processor(processor)
        _tracer = provider.get_tracer("myiacrew")
    except Exception as e:
        log.warning(f"traces OpenTelemetry désactivées : {type(e).__name__}: {e}")
        return False
    log.info("traces OpenTelemetry activées (contenu des sorties : " + ("envoyé" if capture_content() else "non envoyé") + ")")
    return True


def reset() -> None:
    """Désactive les traces (tests)."""
    global _tracer
    _tracer = None


def _attributes(attrs: dict[str, Any]) -> dict[str, Any]:
    run_id = run_context.current_run_id()
    merged = {**({"run_id": run_id} if run_id else {}), **attrs}
    return {k: v for k, v in merged.items() if isinstance(v, (str, int, float, bool))}


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[None]:
    """Span englobant (devient le parent des spans créés dedans) ; ne fait rien si les traces sont inactives."""
    if _tracer is None:
        yield
        return
    try:
        manager = _tracer.start_as_current_span(name, attributes=_attributes(attrs))
        manager.__enter__()
    except Exception as e:
        log.debug(f"span « {name} » non créé : {type(e).__name__}: {e}")
        yield
        return
    try:
        yield
    except BaseException as error:
        _close(manager, error)
        raise
    _close(manager, None)


def _close(manager: Any, error: Optional[BaseException]) -> None:
    try:
        manager.__exit__(type(error) if error else None, error, error.__traceback__ if error else None)
    except Exception as e:
        log.debug(f"span non fermé : {type(e).__name__}: {e}")


def record_span(name: str, seconds: Optional[float], content: Optional[str] = None, error: Optional[str] = None, **attrs: Any) -> None:
    """Span DÉJÀ terminé (durée connue après coup) : enfant du span courant. `content` n'est envoyé que si OTEL_CAPTURE_CONTENT."""
    if _tracer is None:
        return
    try:
        values = dict(attrs)
        if error:
            values["error"] = error
        if content is not None and capture_content():
            values["output"] = input_guards.guard_text(content, "trace")[:MAX_CONTENT_CHARS]
        end = time.time_ns()
        start = end - int(max(seconds or 0.0, 0.0) * 1e9)
        recorded = _tracer.start_span(name, attributes=_attributes(values), start_time=start)
        recorded.end(end_time=end)
    except Exception as e:
        log.debug(f"span « {name} » non enregistré : {type(e).__name__}: {e}")
