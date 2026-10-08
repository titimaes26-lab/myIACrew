import type { AgentMetricsRow } from '../../types';
import { formatCompact, formatInteger, niceTicks, percentOf } from '../../utils/metricsFormat';
import { TipRow } from './ChartTooltip';
import ChartCard from './ChartCard';
import HorizontalChart, { type HorizontalRow } from './HorizontalChart';

// Tokens moyens par exécution : entrée + sortie empilées (part du total), un seul axe.
export default function AgentTokensChart({ agents }: { agents: AgentMetricsRow[] }) {
  const withTokens = agents.filter((a) => a.token_runs > 0);
  const totals = withTokens.map((a) => (a.avg_prompt_tokens ?? 0) + (a.avg_completion_tokens ?? 0));
  const ticks = niceTicks(Math.max(0, ...totals), 4);
  const max = ticks[ticks.length - 1] || 1;

  const rows: HorizontalRow[] = withTokens.map((a, index) => {
    const prompt = a.avg_prompt_tokens ?? 0;
    const completion = a.avg_completion_tokens ?? 0;
    const measured = `${a.token_runs} exécution${a.token_runs > 1 ? 's' : ''} mesurée${a.token_runs > 1 ? 's' : ''} sur ${a.runs}`;
    return {
      key: a.agent,
      label: a.label,
      valueText: formatCompact(totals[index]),
      ariaLabel: `${a.label} : ${formatInteger(prompt)} tokens en entrée, ${formatInteger(completion)} en sortie, ${measured}`,
      tooltip: (
        <>
          <div className="viz-tip-title">{a.label}</div>
          <TipRow label="Entrée (prompt)" value={formatInteger(prompt)} keyColor="s1" />
          <TipRow label="Sortie (completion)" value={formatInteger(completion)} keyColor="s2" />
          <TipRow label="Total" value={formatInteger(totals[index])} />
          <div className="viz-sub" style={{ marginTop: 4 }}>{measured}</div>
        </>
      ),
      marks: (
        <div className="viz-hstack" style={{ width: percentOf(totals[index], max) }}>
          <span style={{ flexGrow: prompt }} />
          <span style={{ flexGrow: completion }} />
        </div>
      ),
    };
  });

  return (
    <ChartCard
      title="Tokens par agent"
      subtitle="Moyenne par exécution. La sortie comprend la réponse et le raisonnement interne du modèle."
      legend={(
        <>
          <span><i className="viz-key-bar" style={{ background: 'var(--viz-s1)' }} />Entrée (prompt)</span>
          <span><i className="viz-key-bar" style={{ background: 'var(--viz-s2)' }} />Sortie (completion)</span>
        </>
      )}
      table={{
        columns: ['Agent', 'Entrée', 'Sortie', 'Total', 'Mesures'],
        rows: withTokens.map((a, i) => [a.label, formatInteger(a.avg_prompt_tokens), formatInteger(a.avg_completion_tokens), formatInteger(totals[i]), `${a.token_runs}/${a.runs}`]),
      }}
      empty={rows.length === 0 ? "Le modèle n'a renvoyé aucun usage de tokens sur cette période." : null}
    >
      <HorizontalChart rows={rows} ticks={ticks} formatTick={formatCompact} />
    </ChartCard>
  );
}
