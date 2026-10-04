import { useCallback, useState, type KeyboardEvent } from 'react';
import type { DailyMetricsRow } from '../../types';
import { useChartTooltip } from '../../hooks/useChartTooltip';
import { useElementWidth } from '../../hooks/useElementWidth';
import { DAILY_METRICS, dailyMetric, type DailyMetricKey } from '../../utils/dailyMetrics';
import { formatCompact, formatDay, formatInteger } from '../../utils/metricsFormat';
import { fillDays } from '../../utils/metricsSeries';
import { formatSeconds } from '../../utils/formatDuration';
import { ChartTooltip, TipRow } from './ChartTooltip';
import ChartCard from './ChartCard';

const MAX_X_LABELS = 6;
const MIN_PX_PER_LABEL = 64; // largeur d'une date (« 29 sept. ») plus de l'air

// Tendance quotidienne d'UNE métrique à la fois (sélecteur : appels LLM, durée médiane, taux de succès,
// tokens) : une seule série (le titre la nomme, pas de légende), colonnes <= 24 px. Un jour sans donnée
// n'a pas de colonne.
export default function DailyTrendsChart({ daily }: { daily: DailyMetricsRow[] }) {
  const [metricKey, setMetricKey] = useState<DailyMetricKey>('llm_calls');
  const metric = dailyMetric(metricKey);
  const { containerRef, tip, showAtPointer, showAtElement, hide, hideUnlessTouch } = useChartTooltip();
  const { width, ref: widthRef } = useElementWidth();
  // Le même élément sert au positionnement de l'infobulle ET à la mesure de largeur.
  const setContainer = useCallback((node: HTMLDivElement | null) => {
    containerRef.current = node;
    widthRef(node);
  }, [containerRef, widthRef]);
  // Une seule colonne dans l'ordre de tabulation (« roving tabindex ») ; les flèches changent de jour.
  const [focusIndex, setFocusIndex] = useState(0);
  const days = fillDays(daily);
  const values = days.map((d) => metric.value(d));
  const peak = Math.max(0, ...values.map((value) => value ?? 0));
  const ticks = metric.ticks(peak);
  const max = ticks[ticks.length - 1] || 1;
  const peakIndex = values.findIndex((value) => value !== null && value === peak);
  // Autant d'étiquettes que la largeur en permet (2 au minimum : premier et dernier jour).
  const fitting = Math.min(MAX_X_LABELS, Math.max(2, Math.floor((width || 600) / MIN_PX_PER_LABEL)));
  const labelStep = Math.ceil(days.length / fitting);

  const moveFocus = (event: KeyboardEvent, index: number) => {
    const target = { ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: days.length - 1 }[event.key];
    if (target === undefined || target < 0 || target >= days.length) return;
    event.preventDefault();
    setFocusIndex(target);
    containerRef.current?.querySelectorAll<HTMLElement>('.viz-col')[target]?.focus();
  };

  const valueText = (d: DailyMetricsRow) => {
    const value = metric.value(d);
    return value === null ? 'aucune donnée' : metric.format(value);
  };

  const tooltipFor = (d: DailyMetricsRow) => (
    <>
      <div className="viz-tip-title">{formatDay(d.date)}</div>
      <TipRow label={metric.label} value={valueText(d)} keyColor="s1" />
      <TipRow label="Exécutions" value={formatInteger(d.executions)} />
      <TipRow label="Échecs" value={formatInteger(d.failed)} />
    </>
  );

  return (
    <ChartCard
      title={metric.title}
      subtitle={metric.subtitle}
      table={{
        columns: ['Jour', 'Exécutions', 'Échecs', 'Appels LLM', 'Tokens', 'Durée médiane', 'Taux de succès'],
        rows: daily.map((d) => [
          formatDay(d.date), String(d.executions), String(d.failed), formatInteger(d.llm_calls),
          d.tokens > 0 ? formatCompact(d.tokens) : '—',
          d.median_duration_seconds === null ? '—' : formatSeconds(d.median_duration_seconds),
          dailyMetric('success').value(d) === null ? '—' : dailyMetric('success').format(dailyMetric('success').value(d) ?? 0),
        ]),
      }}
      empty={daily.length === 0 ? 'Aucune exécution terminée sur cette période.' : null}
    >
      <div className="viz-seg viz-metric-seg" role="group" aria-label="Métrique affichée">
        {DAILY_METRICS.map((option) => (
          <button key={option.key} type="button" aria-pressed={metricKey === option.key} onClick={() => setMetricKey(option.key)}>
            {option.label}
          </button>
        ))}
      </div>
      <div className="viz-cwrap" ref={setContainer}>
        <div className="viz-yaxis" aria-hidden="true">
          {ticks.map((tick) => (
            <span key={tick} className="viz-ytick" style={{ bottom: `${(tick / max) * 100}%` }}>{metric.format(tick)}</span>
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
                tabIndex={index === Math.min(focusIndex, days.length - 1) ? 0 : -1}
                role="group"
                data-viz-mark=""
                aria-label={`${formatDay(d.date)} : ${metric.label} ${valueText(d)}, ${d.executions} exécution${d.executions > 1 ? 's' : ''}, ${d.failed} échec${d.failed > 1 ? 's' : ''}`}
                onPointerDown={(event) => showAtPointer(event, tooltipFor(d))}
                onPointerMove={(event) => showAtPointer(event, tooltipFor(d))}
                onPointerLeave={hideUnlessTouch}
                onKeyDown={(event) => moveFocus(event, index)}
                onFocus={(event) => { setFocusIndex(index); showAtElement(event.currentTarget, tooltipFor(d)); }}
                onBlur={hide}
              >
                {values[index] !== null && <div className="viz-colbar" style={{ height: `${((values[index] ?? 0) / max) * 100}%` }} />}
                {metric.labelPeak && index === peakIndex && peak > 0 && (
                  <span className="viz-collabel" style={{ bottom: `${((values[index] ?? 0) / max) * 100}%` }}>{metric.format(peak)}</span>
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
