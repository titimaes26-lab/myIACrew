import { useState, type ReactNode } from 'react';
import DataTable, { type TableData } from './DataTable';

interface ChartCardProps {
  title: string;
  subtitle?: string;
  legend?: ReactNode;
  table: TableData;
  // Message affiché à la place du graphique quand il n'y a rien à tracer (le tableau reste alors masqué).
  empty?: string | null;
  // Sans graphique (carte « tableau seul »), pas de bascule.
  children?: ReactNode;
}

// Conteneur d'un graphique : titre, légende et jumeau accessible (le tableau des mêmes valeurs).
export default function ChartCard({ title, subtitle, legend, table, empty, children }: ChartCardProps) {
  const [view, setView] = useState<'chart' | 'table'>('chart');
  const showTable = !children || view === 'table';

  return (
    <section className="viz-card" aria-label={title}>
      <div className="viz-card-head">
        <div>
          <h4 className="viz-card-title">{title}</h4>
          {subtitle && <p className="viz-sub">{subtitle}</p>}
        </div>
        {children && !empty && (
          <button type="button" className="viz-toggle" aria-pressed={view === 'table'} onClick={() => setView(view === 'table' ? 'chart' : 'table')}>
            {view === 'table' ? '📈 Graphique' : '📋 Tableau'}
          </button>
        )}
      </div>
      {empty ? (
        <p className="viz-empty">{empty}</p>
      ) : showTable ? (
        <DataTable {...table} label={title} />
      ) : (
        <>
          {legend && <div className="viz-legend">{legend}</div>}
          {children}
        </>
      )}
    </section>
  );
}
