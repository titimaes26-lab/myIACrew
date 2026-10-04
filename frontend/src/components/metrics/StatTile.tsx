import type { Delta } from '../../utils/metricsDelta';

interface StatTileProps {
  label: string;
  value: string;
  sub?: string;
  // Écart par rapport à la période précédente (voir computeDelta) ; absent = pas de comparaison possible.
  delta?: Delta | null;
}

export default function StatTile({ label, value, sub, delta }: StatTileProps) {
  return (
    <div className="viz-tile">
      <div className="viz-tile-label">{label}</div>
      <div className="viz-tile-value">{value}</div>
      {delta && (
        <div className={`viz-delta viz-delta--${delta.tone}`}>
          {/* Le texte visible porte le sens (flèche + signe) ; la phrase complète est pour les lecteurs d'écran. */}
          <span aria-hidden="true">{delta.text}</span>
          <span className="sr-only">{delta.label}</span>
        </div>
      )}
      {sub && <div className="viz-tile-sub">{sub}</div>}
    </div>
  );
}
