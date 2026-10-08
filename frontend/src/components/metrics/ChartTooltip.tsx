import type { ReactNode } from 'react';
import type { TooltipState } from '../../hooks/useChartTooltip';

export function ChartTooltip({ tip }: { tip: TooltipState | null }) {
  if (!tip) return null;
  return (
    <div className="viz-tip" role="tooltip" style={{ left: tip.x, top: tip.y }}>
      {tip.content}
    </div>
  );
}

interface TipRowProps {
  label: string;
  value: string;
  // Couleur de la marque, reprise sous forme de trait court : le texte, lui, reste en encre.
  keyColor?: 's1' | 's2';
}

export function TipRow({ label, value, keyColor }: TipRowProps): ReactNode {
  return (
    <div className="viz-tip-row">
      <span>
        {keyColor && <i className="viz-key-line" style={{ background: `var(--viz-${keyColor})` }} />}
        {label}
      </span>
      <strong>{value}</strong>
    </div>
  );
}
