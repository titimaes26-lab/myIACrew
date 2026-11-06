"""Traces OpenTelemetry (export OTLP/HTTP vers LangSmith) : INACTIVES par défaut, et jamais bloquantes.

Activation : `LANGSMITH_TRACING=true` ET `LANGSMITH_API_KEY`. Sans cela, `span` et `record_span` ne font rien et aucun module
`opentelemetry` n'est importé. Variables : `LANGSMITH_ENDPOINT` (défaut https://api.smith.langchain.com), `LANGSMITH_PROJECT`
(défaut « myiacrew »), `OTEL_CAPTURE_CONTENT=true` (envoie aussi la sortie de chaque agent, secrets masqués ; sinon AUCUN texte
des prompts ni des sorties ne quitte le serveur : durées, modèles, tokens et codes d'erreur seulement).

Le fournisseur de traces n'est PAS posé comme fournisseur global : CrewAI a sa propre télémétrie OpenTelemetry, que nous ne
devons ni remplacer ni polluer. Une erreur de trace est journalisée (la première en WARNING, les suivantes en debug) et ne remonte jamais à l'exécution ; les premiers envois et chaque échec d'envoi sont journalisés.
"""
import os
import time
from urllib.parse import urlsplit
from contextlib import contextmanager
from typing import Any, Iterator, Optional

import input_guards
import run_context
from logs import get_logger

log = get_logger("telemetry")

DEFAULT_ENDPOINT = "https://api.smith.langchain.com"
MAX_CONTENT_CHARS = 4000
_tracer: Any = None
LOGGED_EXPORTS = 5          # les premiers envois sont journalisés (INFO), ensuite seuls les échecs : la preuve que ça part, sans inonder
_span_errors = 0            # erreurs de création/fermeture de span déjà vues (la première seule est journalisée)


def _host(url: str) -> str:
    """Schéma et hôte d'une adresse (jamais le chemin ni d'éventuels identifiants)."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else "adresse illisible"


class _LoggingExporter:
    """Enveloppe un exporteur de spans : journalise le résultat des premiers envois puis les échecs, ne lève jamais.
    Sans cela, un envoi refusé ou perdu ne laisse aucune trace dans nos journaux."""

    def __init__(self, inner: Any, target: str):
        self._inner, self._target, self._sent = inner, target, 0

    def export(self, spans: Any) -> Any:
        try:
            result = self._inner.export(spans)
        except Exception as e:
            log.warning(f"envoi de {len(spans)} span(s) vers {self._target} en échec : {type(e).__name__}")
            raise
        ok = getattr(result, "name", str(result)) == "SUCCESS"
        if not ok:
            log.warning(f"envoi de {len(spans)} span(s) vers {self._target} refusé ou perdu ({getattr(result, 'name', result)}) : clé, région (LANGSMITH_ENDPOINT) ?")
        elif self._sent < LOGGED_EXPORTS:
            self._sent += 1
            log.info(f"{len(spans)} span(s) envoyé(s) vers {self._target} : accepté")
        return result

    def shutdown(self) -> Any:
        return self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> Any:
        return self._inner.force_flush(timeout_millis)


def _note_span_error(what: str, name: str, error: Exception) -> None:
    global _span_errors
    _span_errors += 1
    if _span_errors == 1:
        log.warning(f"span « {name} » {what} : {type(error).__name__}: {error} (les suivantes ne sont plus signalées)")
    else:
        log.debug(f"span « {name} » {what} : {type(error).__name__}: {error}")


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
        if processor is None:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            base = os.getenv("LANGSMITH_ENDPOINT", "").strip().rstrip("/") or DEFAULT_ENDPOINT
            headers = {"x-api-key": os.environ["LANGSMITH_API_KEY"].strip(), "Langsmith-Project": os.getenv("LANGSMITH_PROJECT", "").strip() or "myiacrew"}
            exporter = _LoggingExporter(OTLPSpanExporter(endpoint=f"{base}/otel/v1/traces", headers=headers, timeout=10), _host(base))
            processor = BatchSpanProcessor(exporter)  # type: ignore[arg-type]
            _announce(base, headers["Langsmith-Project"])
        provider = _new_provider(Resource.create({"service.name": "myiacrew"}))
        provider.add_span_processor(processor)
        _tracer = provider.get_tracer("myiacrew")
    except Exception as e:
        log.warning(f"traces OpenTelemetry désactivées : {type(e).__name__}: {e}")
        return False
    log.info("traces OpenTelemetry activées (contenu des sorties : " + ("envoyé" if capture_content() else "non envoyé") + ")")
    return True


def _new_provider(resource: Any) -> Any:
    """Fournisseur de traces INDÉPENDANT de OTEL_SDK_DISABLED : cette variable (souvent posée pour couper la télémétrie de CrewAI) rend
    silencieusement NUL tout fournisseur créé tant qu'elle vaut « true » : aucun span, aucun envoi, aucune erreur. Nos traces sont
    demandées explicitement (LANGSMITH_TRACING + clé) : la variable n'est donc ignorée que le temps de la création, puis rétablie."""
    from opentelemetry.sdk.trace import TracerProvider

    saved = os.environ.pop("OTEL_SDK_DISABLED", None)
    try:
        return TracerProvider(resource=resource)
    finally:
        if saved is not None:
            os.environ["OTEL_SDK_DISABLED"] = saved
            if saved.strip().lower() == "true":
                log.info("OTEL_SDK_DISABLED=true est ignorée pour les traces LangSmith (la télémétrie de CrewAI reste coupée)")


def _announce(base: str, project: str) -> None:
    """Où partent les traces (hôte et projet, jamais la clé). L'adresse par défaut est américaine : un compte EU doit définir LANGSMITH_ENDPOINT."""
    log.info(f"traces envoyées vers {_host(base)} (projet « {project} »)")
    if base == DEFAULT_ENDPOINT:
        log.warning("LANGSMITH_ENDPOINT absent : adresse américaine utilisée ; un compte de région EU doit définir https://eu.api.smith.langchain.com")


def reset() -> None:
    """Désactive les traces (tests)."""
    global _tracer, _span_errors
    _tracer = None
    _span_errors = 0


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
        _note_span_error("non créé", name, e)
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
        _note_span_error("non fermé", "?", e)


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
        _note_span_error("non enregistré", name, e)
