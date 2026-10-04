import { METRIC_HINTS } from '../../utils/metricHints';
import type { AgentMetricsRow } from '../../types';
import { formatSeconds } from '../../utils/formatDuration';
import { niceDurationTicks, percentOf } from '../../utils/metricsFormat';
import { TipRow } from './ChartTooltip';
import ChartCard from './ChartCard';
import InfoTip from './InfoTip';
import HorizontalChart, { type HorizontalRow } from './HorizontalChart';

const SYSTEM_AGENTS = new Set(['system', 'other']);

// Durée par agent : barre = médiane (p50), point = p95 (le pire cas courant) sur le MÊME axe en secondes.
export default function AgentDurationChart({ agents }: { agents: AgentMetricsRow[] }) {
  const measured = agents.filter((a) => a.duration_p50 != null && !SYSTEM_AGENTS.has(a.agent));
  const slowest = Math.max(0, ...measured.map((a) => a.duration_p95 ?? a.duration_p50 ?? 0));
  const ticks = niceDurationTicks(slowest, 4);
  const max = ticks[ticks.length - 1] || 1;

  const rows: HorizontalRow[] = measured.map((a) => {
    const p50 = a.duration_p50 ?? 0;
    const p95 = a.duration_p95;
    const runs = `${a.runs} exécution${a.runs > 1 ? 's' : ''}`;
    return {
      key: a.agent,
      label: a.label,
      valueText: p95 == null ? formatSeconds(p50) : `${formatSeconds(p50)} · p95 ${formatSeconds(p95)}`,
      ariaLabel: `${a.label} : médiane ${formatSeconds(p50)}${p95 == null ? '' : `, p95 ${formatSeconds(p95)}`}, ${runs}`,
      tooltip: (
        <>
          <div className="viz-tip-title">{a.label}</div>
          <TipRow label="Médiane (p50)" value={formatSeconds(p50)} keyColor="s1" />
          {p95 != null && <TipRow label="p95" value={formatSeconds(p95)} keyColor="s2" />}
          <TipRow label="Exécutions" value={`${a.runs}${a.incomplete ? ` (dont ${a.incomplete} incomplète${a.incomplete > 1 ? 's' : ''})` : ''}`} />
        </>
      ),
      marks: (
        <>
          <div className="viz-hbar" style={{ width: percentOf(p50, max) }} />
          {p95 != null && <div className="viz-hdot" style={{ left: percentOf(p95, max) }} />}
        </>
      ),
    };
  });

  return (
    <ChartCard
      title="Durée par agent"
      subtitle="Temps d'exécution de la tâche de chaque agent."
      legend={(
        <>
          <span><i className="viz-key-bar" style={{ background: 'var(--viz-s1)' }} />Médiane (p50)<InfoTip term="p50" text={METRIC_HINTS.p50} /></span>
          <span><i className="viz-key-dot" style={{ background: 'var(--viz-s2)' }} />p95<InfoTip term="p95" text={METRIC_HINTS.p95} /></span>
        </>
      )}
      table={{
        columns: ['Agent', 'Exécutions', 'Médiane (p50)', 'p95'],
        hints: { 'Médiane (p50)': METRIC_HINTS.p50, p95: METRIC_HINTS.p95 },
        rows: measured.map((a) => [a.label, String(a.runs), formatSeconds(a.duration_p50 ?? 0), a.duration_p95 == null ? '—' : formatSeconds(a.duration_p95)]),
      }}
      empty={rows.length === 0 ? 'Aucune durée mesurée sur cette période.' : null}
    >
      <HorizontalChart rows={rows} ticks={ticks} formatTick={formatSeconds} />
    </ChartCard>
  );
}
