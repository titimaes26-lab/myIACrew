"""Points d'accès du tableau de bord de performance : agrégats, tableau des exécutions, mesures d'une exécution."""
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case
from sqlmodel import Session, col, func, select

from metrics_report import agent_run_view, list_executions, split_by_period, sort_pipeline, summarize
from auth import get_current_user
from database import AgentRun, ExecutionHistory, get_session

router = APIRouter()

_IN_CLAUSE_CHUNK = 500

REQUEST_FETCH_CHARS = 200

# Garde-fou mémoire, pas une limite de produit : au-delà, le tableau de bord signale `truncated` et ne fait
# plus de comparaison. Assez haut pour que 365 jours d'un usage normal soient calculés EXACTEMENT.
_METRICS_EXECUTION_LIMIT = 20_000

def _token_prices() -> Optional[tuple[float, float]]:
    """Tarif (entrée, sortie) par million de tokens, lu à l'appel depuis TOKEN_PRICE_INPUT_PER_MILLION et
    TOKEN_PRICE_OUTPUT_PER_MILLION ; None (coût masqué) si absent, invalide ou nul. Un tarif unique : le modèle
    n'est pas stocké par mesure."""
    try:
        prices = (
            float(os.getenv("TOKEN_PRICE_INPUT_PER_MILLION", "0")),
            float(os.getenv("TOKEN_PRICE_OUTPUT_PER_MILLION", "0")),
        )
    except ValueError:
        return None
    return prices if prices[0] > 0 or prices[1] > 0 else None

@router.get("/api/metrics/summary")
def metrics_summary(
    days: int = 30,
    workflow: Optional[str] = None,
    tz_offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Performance par agent sur les `days` derniers jours (exécutions TERMINÉES de l'utilisateur),
    éventuellement restreinte à un workflow : durées p50/p95, appels LLM, tokens, outils, tendance.
    `tz_offset` : minutes à l'est d'UTC (celui du navigateur), pour regrouper par jour LOCAL."""
    days = max(1, min(days, 365))
    tz_offset = max(-840, min(tz_offset, 840))
    # Bornes AVEC fuseau : SQLModel refuse de lier un datetime naïf à ces colonnes. On lit les DEUX périodes
    # (courante et précédente, de même durée) en une seule requête pour la comparaison.
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    uid = user.get("id")
    statement = (
        select(  # type: ignore[misc]  # « too many unions » : select() à nombreuses colonnes, bruit SQLModel
            ExecutionHistory.id, ExecutionHistory.status, ExecutionHistory.workflow,
            ExecutionHistory.created_at, ExecutionHistory.updated_at,
            ExecutionHistory.rate_limit_hits, ExecutionHistory.total_wait_time_seconds,
            ExecutionHistory.error_code, ExecutionHistory.attempts, ExecutionHistory.reused_steps,
            ExecutionHistory.qa_verdict,
        )
        .where(ExecutionHistory.user_id == uid)
        .where(ExecutionHistory.created_at >= now - timedelta(days=2 * days))
        .where(col(ExecutionHistory.status).in_(("success", "failed")))
        .order_by(col(ExecutionHistory.created_at).desc())
        .limit(_METRICS_EXECUTION_LIMIT)
    )
    if workflow:
        statement = statement.where(ExecutionHistory.workflow == workflow)
    rows = [
        {
            "id": r[0], "status": r[1], "workflow": r[2], "created_at": r[3], "updated_at": r[4],
            "rate_limit_hits": r[5], "total_wait_time_seconds": r[6], "error_code": r[7],
            "attempts": r[8], "reused_steps": r[9], "qa_verdict": r[10],
        }
        for r in session.exec(statement).all()
    ]
    executions, previous_executions = split_by_period(rows, since)
    # Limite atteinte : les plus anciennes exécutions ont été coupées. La période courante n'est incomplète
    # que si la coupure l'atteint ; la période précédente, elle, l'est dès que la limite est atteinte.
    cut = len(rows) >= _METRICS_EXECUTION_LIMIT
    truncated = cut and not previous_executions
    # Comparaison abandonnée à cause de la limite alors que la période courante est complète : signalé à part.
    comparison_limited = cut and bool(previous_executions)
    # Par paquets : une liste IN de milliers d'identifiants dépasse la limite de paramètres des anciennes
    # versions de SQLite (999) ; sans effet notable sous Postgres.
    runs: list[dict[str, Any]] = []
    # Les mesures de la période précédente ne servent que si la comparaison est affichée : à la limite elle
    # est abandonnée, inutile alors de charger jusqu'à 20 000 exécutions de mesures pour les jeter.
    compare = bool(previous_executions) and not cut
    ids = [e["id"] for e in (rows if compare else executions)]
    for start in range(0, len(ids), _IN_CLAUSE_CHUNK):
        runs.extend(
            row.model_dump()
            for row in session.exec(
                select(AgentRun)
                .where(AgentRun.user_id == uid)
                .where(col(AgentRun.execution_id).in_(ids[start:start + _IN_CLAUSE_CHUNK]))
            ).all()
        )
    current_ids = {e["id"] for e in executions}
    prices = _token_prices()
    result = summarize(
        [r for r in runs if r["execution_id"] in current_ids], executions, days, workflow, tz_offset, prices,
    )
    result["currency"] = os.getenv("COST_CURRENCY", "$") if prices else None
    result["truncated"] = truncated
    result["comparison_limited"] = comparison_limited
    # Comparaison honnête seulement : sans exécution précédente, ou si la période précédente est incomplète
    # (limite atteinte), il n'y a rien à comparer — jamais un écart calculé sur un échantillon tronqué.
    if compare:
        previous_ids = {e["id"] for e in previous_executions}
        result["previous"] = summarize(
            [r for r in runs if r["execution_id"] in previous_ids], previous_executions, days, workflow, tz_offset,
            prices,
        )["executions"]
    else:
        result["previous"] = None
    return result

@router.get("/api/metrics/executions")
def metrics_executions(
    days: int = 30,
    workflow: Optional[str] = None,
    status: Optional[Literal["success", "failed"]] = None,
    sort: Literal["created_at", "duration", "llm_calls", "tokens"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    limit: int = 20,
    offset: int = 0,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Exécutions terminées de l'utilisateur sur la période, une page à la fois : filtre par workflow et par
    statut, tri par date, durée, appels LLM ou tokens (valeurs absentes toujours en dernier)."""
    days = max(1, min(days, 365))
    since = datetime.now(timezone.utc) - timedelta(days=days)
    uid = user.get("id")
    statement = (
        select(  # type: ignore[misc]  # « too many unions » : select() à nombreuses colonnes, bruit SQLModel
            ExecutionHistory.id, ExecutionHistory.conversation_id,
            # Début de la demande seulement : une demande peut faire des milliers de caractères.
            func.substr(ExecutionHistory.user_request, 1, REQUEST_FETCH_CHARS),
            ExecutionHistory.workflow, ExecutionHistory.status, ExecutionHistory.created_at,
            ExecutionHistory.updated_at, ExecutionHistory.error_code, ExecutionHistory.qa_verdict,
            ExecutionHistory.attempts, ExecutionHistory.reused_steps, ExecutionHistory.repo_owner,
            ExecutionHistory.repo_name,
        )
        .where(ExecutionHistory.user_id == uid)
        .where(ExecutionHistory.created_at >= since)
        .where(col(ExecutionHistory.status).in_(("success", "failed")))
        .order_by(col(ExecutionHistory.created_at).desc())
        .limit(_METRICS_EXECUTION_LIMIT)
    )
    if workflow:
        statement = statement.where(ExecutionHistory.workflow == workflow)
    if status:
        statement = statement.where(ExecutionHistory.status == status)
    rows = [
        {
            "id": r[0], "conversation_id": r[1], "user_request": r[2], "workflow": r[3], "status": r[4],
            "created_at": r[5], "updated_at": r[6], "error_code": r[7], "qa_verdict": r[8], "attempts": r[9],
            "reused_steps": r[10], "repo_owner": r[11], "repo_name": r[12],
        }
        for r in session.exec(statement).all()
    ]
    # Une ligne par exécution (GROUP BY) : appels LLM et tokens (usage connu seulement ; None sinon).
    sums: dict[int, dict[str, Any]] = {}
    ids = [row["id"] for row in rows]
    known_usage = case((col(AgentRun.usage_calls) > 0, 1), else_=0)
    for start in range(0, len(ids), _IN_CLAUSE_CHUNK):
        grouped = session.exec(
            select(
                AgentRun.execution_id, func.sum(AgentRun.llm_calls),
                func.sum(case((col(AgentRun.usage_calls) > 0, AgentRun.total_tokens), else_=0)),
                func.sum(known_usage),
            )
            .where(AgentRun.user_id == uid)
            .where(col(AgentRun.execution_id).in_(ids[start:start + _IN_CLAUSE_CHUNK]))
            .group_by(AgentRun.execution_id)
        ).all()
        for execution_id, calls, tokens, known in grouped:
            sums[execution_id] = {"llm_calls": int(calls or 0), "tokens": int(tokens or 0) if known else None}
    return list_executions(
        rows, sums, status=status, sort=sort, descending=order != "asc", limit=limit, offset=offset,
    )

@router.get("/api/executions/{execution_id}/agent-runs")
def execution_agent_runs(
    execution_id: int,
    session: Session = Depends(get_session),
    user: dict = Depends(get_current_user),
):
    """Détail par agent d'UNE exécution (durée, appels LLM, tokens, outils), dans l'ordre du pipeline."""
    entry = session.get(ExecutionHistory, execution_id)
    if not entry or entry.user_id != user.get("id"):
        raise HTTPException(status_code=404, detail="Exécution introuvable.")
    rows = [
        row.model_dump()
        for row in session.exec(select(AgentRun).where(AgentRun.execution_id == execution_id)).all()
    ]
    return [agent_run_view(row) for row in sort_pipeline(rows)]
