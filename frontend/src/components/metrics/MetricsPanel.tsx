import { useState } from 'react';
import { useMetricsSummary } from '../../hooks/useMetricsSummary';
import type { MetricsSummary, MetricsWorkflowFilter } from '../../types';
import {
  formatCompact, formatCost, formatInteger, formatNumber, formatPercent, formatSecondsOrDash, successPercent,
} from '../../utils/metricsFormat';
import AgentDurationChart from './AgentDurationChart';
import AgentTokensChart from './AgentTokensChart';
import ChartCard from './ChartCard';
import DailyTrendsChart from './DailyTrendsChart';
import ExecutionsTable from './ExecutionsTable';
import FailureCausesChart from './FailureCausesChart';
import QaVerdictsChart from './QaVerdictsChart';
import MetricsFilters from './MetricsFilters';
import StatTile from './StatTile';
import { computeDelta } from '../../utils/metricsDelta';
import './metrics.css';

function Tiles({ summary }: { summary: MetricsSummary }) {
  const e = summary.executions;
  const p = summary.previous; // null : pas de période précédente comparable (aucune donnée, ou limite atteinte)
  const delta = {
    total: p ? computeDelta(e.total, p.total, 'none') : null,
    success: p ? computeDelta(successPercent(e.success, e.total), successPercent(p.success, p.total), 'up', 'points') : null,
    duration: p ? computeDelta(e.median_duration_seconds, p.median_duration_seconds, 'down') : null,
    calls: p ? computeDelta(e.avg_llm_calls, p.avg_llm_calls, 'down') : null,
    tokens: p ? computeDelta(e.avg_tokens, p.avg_tokens, 'down') : null,
    pauses: p ? computeDelta(e.rate_limit_hits, p.rate_limit_hits, 'down') : null,
    cost: p ? computeDelta(e.avg_cost, p.avg_cost, 'down') : null,
    // Relances et reprises comparées en TAUX (points) : leur nombre dépend du volume d'exécutions.
    retried: p ? computeDelta(successPercent(e.auto_retried, e.total), successPercent(p.auto_retried, p.total), 'down', 'points') : null,
  };
  const share = (count: number) => (e.total > 0 ? `${formatPercent(count, e.total)} des exécutions` : '');
  return (
    <div className="viz-tiles">
      <StatTile label="Exécutions terminées" value={formatInteger(e.total)} delta={delta.total} sub={`${e.success} réussie${e.success > 1 ? 's' : ''} · ${e.failed} en échec`} />
      <StatTile label="Taux de succès" value={formatPercent(e.success, e.total)} delta={delta.success} sub="exécutions terminées" />
      <StatTile label="Durée médiane" value={formatSecondsOrDash(e.median_duration_seconds)} delta={delta.duration} sub="par exécution" />
      <StatTile label="Appels LLM" value={formatNumber(e.avg_llm_calls)} delta={delta.calls} sub="par exécution (appels réels)" />
      <StatTile label="Tokens" value={formatCompact(e.avg_tokens)} delta={delta.tokens} sub={e.token_executions > 0 ? `par exécution · ${e.token_executions}/${e.total} mesurées` : 'usage non fourni par le modèle'} />
      <StatTile label="Pauses quota" value={formatInteger(e.rate_limit_hits)} delta={delta.pauses} sub={`${formatSecondsOrDash(e.wait_seconds)} d'attente au total`} />
      {e.avg_cost != null && (
        <StatTile label="Coût estimé" value={formatCost(e.avg_cost, summary.currency)} delta={delta.cost} sub={`par exécution · ${formatCost(e.total_cost, summary.currency)} au total`} />
      )}
      <StatTile label="Relances automatiques" value={formatInteger(e.auto_retried)} delta={delta.retried} sub={share(e.auto_retried) || 'après une panne temporaire'} />
      <StatTile label="Reprises à l'étape" value={formatInteger(e.resumed)} sub={share(e.resumed) || 'étapes déjà réussies réutilisées'} />
    </div>
  );
}

function AgentTable({ summary }: { summary: MetricsSummary }) {
  return (
    <ChartCard
      title="Détail par agent"
      subtitle="Moyennes par exécution ; les durées sont des médianes."
      table={{
        columns: ['Agent', 'Exécutions', 'Durée p50', 'Durée p95', 'Appels LLM', 'Tokens', 'Outils', 'Incomplètes'],
        rows: summary.agents.map((a) => [
          a.label,
          String(a.runs),
          formatSecondsOrDash(a.duration_p50),
          formatSecondsOrDash(a.duration_p95),
          formatNumber(a.avg_llm_calls),
          a.token_runs > 0 ? formatCompact((a.avg_prompt_tokens ?? 0) + (a.avg_completion_tokens ?? 0)) : '—',
          `${formatNumber(a.avg_tool_calls)}${a.tool_errors ? ` (${a.tool_errors} err.)` : ''}`,
          a.incomplete > 0 ? `⚠️ ${a.incomplete}` : '0',
        ]),
      }}
      empty={summary.agents.length === 0 ? 'Aucune mesure par agent pour cette période.' : null}
    />
  );
}

export default function MetricsPanel({ apiUrl, accessToken }: { apiUrl: string; accessToken: string }) {
  const [days, setDays] = useState(30);
  const [workflow, setWorkflow] = useState<MetricsWorkflowFilter>('ALL');
  const { data, error, loading } = useMetricsSummary(apiUrl, accessToken, days, workflow);

  return (
    <section className="viz-root" aria-label="Performance des agents" aria-busy={loading}>
      <div className="viz-head">
        <h3 className="viz-title">📊 Performance des agents</h3>
        <span className="viz-sub">Vos exécutions terminées sur la période</span>
      </div>
      <MetricsFilters days={days} onDaysChange={setDays} workflow={workflow} onWorkflowChange={setWorkflow} />
      {error && <div className="viz-error" role="alert">❌ {error}</div>}
      {!data && !error && <p className="viz-empty">Chargement…</p>}
      {data && data.executions.total === 0 && (
        <p className="viz-empty">Aucune exécution terminée sur cette période. Lancez une demande, puis revenez ici.</p>
      )}
      {data?.comparison_limited && !data.truncated && (
        <p className="viz-sub">Comparaison indisponible : trop d'exécutions sur la période précédente.</p>
      )}
      {data?.truncated && (
        <p className="viz-sub">Trop d'exécutions sur cette période : les plus anciennes sont ignorées et aucune comparaison n'est affichée.</p>
      )}
      {data && data.executions.total > 0 && data.previous && (
        <p className="viz-sub">Écarts calculés par rapport aux {data.period_days} jours précédents.</p>
      )}
      {data && data.executions.total > 0 && (
        <div className={loading ? 'viz-loading' : undefined}>
          <Tiles summary={data} />
          <AgentDurationChart agents={data.agents} />
          <AgentTokensChart agents={data.agents} />
          <FailureCausesChart failures={data.failures ?? []} total={data.executions.total} />
          <QaVerdictsChart verdicts={data.executions.qa_verdicts ?? { GO: 0, GO_AVEC_RESERVES: 0, NO_GO: 0 }} total={data.executions.total} />
          <DailyTrendsChart daily={data.daily} />
          <ExecutionsTable apiUrl={apiUrl} accessToken={accessToken} days={days} workflow={workflow} />
          <AgentTable summary={data} />
        </div>
      )}
    </section>
  );
}
