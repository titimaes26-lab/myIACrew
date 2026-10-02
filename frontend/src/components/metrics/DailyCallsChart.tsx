import type { DailyMetricsRow } from '../../types';
import { useChartTooltip } from '../../hooks/useChartTooltip';
import { useElementWidth } from '../../hooks/useElementWidth';
import { formatDay, formatInteger, niceTicks } from '../../utils/metricsFormat';
import { fillDays } from '../../utils/metricsSeries';
import { ChartTooltip, TipRow } from './ChartTooltip';
import ChartCard from './ChartCard';

const MAX_X_LABELS = 6;
const MIN_PX_PER_LABEL = 64; // largeur d'une date (« 29 sept. ») plus de l'air

// Appels LLM par jour : une seule série (le titre la nomme, pas de légende), colonnes <= 24 px.
export default function DailyCallsChart({ daily }: { daily: DailyMetricsRow[] }) {
  const { containerRef, tip, showAtPointer, showAtElement, hide } = useChartTooltip();
  const width = useElementWidth(containerRef);
  const days = fillDays(daily);
  const peak = Math.max(0, ...days.map((d) => d.llm_calls));
  const ticks = niceTicks(peak, 3);
  const max = ticks[ticks.length - 1] || 1;
  const peakIndex = days.findIndex((d) => d.llm_calls === peak);
  // Autant d'étiquettes que la largeur en permet (2 au minimum : premier et dernier jour).
  const fitting = Math.min(MAX_X_LABELS, Math.max(2, Math.floor((width || 600) / MIN_PX_PER_LABEL)));
  const labelStep = Math.ceil(days.length / fitting);

  const tooltipFor = (d: DailyMetricsRow) => (
    <>
      <div className="viz-tip-title">{formatDay(d.date)}</div>
      <TipRow label="Appels LLM" value={formatInteger(d.llm_calls)} keyColor="s1" />
      <TipRow label="Exécutions" value={formatInteger(d.executions)} />
      <TipRow label="Échecs" value={formatInteger(d.failed)} />
      <TipRow label="Tokens" value={d.tokens > 0 ? formatInteger(d.tokens) : '—'} />
    </>
  );

  return (
    <ChartCard
      title="Appels LLM par jour"
      subtitle="Appels réels au modèle, toutes exécutions terminées du jour."
      table={{
        columns: ['Jour', 'Exécutions', 'Échecs', 'Appels LLM', 'Tokens'],
        rows: daily.map((d) => [formatDay(d.date), String(d.executions), String(d.failed), formatInteger(d.llm_calls), d.tokens > 0 ? formatInteger(d.tokens) : '—']),
      }}
      empty={daily.length === 0 ? 'Aucune exécution terminée sur cette période.' : null}
    >
      <div className="viz-cwrap" ref={containerRef}>
        <div className="viz-yaxis" aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick} className="viz-ytick" style={{ bottom: `${(tick / max) * 100}%` }}>{formatInteger(tick)}</span>
          ))}
        </div>
        <div className="viz-cplot">
          <div className="viz-cols" style={{ gridTemplateColumns: `repeat(${days.length}, minmax(6px, 1fr))` }}>
            {ticks.map((tick) => (
              <div key={tick} className={`viz-cgrid${tick === 0 ? ' is-base' : ''}`} style={{ bottom: `${(tick / max) * 100}%` }} />
            ))}
            {days.map((d, index) => (
              <div
                key={d.date}
                className="viz-col"
                tabIndex={0}
                role="group"
                aria-label={`${formatDay(d.date)} : ${d.llm_calls} appels LLM, ${d.executions} exécution${d.executions > 1 ? 's' : ''}, ${d.failed} échec${d.failed > 1 ? 's' : ''}`}
                onPointerMove={(event) => showAtPointer(event, tooltipFor(d))}
                onPointerLeave={hide}
                onFocus={(event) => showAtElement(event.currentTarget, tooltipFor(d))}
                onBlur={hide}
              >
                {d.llm_calls > 0 && <div className="viz-colbar" style={{ height: `${(d.llm_calls / max) * 100}%` }} />}
                {index === peakIndex && peak > 0 && (
                  <span className="viz-collabel" style={{ bottom: `${(d.llm_calls / max) * 100}%` }}>{formatInteger(d.llm_calls)}</span>
                )}
              </div>
            ))}
          </div>
          <div className="viz-xaxis" aria-hidden="true">
            {days.map((d, index) => (index % labelStep === 0 || index === days.length - 1) && (
              <span key={d.date} className="viz-xtick" style={{ left: `${((index + 0.5) / days.length) * 100}%` }}>{formatDay(d.date)}</span>
            ))}
          </div>
        </div>
        <ChartTooltip tip={tip} />
      </div>
    </ChartCard>
  );
}
