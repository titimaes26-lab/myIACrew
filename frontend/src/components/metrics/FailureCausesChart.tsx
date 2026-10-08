import type { FailureCauseRow } from '../../types';
import { formatInteger, formatPercent, niceTicks, percentOf } from '../../utils/metricsFormat';
import { TipRow } from './ChartTooltip';
import ChartCard from './ChartCard';
import HorizontalChart, { type HorizontalRow } from './HorizontalChart';

// Échecs par cause : une seule série (nombre d'exécutions), un seul axe, du plus fréquent au moins
// fréquent. Sans échec sur la période, la carte l'annonce au lieu de disparaître.
export default function FailureCausesChart({ failures, total }: { failures: FailureCauseRow[]; total: number }) {
  const hasUnclassified = failures.some((f) => f.code === 'UNCLASSIFIED');
  const failed = failures.reduce((sum, f) => sum + f.count, 0);
  const ticks = niceTicks(Math.max(0, ...failures.map((f) => f.count)), 4, true);
  const max = ticks[ticks.length - 1] || 1;

  const rows: HorizontalRow[] = failures.map((f) => {
    const share = formatPercent(f.count, failed);
    const plural = f.count > 1 ? 's' : '';
    return {
      key: f.code,
      label: f.label,
      valueText: `${f.count} · ${share}`,
      ariaLabel: `${f.label} : ${f.count} échec${plural}, ${share} des échecs`,
      tooltip: (
        <>
          <div className="viz-tip-title">{f.label}</div>
          <TipRow label="Échecs" value={formatInteger(f.count)} keyColor="s1" />
          <TipRow label="Part des échecs" value={share} />
          <TipRow label="Part des exécutions" value={formatPercent(f.count, total)} />
        </>
      ),
      marks: <div className="viz-hbar" style={{ width: percentOf(f.count, max) }} />,
    };
  });

  return (
    <ChartCard
      title="Échecs par cause"
      subtitle={failed > 0
        ? `${failed} exécution${failed > 1 ? 's' : ''} en échec sur ${total}.${hasUnclassified ? " Les échecs d'avant le suivi des causes sont regroupés à part." : ''}`
        : 'Cause de chaque exécution en échec.'}
      table={{
        columns: ['Cause', 'Échecs', 'Part des échecs'],
        rows: failures.map((f) => [f.label, String(f.count), formatPercent(f.count, failed)]),
      }}
      empty={failures.length === 0 ? 'Aucun échec sur cette période : toutes les exécutions terminées ont réussi.' : null}
    >
      <HorizontalChart rows={rows} ticks={ticks} formatTick={(tick) => formatInteger(tick)} />
    </ChartCard>
  );
}
