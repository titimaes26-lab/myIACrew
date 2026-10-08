import { Fragment, useMemo, useState } from 'react';
import { MetricsApiContext } from '../../hooks/metricsApi';
import { useMetricsExecutions, type ExecutionStatusFilter } from '../../hooks/useMetricsExecutions';
import type { ExecutionRow, ExecutionSortKey, MetricsWorkflowFilter, QaVerdict } from '../../types';
import { formatSeconds } from '../../utils/formatDuration';
import { formatCompact, formatInteger } from '../../utils/metricsFormat';
import { toServerDate } from '../../utils/serverDate';
import { statusTone } from '../../utils/statusTone';
import Badge from '../ui/Badge';
import { ExecutionDetail } from './ExecutionBreakdown';

const PAGE_SIZE = 10;
const QA_LABEL: Record<QaVerdict, string> = { GO: 'GO', GO_AVEC_RESERVES: 'GO avec réserves', NO_GO: 'NO GO' };
const STATUS_FILTERS: { key: ExecutionStatusFilter; label: string }[] = [
  { key: 'all', label: 'Toutes' }, { key: 'success', label: 'Réussies' }, { key: 'failed', label: 'Échecs' },
];
const SORTABLE: { key: ExecutionSortKey; label: string }[] = [
  { key: 'created_at', label: 'Date' }, { key: 'duration', label: 'Durée' },
  { key: 'llm_calls', label: 'Appels LLM' }, { key: 'tokens', label: 'Tokens' },
];

interface SortState {
  sort: ExecutionSortKey;
  order: 'asc' | 'desc';
}

function SortableHeader({ column, state, onSort }: { column: { key: ExecutionSortKey; label: string }; state: SortState; onSort: (key: ExecutionSortKey) => void }) {
  const active = state.sort === column.key;
  return (
    <th scope="col" aria-sort={active ? (state.order === 'asc' ? 'ascending' : 'descending') : 'none'}>
      <button type="button" className="viz-sort" onClick={() => onSort(column.key)}>
        {column.label}{active ? (state.order === 'asc' ? ' ▲' : ' ▼') : ''}
      </button>
    </th>
  );
}

function RowSummary({ row, open, onToggle }: { row: ExecutionRow; open: boolean; onToggle: () => void }) {
  return (
    <tr>
      <td>
        <button type="button" className="viz-sort" aria-expanded={open} onClick={onToggle} aria-label={`${open ? 'Masquer' : 'Voir'} le détail de l'exécution du ${toServerDate(row.created_at).toLocaleString('fr-FR')}`}>
          {open ? '▾' : '▸'} {toServerDate(row.created_at).toLocaleString('fr-FR', { dateStyle: 'short', timeStyle: 'short' })}
        </button>
      </td>
      <td className="viz-request" title={row.user_request}>{row.user_request}</td>
      <td>{row.workflow}</td>
      <td>{row.status === 'success' ? '✅ Succès' : '❌ Échec'}</td>
      <td>{row.duration_seconds == null ? '—' : formatSeconds(row.duration_seconds)}</td>
      <td>{row.llm_calls == null ? '—' : formatInteger(row.llm_calls)}</td>
      <td>{row.tokens == null ? '—' : formatCompact(row.tokens)}</td>
      <td>
        {row.qa_verdict && <Badge tone={statusTone(row.qa_verdict)}>{QA_LABEL[row.qa_verdict]}</Badge>}
        {row.attempts > 1 && <Badge tone="warning" title="Relancée automatiquement après une panne temporaire">↻ relance</Badge>}
        {row.reused_steps > 0 && <Badge title={`${row.reused_steps} étape(s) reprise(s) d'une exécution précédente`}>⏩ reprise</Badge>}
      </td>
    </tr>
  );
}

// Exécutions terminées de la période : triables, filtrables par statut, paginées ; une ligne se déplie sur sa
// chronologie et son détail par agent. Le tri se fait côté serveur (toutes les pages, pas seulement l'affichée).
export default function ExecutionsTable({ apiUrl, accessToken, days, workflow }: { apiUrl: string; accessToken: string; days: number; workflow: MetricsWorkflowFilter }) {
  const [status, setStatus] = useState<ExecutionStatusFilter>('all');
  const [sortState, setSortState] = useState<SortState>({ sort: 'created_at', order: 'desc' });
  // Le décalage est rattaché à l'ensemble filtre + tri + période : en changer ramène à la première page
  // sans effet de bord (valeur dérivée plutôt qu'un setState dans un effet).
  const scope = `${days}|${workflow}|${status}|${sortState.sort}|${sortState.order}`;
  const [pageState, setPageState] = useState({ scope, offset: 0 });
  const offset = pageState.scope === scope ? pageState.offset : 0;
  const [openId, setOpenId] = useState<number | null>(null);
  // Le détail d'une ligne lit l'API par le contexte (comme sous un message du fil) : il est fourni ici pour que ce
  // tableau fonctionne partout où le panneau est monté.
  const apiConfig = useMemo(() => ({ apiUrl, accessToken }), [apiUrl, accessToken]);
  const { page, error, loading } = useMetricsExecutions(apiUrl, accessToken, {
    days, workflow, status, sort: sortState.sort, order: sortState.order, limit: PAGE_SIZE, offset,
  });

  const onSort = (key: ExecutionSortKey) => setSortState((current) => (
    current.sort === key ? { sort: key, order: current.order === 'desc' ? 'asc' : 'desc' } : { sort: key, order: 'desc' }
  ));
  const total = page?.total ?? 0;
  const goTo = (next: number) => setPageState({ scope, offset: next });

  return (
    <MetricsApiContext.Provider value={apiConfig}>
    <section className="viz-card" aria-label="Exécutions récentes" aria-busy={loading}>
      <div className="viz-card-head">
        <div>
          <h4 className="viz-card-title">Exécutions</h4>
          <p className="viz-sub">Triez, filtrez, puis dépliez une ligne pour voir sa chronologie et son détail par agent.</p>
        </div>
        <div className="viz-seg" role="group" aria-label="Statut">
          {STATUS_FILTERS.map((filter) => (
            <button key={filter.key} type="button" aria-pressed={status === filter.key} onClick={() => setStatus(filter.key)}>{filter.label}</button>
          ))}
        </div>
      </div>
      {error && <div className="viz-error" role="alert">❌ {error}</div>}
      {!page && !error && <p className="viz-empty">Chargement…</p>}
      {page && page.items.length === 0 && <p className="viz-empty">Aucune exécution ne correspond à ces filtres.</p>}
      {page && page.items.length > 0 && (
        <div className={`viz-table-wrap${loading ? ' viz-loading' : ''}`}>
          <table className="viz-table viz-exec-table">
            <thead>
              <tr>
                <SortableHeader column={SORTABLE[0]} state={sortState} onSort={onSort} />
                <th scope="col">Demande</th>
                <th scope="col">Workflow</th>
                <th scope="col">Statut</th>
                <SortableHeader column={SORTABLE[1]} state={sortState} onSort={onSort} />
                <SortableHeader column={SORTABLE[2]} state={sortState} onSort={onSort} />
                <SortableHeader column={SORTABLE[3]} state={sortState} onSort={onSort} />
                <th scope="col">Qualité</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((row) => (
                <Fragment key={row.id}>
                  <RowSummary row={row} open={openId === row.id} onToggle={() => setOpenId((current) => (current === row.id ? null : row.id))} />
                  {openId === row.id && (
                    <tr>
                      <td colSpan={8}><div className="viz-exec-detail"><ExecutionDetail executionId={row.id} totalSeconds={row.duration_seconds} /></div></td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {page && total > PAGE_SIZE && (
        <div className="viz-pager">
          <button type="button" className="viz-toggle" disabled={offset === 0} onClick={() => goTo(Math.max(0, offset - PAGE_SIZE))}>← Précédentes</button>
          <span className="viz-sub" aria-live="polite">{offset + 1}–{Math.min(total, offset + PAGE_SIZE)} sur {total}</span>
          <button type="button" className="viz-toggle" disabled={offset + PAGE_SIZE >= total} onClick={() => goTo(offset + PAGE_SIZE)}>Suivantes →</button>
        </div>
      )}
    </section>
    </MetricsApiContext.Provider>
  );
}
