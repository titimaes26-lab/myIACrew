import type { QaVerdict } from '../../types';
import { formatInteger, formatPercent, niceTicks, percentOf } from '../../utils/metricsFormat';
import { TipRow } from './ChartTooltip';
import ChartCard from './ChartCard';
import HorizontalChart, { type HorizontalRow } from './HorizontalChart';

const VERDICTS: { key: QaVerdict; label: string }[] = [
  { key: 'GO', label: 'GO' },
  { key: 'GO_AVEC_RESERVES', label: 'GO avec réserves' },
  { key: 'NO_GO', label: 'NO GO' },
];

// Qualité : verdict QA des résultats (la mesure la plus proche de « la réponse était-elle bonne ? »). Une seule
// série (nombre d'exécutions), un seul axe. Seules les exécutions qui portent un verdict sont comptées.
export default function QaVerdictsChart({ verdicts, total }: { verdicts: Record<QaVerdict, number>; total: number }) {
  const withVerdict = VERDICTS.reduce((sum, v) => sum + (verdicts[v.key] ?? 0), 0);
  const ticks = niceTicks(Math.max(0, ...VERDICTS.map((v) => verdicts[v.key] ?? 0)), 4, true);
  const max = ticks[ticks.length - 1] || 1;

  const rows: HorizontalRow[] = VERDICTS.map((v) => {
    const count = verdicts[v.key] ?? 0;
    const share = formatPercent(count, withVerdict);
    return {
      key: v.key,
      label: v.label,
      valueText: `${count} · ${share}`,
      ariaLabel: `Verdict ${v.label} : ${count} exécution${count > 1 ? 's' : ''}, ${share} des verdicts`,
      tooltip: (
        <>
          <div className="viz-tip-title">Verdict {v.label}</div>
          <TipRow label="Exécutions" value={formatInteger(count)} keyColor="s1" />
          <TipRow label="Part des verdicts" value={share} />
          <TipRow label="Part des exécutions" value={formatPercent(count, total)} />
        </>
      ),
      marks: <div className="viz-hbar" style={{ width: percentOf(count, max) }} />,
    };
  });

  return (
    <ChartCard
      title="Verdict QA"
      subtitle={withVerdict > 0
        ? `${withVerdict} exécution${withVerdict > 1 ? 's' : ''} avec un verdict QA sur ${total}.`
        : 'Verdict de la revue QA sur les résultats.'}
      table={{
        columns: ['Verdict', 'Exécutions', 'Part des verdicts'],
        rows: VERDICTS.map((v) => [v.label, String(verdicts[v.key] ?? 0), formatPercent(verdicts[v.key] ?? 0, withVerdict)]),
      }}
      empty={withVerdict === 0 ? 'Aucun verdict QA sur cette période (les workflows sans revue QA n’en produisent pas).' : null}
    >
      <HorizontalChart rows={rows} ticks={ticks} formatTick={(tick) => formatInteger(tick)} />
    </ChartCard>
  );
}
