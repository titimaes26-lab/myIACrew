"""Agrégats du tableau de bord : percentiles, causes d'échec, coût, séries par période, tableau des exécutions (fonctions pures)."""
import math
from datetime import timedelta, timezone
from typing import Any, Optional

import metrics_roles
from errors import ErrorCode
from logs import get_logger

log = get_logger("metrics")

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
    ErrorCode.DELIVERY_FAILED: "Livraison GitHub non confirmée",
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

QA_VERDICTS = ("GO", "GO_AVEC_RESERVES", "NO_GO")

def execution_cost(prompt_tokens: float, completion_tokens: float, price_per_million: Optional[tuple[float, float]]) -> Optional[float]:
    """Coût estimé (dans la devise des tarifs) ; None sans tarif configuré. Un seul tarif global (entrée, sortie)
    par million de tokens : le modèle utilisé n'est pas stocké par mesure."""
    if price_per_million is None or (price_per_million[0] <= 0 and price_per_million[1] <= 0):
        return None
    return prompt_tokens * price_per_million[0] / 1_000_000 + completion_tokens * price_per_million[1] / 1_000_000

def summarize(
    runs: list[dict], executions: list[dict], days: int, workflow: Optional[str] = None, tz_offset_minutes: int = 0,
    price_per_million: Optional[tuple[float, float]] = None,
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
    # Coût par exécution, sur les seules mesures dont l'usage de tokens est connu (jamais « 0 » inventé).
    token_split: dict[int, list[float]] = {}
    for run in runs:
        if run["usage_calls"] > 0:
            split = token_split.setdefault(run["execution_id"], [0.0, 0.0])
            split[0] += run["prompt_tokens"]
            split[1] += run["completion_tokens"]
    costs = [
        cost for cost in (execution_cost(p, c, price_per_million) for p, c in token_split.values()) if cost is not None
    ]
    verdicts = {name: sum(1 for e in executions if e.get("qa_verdict") == name) for name in QA_VERDICTS}

    agents = []
    for step in metrics_roles.PIPELINE_ORDER:
        step_runs = [r for r in runs if r["agent"] == step]
        if not step_runs:
            continue
        measured = [r["duration_seconds"] for r in step_runs if r["duration_seconds"] is not None]
        with_tokens = [r for r in step_runs if r["usage_calls"] > 0]
        agents.append({
            "agent": step, "label": metrics_roles.AGENT_LABELS[step],
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
        day = daily.setdefault(execution_day[e["id"]], {"executions": 0, "failed": 0, "llm_calls": 0, "tokens": 0, "qa_total": 0, "qa_go": 0})
        day["executions"] += 1
        day["failed"] += 1 if e["status"] == "failed" else 0
        day["llm_calls"] += by_execution_calls.get(e["id"], 0)
        day["tokens"] += by_execution_tokens.get(e["id"], 0)
        if e.get("qa_verdict") in QA_VERDICTS:
            day["qa_total"] += 1
            day["qa_go"] += 1 if e["qa_verdict"] == "GO" else 0
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
            # Raisonnement : exécutions relancées automatiquement (2 tentatives) / reprises d'étapes déjà réussies.
            "auto_retried": sum(1 for e in executions if (e.get("attempts") or 1) > 1),
            "resumed": sum(1 for e in executions if (e.get("reused_steps") or 0) > 0),
            # Qualité : verdict QA du résultat (exécutions réussies qui en portent un).
            "qa_verdicts": verdicts,
            # Coût estimé (None sans tarif configuré ou sans tokens connus).
            "total_cost": _round(sum(costs), 6) if costs else None,
            "avg_cost": _round(_mean(costs), 6) if costs else None,
        },
        "agents": agents,
        "failures": failure_causes(executions),
        "daily": [{"date": day, **values} for day, values in sorted(daily.items())],
    }

def agent_run_view(row: dict) -> dict:
    """Une ligne AgentRun prête pour l'API/l'interface (libellé ajouté, durées arrondies)."""
    return {
        "agent": row["agent"], "label": metrics_roles.AGENT_LABELS.get(row["agent"], row["agent"]),
        "status": row["status"], "duration_seconds": _round(row["duration_seconds"], 2),
        "llm_calls": row["llm_calls"], "llm_errors": row["llm_errors"],
        "tokens_known": row["usage_calls"] > 0,
        "prompt_tokens": row["prompt_tokens"], "completion_tokens": row["completion_tokens"],
        "total_tokens": row["total_tokens"],
        "tool_calls": row["tool_calls"], "tool_errors": row["tool_errors"],
    }

def sort_pipeline(rows: list[dict]) -> list[dict]:
    order = {step: index for index, step in enumerate(metrics_roles.PIPELINE_ORDER)}
    return sorted(rows, key=lambda row: order.get(row["agent"], len(order)))

def _as_utc(value: Any) -> Any:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

def split_by_period(executions: list[dict], since: Any) -> tuple[list[dict], list[dict]]:
    """(période courante, période précédente) d'une liste d'exécutions couvrant les DEUX périodes :
    created_at >= since / created_at < since. SQLite renvoie des dates naïves (UTC), Postgres des dates
    avec fuseau : les deux sont comparées en UTC."""
    current = [e for e in executions if _as_utc(e["created_at"]) >= since]
    previous = [e for e in executions if _as_utc(e["created_at"]) < since]
    return current, previous

EXECUTION_SORT_KEYS = ("created_at", "duration", "llm_calls", "tokens")

EXECUTION_STATUS_FILTERS = ("success", "failed")

REQUEST_PREVIEW_CHARS = 140

def list_executions(
    executions: list[dict], sums: dict[int, dict], *, status: Optional[str] = None, sort: str = "created_at",
    descending: bool = True, limit: int = 20, offset: int = 0,
) -> dict:
    """Page d'exécutions terminées pour le tableau de bord : filtre par statut, tri (date, durée, appels LLM,
    tokens) et pagination. `sums` : {id: {"llm_calls": int, "tokens": int | None}} (tokens None si l'usage est
    inconnu). Une valeur absente est TOUJOURS classée en dernier, quel que soit le sens du tri."""
    if sort not in EXECUTION_SORT_KEYS:
        sort = "created_at"
    rows = []
    for e in executions:
        if status in EXECUTION_STATUS_FILTERS and e["status"] != status:
            continue
        measured = sums.get(e["id"], {})
        duration = (
            (e["updated_at"] - e["created_at"]).total_seconds()
            if e.get("updated_at") and e.get("created_at") else None
        )
        request = (e.get("user_request") or "").strip().replace("\n", " ")
        rows.append({
            "id": e["id"], "conversation_id": e.get("conversation_id"),
            "user_request": request[:REQUEST_PREVIEW_CHARS] + ("…" if len(request) > REQUEST_PREVIEW_CHARS else ""),
            "workflow": e["workflow"], "status": e["status"], "created_at": e["created_at"],
            "duration_seconds": _round(duration), "llm_calls": measured.get("llm_calls"),
            "tokens": measured.get("tokens"), "error_code": e.get("error_code"), "qa_verdict": e.get("qa_verdict"),
            "attempts": e.get("attempts") or 1, "reused_steps": e.get("reused_steps") or 0,
            "repo": f"{e['repo_owner']}/{e['repo_name']}" if e.get("repo_owner") and e.get("repo_name") else None,
        })

    def key(row: dict) -> Any:
        return _as_utc(row["created_at"]).timestamp() if sort == "created_at" else row[{
            "duration": "duration_seconds", "llm_calls": "llm_calls", "tokens": "tokens"}[sort]]

    present = [row for row in rows if key(row) is not None]
    missing = [row for row in rows if key(row) is None]
    present.sort(key=key, reverse=descending)
    ordered = present + missing
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    return {"total": len(ordered), "items": ordered[offset:offset + limit]}
