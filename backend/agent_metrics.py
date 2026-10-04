"""Mesure de la performance PAR AGENT : appels LLM réels, tokens, appels d'outils, durée.

Les compteurs viennent des événements CrewAI (un événement par appel LLM terminé, par appel d'outil),
pas d'un décompte maison : l'ancien « api_calls_count » ne comptait que les tentatives de kickoff
et sous-estimait fortement la consommation du quota Gemini. Le bus d'événements copie le contexte
(contextvars) pour chaque handler : l'exécution suivie par track_execution_metrics reste donc isolée
des exécutions concurrentes. Les agrégats (percentiles, moyennes, séries par jour) sont des fonctions
pures, testables sans base ni réseau."""
import math
import re
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

from errors import ErrorCode

import yaml

SYSTEM_BUCKET = "system"  # appels LLM hors agent (ex : synthèse finale du résultat)
OTHER_BUCKET = "other"

# Ordre du pipeline, clés alignées sur WORKFLOW_STEPS côté frontend.
PIPELINE_ORDER = ["design", "architecture", "diagnostic", "development", "qa", SYSTEM_BUCKET, OTHER_BUCKET]
STEP_BY_AGENT_CONFIG = {
    "product_designer_agent": "design",
    "architect_agent": "architecture",
    "diagnostic_agent": "diagnostic",
    "developer_agent": "development",
    "qa_agent": "qa",
}
AGENT_LABELS = {
    "design": "Conception",
    "architecture": "Architecture",
    "diagnostic": "Diagnostic",
    "development": "Développement",
    "qa": "QA",
    SYSTEM_BUCKET: "Synthèse (hors agent)",
    OTHER_BUCKET: "Autre",
}


def _normalize_role(role: Optional[str]) -> str:
    return re.sub(r"\s+", " ", (role or "").strip()).lower()


def _load_role_map() -> dict[str, str]:
    """{rôle normalisé -> clé d'étape} lu dans agentsquestion.yaml : le rôle d'un événement CrewAI
    est le texte de `role:`, la seule source de vérité pour relier un appel à son agent."""
    try:
        config = yaml.safe_load((Path(__file__).resolve().parent / "agentsquestion.yaml").read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {
        _normalize_role(config[name]["role"]): step
        for name, step in STEP_BY_AGENT_CONFIG.items()
        if isinstance(config.get(name), dict) and config[name].get("role")
    }


ROLE_TO_STEP = _load_role_map()


def step_for_role(role: Optional[str]) -> str:
    if not role or not role.strip():
        return SYSTEM_BUCKET
    return ROLE_TO_STEP.get(_normalize_role(role), OTHER_BUCKET)


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
        return self.agents.setdefault(step_for_role(role), AgentStats())

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
_current_metrics: ContextVar[Optional[ExecutionMetrics]] = ContextVar("current_metrics", default=None)


@contextmanager
def track_execution_metrics():
    """Active un ExecutionMetrics dédié le temps du bloc, à lire une fois celui-ci terminé."""
    metrics = ExecutionMetrics()
    token = _current_metrics.set(metrics)
    try:
        yield metrics
    finally:
        _current_metrics.reset(token)


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
            print(f"AVERTISSEMENT : métriques par agent indisponibles ({type(e).__name__}: {e})", flush=True)
            return False

        def safely(record):
            def handler(source, event):
                metrics = _current_metrics.get()
                if metrics is None:
                    return
                try:
                    record(metrics, event)
                except Exception as err:  # une mesure ne doit JAMAIS faire échouer une exécution
                    print(f"AVERTISSEMENT : mesure ignorée ({type(err).__name__}: {err})", flush=True)
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
    for step in PIPELINE_ORDER:
        stats = snapshot.get(step)
        if stats is None or (stats.llm_calls == 0 and stats.tool_calls == 0 and stats.duration_seconds is None):
            continue
        if step in (SYSTEM_BUCKET, OTHER_BUCKET):
            status = "n/a"
        else:
            status = "completed" if stats.duration_seconds is not None else "incomplete"
        rows.append({
            "execution_id": execution_id, "conversation_id": conversation_id, "user_id": user_id,
            "workflow": workflow, "agent": step, "status": status, **vars(stats),
        })
    return rows


# --- Agrégats purs ------------------------------------------------------------------------------
def percentile(values: list[float], fraction: float) -> Optional[float]:
    """Percentile par interpolation linéaire ; None si aucune valeur."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _mean(values: list[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _round(value: Optional[float], digits: int = 1) -> Optional[float]:
    return None if value is None else round(value, digits)


# Libellés des causes d'échec (codes de errors.ErrorCode) ; UNCLASSIFIED = lignes d'avant le suivi des
# causes ou échec sans code. Un code inconnu (futur) s'affiche tel quel plutôt que d'être masqué.
UNCLASSIFIED_FAILURE = "UNCLASSIFIED"
FAILURE_LABELS = {
    ErrorCode.QUOTA_EXHAUSTED: "Quota du modèle épuisé",
    ErrorCode.LLM_UNAVAILABLE: "Modèle indisponible ou surchargé",
    ErrorCode.LLM_TIMEOUT: "Délai du modèle dépassé",
    ErrorCode.GITHUB_UNAVAILABLE: "GitHub injoignable",
    ErrorCode.GUARDRAIL_FAILED: "Contrôle de qualité non respecté",
    ErrorCode.INTERRUPTED: "Interrompue (redémarrage du serveur)",
    ErrorCode.INTERNAL_ERROR: "Erreur interne",
    UNCLASSIFIED_FAILURE: "Cause non enregistrée",
}


def failure_causes(executions: list[dict]) -> list[dict]:
    """Échecs de la période regroupés par cause, du plus fréquent au moins fréquent."""
    counts: dict[str, int] = {}
    for e in executions:
        if e["status"] == "failed":
            code = e.get("error_code") or UNCLASSIFIED_FAILURE
            counts[code] = counts.get(code, 0) + 1
    return [
        {"code": code, "label": FAILURE_LABELS.get(code, code), "count": count}
        for code, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def summarize(
    runs: list[dict], executions: list[dict], days: int, workflow: Optional[str] = None, tz_offset_minutes: int = 0,
) -> dict:
    """Vue d'ensemble pour le tableau de bord. `runs` : lignes AgentRun ; `executions` : exécutions
    TERMINÉES (status success|failed) de la période, avec created_at / updated_at (datetime).
    tz_offset_minutes : décalage de l'utilisateur à l'est d'UTC, appliqué UNIQUEMENT au regroupement par
    jour (une exécution à 23 h 30 UTC appartient au lendemain pour quelqu'un à UTC+2)."""
    # Une seule définition de « durée d'une exécution » : sert à la médiane globale ET à celle de chaque jour.
    duration_by_execution = {
        e["id"]: (e["updated_at"] - e["created_at"]).total_seconds()
        for e in executions if e.get("updated_at") and e.get("created_at")
    }
    durations = list(duration_by_execution.values())
    by_execution_tokens: dict[int, int] = {}
    by_execution_calls: dict[int, int] = {}
    for run in runs:
        if run["usage_calls"] > 0:
            by_execution_tokens[run["execution_id"]] = by_execution_tokens.get(run["execution_id"], 0) + run["total_tokens"]
        by_execution_calls[run["execution_id"]] = by_execution_calls.get(run["execution_id"], 0) + run["llm_calls"]
    succeeded = sum(1 for e in executions if e["status"] == "success")

    agents = []
    for step in PIPELINE_ORDER:
        step_runs = [r for r in runs if r["agent"] == step]
        if not step_runs:
            continue
        measured = [r["duration_seconds"] for r in step_runs if r["duration_seconds"] is not None]
        with_tokens = [r for r in step_runs if r["usage_calls"] > 0]
        agents.append({
            "agent": step, "label": AGENT_LABELS[step],
            "runs": len(step_runs),
            "incomplete": sum(1 for r in step_runs if r["status"] == "incomplete"),
            "duration_p50": _round(percentile(measured, 0.5)),
            "duration_p95": _round(percentile(measured, 0.95)),
            "avg_llm_calls": _round(_mean([r["llm_calls"] for r in step_runs])),
            "llm_errors": sum(r["llm_errors"] for r in step_runs),
            "token_runs": len(with_tokens),
            "avg_prompt_tokens": _round(_mean([r["prompt_tokens"] for r in with_tokens]), 0),
            "avg_completion_tokens": _round(_mean([r["completion_tokens"] for r in with_tokens]), 0),
            "avg_tool_calls": _round(_mean([r["tool_calls"] for r in step_runs])),
            "tool_errors": sum(r["tool_errors"] for r in step_runs),
        })

    daily: dict[str, dict] = {}
    daily_durations: dict[str, list[float]] = {}
    shift = timedelta(minutes=tz_offset_minutes)
    execution_day = {e["id"]: (e["created_at"] + shift).date().isoformat() for e in executions if e.get("created_at")}
    for e in executions:
        day = daily.setdefault(execution_day[e["id"]], {"executions": 0, "failed": 0, "llm_calls": 0, "tokens": 0})
        day["executions"] += 1
        day["failed"] += 1 if e["status"] == "failed" else 0
        day["llm_calls"] += by_execution_calls.get(e["id"], 0)
        day["tokens"] += by_execution_tokens.get(e["id"], 0)
        if e["id"] in duration_by_execution:
            daily_durations.setdefault(execution_day[e["id"]], []).append(duration_by_execution[e["id"]])
    for date, values in daily.items():
        # None (pas 0) un jour sans durée mesurable : le graphique n'y trace aucune barre.
        values["median_duration_seconds"] = _round(percentile(daily_durations.get(date, []), 0.5))

    return {
        "period_days": days,
        "workflow": workflow,
        "executions": {
            "total": len(executions),
            "success": succeeded,
            "failed": len(executions) - succeeded,
            "median_duration_seconds": _round(percentile(durations, 0.5)),
            "avg_llm_calls": _round(_mean(list(by_execution_calls.values()))),
            "avg_tokens": _round(_mean(list(by_execution_tokens.values())), 0),
            "token_executions": len(by_execution_tokens),
            "rate_limit_hits": sum(e.get("rate_limit_hits") or 0 for e in executions),
            "wait_seconds": _round(sum(e.get("total_wait_time_seconds") or 0 for e in executions)),
        },
        "agents": agents,
        "failures": failure_causes(executions),
        "daily": [{"date": day, **values} for day, values in sorted(daily.items())],
    }


def agent_run_view(row: dict) -> dict:
    """Une ligne AgentRun prête pour l'API/l'interface (libellé ajouté, durées arrondies)."""
    return {
        "agent": row["agent"], "label": AGENT_LABELS.get(row["agent"], row["agent"]),
        "status": row["status"], "duration_seconds": _round(row["duration_seconds"], 2),
        "llm_calls": row["llm_calls"], "llm_errors": row["llm_errors"],
        "tokens_known": row["usage_calls"] > 0,
        "prompt_tokens": row["prompt_tokens"], "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "tool_calls": row["tool_calls"], "tool_errors": row["tool_errors"],
    }


def sort_pipeline(rows: list[dict]) -> list[dict]:
    order = {step: index for index, step in enumerate(PIPELINE_ORDER)}
    return sorted(rows, key=lambda row: order.get(row["agent"], len(order)))


def split_by_period(executions: list[dict], since: Any) -> tuple[list[dict], list[dict]]:
    """(période courante, période précédente) d'une liste d'exécutions couvrant les DEUX périodes :
    created_at >= since / created_at < since. SQLite renvoie des dates naïves (UTC), Postgres des dates
    avec fuseau : les deux sont comparées en UTC."""
    def as_utc(value: Any) -> Any:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    current = [e for e in executions if as_utc(e["created_at"]) >= since]
    previous = [e for e in executions if as_utc(e["created_at"]) < since]
    return current, previous


__all__ = [
    "AgentStats", "ExecutionMetrics", "track_execution_metrics", "register_event_listeners", "flush_events",
    "build_agent_run_rows", "summarize", "percentile", "parse_usage", "step_for_role", "agent_run_view",
    "sort_pipeline", "split_by_period", "PIPELINE_ORDER", "AGENT_LABELS", "SYSTEM_BUCKET", "OTHER_BUCKET",
]
