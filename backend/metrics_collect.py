"""Mesure de la performance PAR AGENT : appels LLM réels, tokens, appels d'outils, durée.

Les compteurs viennent des événements CrewAI (un événement par appel LLM terminé, par appel d'outil),
pas d'un décompte maison : l'ancien « api_calls_count » ne comptait que les tentatives de kickoff
et sous-estimait fortement la consommation du quota Gemini. Le bus d'événements copie le contexte
(contextvars) pour chaque handler : l'exécution suivie par track_execution_metrics reste donc isolée
des exécutions concurrentes. Les agrégats (percentiles, moyennes, séries par jour) sont dans metrics_report."""
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import metrics_roles
from logs import get_logger

log = get_logger("metrics")

# --- Usage de tokens ----------------------------------------------------------------------------
_PROMPT_KEYS = ("prompt_tokens", "input_tokens", "prompt_token_count")

_COMPLETION_KEYS = ("completion_tokens", "output_tokens", "candidates_token_count")

_TOTAL_KEYS = ("total_tokens", "total_token_count")

def _first_int(data: dict, keys: Iterable[str]) -> Optional[int]:
    for key in keys:
        value = data.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None

def parse_usage(usage: Any) -> Optional[tuple[int, int, int]]:
    """(prompt, completion, total) ou None si le fournisseur n'a rien renvoyé d'exploitable : un
    usage absent n'est PAS « 0 token », il doit rester distinguable pour ne pas fausser les moyennes."""
    if usage is not None and not isinstance(usage, dict) and hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if not isinstance(usage, dict):
        return None
    prompt, completion, total = (_first_int(usage, keys) for keys in (_PROMPT_KEYS, _COMPLETION_KEYS, _TOTAL_KEYS))
    if prompt is None and completion is None and total is None:
        return None
    prompt, completion = prompt or 0, completion or 0
    total = total if total is not None else prompt + completion
    # Un appel réel consomme toujours des tokens : tout à zéro (ex : Gemini sans usage_metadata renvoie
    # {"total_tokens": 0}) signifie « inconnu », pas « gratuit » — sinon les moyennes seraient tirées vers 0.
    if prompt == 0 and completion == 0 and total == 0:
        return None
    return prompt, completion, total

# --- Collecte -----------------------------------------------------------------------------------
@dataclass
class AgentStats:
    llm_calls: int = 0
    llm_errors: int = 0
    usage_calls: int = 0  # appels dont l'usage de tokens est connu
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    duration_seconds: Optional[float] = None

class ExecutionMetrics:
    """Coût et performance d'UNE exécution. `api_calls_count` = appels LLM RÉELS (événements CrewAI),
    `attempts` = tentatives de kickoff (retry sur quota). Thread-safe : les handlers d'événements
    s'exécutent dans un pool de threads."""

    def __init__(self):
        self.start_time = time.time()
        self.attempts = 0
        self.rate_limit_hits = 0
        self.total_wait_time = 0.0
        self.agents: dict[str, AgentStats] = {}
        self._lock = threading.Lock()

    @property
    def api_calls_count(self) -> int:
        with self._lock:
            return sum(stats.llm_calls for stats in self.agents.values())

    def _stats(self, role: Optional[str]) -> AgentStats:
        return self.agents.setdefault(metrics_roles.step_for_role(role), AgentStats())

    def record_attempt(self):
        with self._lock:
            self.attempts += 1

    def record_rate_limit(self, wait_seconds: float):
        with self._lock:
            self.rate_limit_hits += 1
            self.total_wait_time += wait_seconds

    def record_wait(self, wait_seconds: float):
        with self._lock:
            self.total_wait_time += wait_seconds

    def record_llm_call(self, role: Optional[str], usage: Any = None):
        parsed = parse_usage(usage)
        with self._lock:
            stats = self._stats(role)
            stats.llm_calls += 1
            if parsed is not None:
                stats.usage_calls += 1
                stats.prompt_tokens += parsed[0]
                stats.completion_tokens += parsed[1]
                stats.total_tokens += parsed[2]

    def record_llm_error(self, role: Optional[str]):
        with self._lock:
            self._stats(role).llm_errors += 1

    def record_tool(self, role: Optional[str], ok: bool = True):
        with self._lock:
            stats = self._stats(role)
            stats.tool_calls += 1
            if not ok:
                stats.tool_errors += 1

    def record_agent_done(self, role: Optional[str], duration_seconds: Optional[float]):
        with self._lock:
            self._stats(role).duration_seconds = duration_seconds

    def agent_snapshot(self) -> dict[str, AgentStats]:
        with self._lock:
            return {step: AgentStats(**vars(stats)) for step, stats in self.agents.items()}

# Métriques de l'exécution actuellement suivie (voir track_execution_metrics) : renvoyer à
# l'utilisateur le coût/la performance de SA requête plutôt qu'un compteur global partagé.
# contextvars (et non un simple global) : isolé entre requêtes concurrentes, propagé dans un thread
# lancé via asyncio.to_thread (kickoff_async) et copié par le bus d'événements CrewAI. Attention :
# loop.run_in_executor() nu ne copie PAS ce contexte (cf. _generate_summary, qui le fait explicitement).
current_metrics: ContextVar[Optional[ExecutionMetrics]] = ContextVar("current_metrics", default=None)

@contextmanager
def track_execution_metrics():
    """Active un ExecutionMetrics dédié le temps du bloc, à lire une fois celui-ci terminé."""
    metrics = ExecutionMetrics()
    token = current_metrics.set(metrics)
    try:
        yield metrics
    finally:
        current_metrics.reset(token)

# --- Événements CrewAI --------------------------------------------------------------------------
_listeners_registered = False

_listeners_lock = threading.Lock()

def register_event_listeners() -> bool:
    """Abonne (une seule fois) la collecte aux événements CrewAI. False si cette version de CrewAI
    n'expose pas ces événements : l'exécution continue alors sans détail par agent."""
    global _listeners_registered
    with _listeners_lock:
        if _listeners_registered:
            return True
        try:
            from crewai.events.event_bus import crewai_event_bus
            from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallFailedEvent
            from crewai.events.types.tool_usage_events import ToolUsageErrorEvent, ToolUsageFinishedEvent
        except Exception as e:  # pragma: no cover - dépend de la version de CrewAI
            log.warning(f"métriques par agent indisponibles ({type(e).__name__}: {e})")
            return False

        def safely(record):
            def handler(source, event):
                metrics = current_metrics.get()
                if metrics is None:
                    return
                try:
                    record(metrics, event)
                except Exception as err:  # une mesure ne doit JAMAIS faire échouer une exécution
                    log.warning(f"mesure ignorée ({type(err).__name__}: {err})")
            return handler

        crewai_event_bus.on(LLMCallCompletedEvent)(
            safely(lambda m, e: m.record_llm_call(getattr(e, "agent_role", None), getattr(e, "usage", None)))
        )
        crewai_event_bus.on(LLMCallFailedEvent)(
            safely(lambda m, e: m.record_llm_error(getattr(e, "agent_role", None)))
        )
        crewai_event_bus.on(ToolUsageFinishedEvent)(
            safely(lambda m, e: m.record_tool(getattr(e, "agent_role", None), getattr(e, "failure", None) is None))
        )
        crewai_event_bus.on(ToolUsageErrorEvent)(
            safely(lambda m, e: m.record_tool(getattr(e, "agent_role", None), False))
        )
        _listeners_registered = True
        return True

def flush_events(timeout: float = 5.0) -> None:
    """Attend les handlers encore en cours : sans cela, les derniers appels d'une exécution
    pourraient ne pas être comptés au moment de lire les métriques."""
    try:
        from crewai.events.event_bus import crewai_event_bus
        crewai_event_bus.flush(timeout=timeout)
    except Exception:
        pass

# --- Lignes persistées --------------------------------------------------------------------------
def build_agent_run_rows(
    metrics: ExecutionMetrics, *, execution_id: int, conversation_id: Optional[int],
    user_id: Optional[str], workflow: str,
) -> list[dict]:
    """Une ligne par agent ayant fait au moins un appel ou terminé sa tâche, dans l'ordre du
    pipeline. `incomplete` : appels faits mais tâche jamais terminée (échec en cours d'agent)."""
    snapshot = metrics.agent_snapshot()
    rows = []
    for step in metrics_roles.PIPELINE_ORDER:
        stats = snapshot.get(step)
        if stats is None or (stats.llm_calls == 0 and stats.tool_calls == 0 and stats.duration_seconds is None):
            continue
        if step in (metrics_roles.SYSTEM_BUCKET, metrics_roles.OTHER_BUCKET):
            status = "n/a"
        else:
            status = "completed" if stats.duration_seconds is not None else "incomplete"
        rows.append({
            "execution_id": execution_id, "conversation_id": conversation_id, "user_id": user_id,
            "workflow": workflow, "agent": step, "status": status, **vars(stats),
        })
    return rows
