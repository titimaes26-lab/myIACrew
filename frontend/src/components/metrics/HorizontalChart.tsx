import type { ReactNode } from 'react';
import { useChartTooltip } from '../../hooks/useChartTooltip';
import { ChartTooltip } from './ChartTooltip';

export interface HorizontalRow {
  key: string;
  label: string;
  // Valeur écrite au bout de la barre (la lecture principale, sans survol).
  valueText: string;
  ariaLabel: string;
  tooltip: ReactNode;
  // Marques déjà positionnées en pourcentage de l'axe (barre, pile, point).
  marks: ReactNode;
}

interface HorizontalChartProps {
  rows: HorizontalRow[];
  ticks: number[];
  formatTick: (value: number) => string;
}

// Barres horizontales sur UN axe (jamais deux échelles). Chaque ligne est une cible de survol et de
// focus clavier ; les graduations sont écrites une seule fois, sous la dernière ligne.
export default function HorizontalChart({ rows, ticks, formatTick }: HorizontalChartProps) {
  const { containerRef, tip, showAtPointer, showAtElement, hide } = useChartTooltip();
  const max = ticks[ticks.length - 1] || 1;
  const gridLines = ticks.map((tick) => <div key={tick} className="viz-hgrid" style={{ left: `${(tick / max) * 100}%` }} />);

  return (
    <div className="viz-hbars" ref={containerRef}>
      {rows.map((row) => (
        <div
          key={row.key}
          className="viz-hrow"
          tabIndex={0}
          role="group"
          aria-label={row.ariaLabel}
          onPointerMove={(event) => showAtPointer(event, row.tooltip)}
          onPointerLeave={hide}
          onFocus={(event) => showAtElement(event.currentTarget, row.tooltip)}
          onBlur={hide}
        >
          <span className="viz-hlabel">{row.label}</span>
          <div className="viz-hplot">
            {gridLines}
            {row.marks}
          </div>
          <span className="viz-hvalue">{row.valueText}</span>
        </div>
      ))}
      <div className="viz-haxis" aria-hidden="true">
        <span />
        <div className="viz-haxis-plot">
          {ticks.map((tick) => (
            <span key={tick} className="viz-tick" style={{ left: `${(tick / max) * 100}%` }}>{formatTick(tick)}</span>
          ))}
        </div>
        <span />
      </div>
      <ChartTooltip tip={tip} />
    </div>
  );
}
