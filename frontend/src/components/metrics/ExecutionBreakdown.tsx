import { useContext, useEffect, useMemo, useState } from 'react';
import { apiClient } from '../../api';
import { MetricsApiContext } from '../../hooks/metricsApi';
import type { AgentRunView } from '../../types';
import { formatSeconds } from '../../utils/formatDuration';
import { formatCompact, formatInteger } from '../../utils/metricsFormat';
import './metrics.css';

interface Loaded {
  executionId: number;
  runs: AgentRunView[] | null;
  error: string | null;
}

function Rows({ runs }: { runs: AgentRunView[] }) {
  const slowest = Math.max(1, ...runs.map((r) => r.duration_seconds ?? 0));
  return (
    <div className="viz-table-wrap">
      <table className="viz-table" aria-label="Performance par agent de cette exécution">
        <thead>
          <tr><th scope="col">Agent</th><th scope="col">Durée</th><th scope="col" /><th scope="col">Appels LLM</th><th scope="col">Tokens</th><th scope="col">Outils</th></tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.agent}>
              <th scope="row">
                {r.label}{r.status === 'incomplete' ? ' ⚠️ incomplet' : ''}
              </th>
              <td>{r.duration_seconds == null ? '—' : formatSeconds(r.duration_seconds)}</td>
              <td aria-hidden="true">
                <span className="viz-miniplot"><i style={{ width: `${((r.duration_seconds ?? 0) / slowest) * 100}%` }} /></span>
              </td>
              <td>{formatInteger(r.llm_calls)}{r.llm_errors ? ` (${r.llm_errors} err.)` : ''}</td>
              <td>{r.tokens_known ? formatCompact(r.total_tokens) : '—'}</td>
              <td>{formatInteger(r.tool_calls)}{r.tool_errors ? ` (${r.tool_errors} err.)` : ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Détail par agent d'UNE exécution, chargé seulement quand on l'ouvre. Rien n'est affiché sans
// configuration d'API (contexte) ni pour les tours qui n'ont pas d'identifiant serveur.
export default function ExecutionBreakdown({ executionId }: { executionId: number }) {
  const config = useContext(MetricsApiContext);
  const [open, setOpen] = useState(false);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const api = useMemo(() => (config ? apiClient(config.apiUrl, config.accessToken) : null), [config]);

  useEffect(() => {
    if (!open || !api) return;
    const controller = new AbortController();
    api.getExecutionAgentRuns(executionId, controller.signal)
      .then((runs) => setLoaded({ executionId, runs, error: null }))
      .catch((err: unknown) => {
        if (!controller.signal.aborted) {
          setLoaded({ executionId, runs: null, error: err instanceof Error ? err.message : 'Impossible de charger le détail.' });
        }
      });
    return () => controller.abort();
  }, [open, api, executionId]);

  if (!api) return null;
  const current = loaded?.executionId === executionId ? loaded : null;

  return (
    <div className="viz-break">
      <button type="button" className="viz-break-toggle" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
        📊 {open ? 'Masquer' : 'Voir'} la performance par agent
      </button>
      {open && (
        <div className="viz-root viz-break-body">
          {!current && <p className="viz-sub">Chargement…</p>}
          {current?.error && <p role="alert">❌ {current.error}</p>}
          {current?.runs && current.runs.length === 0 && (
            <p className="viz-sub">Aucune mesure pour cette exécution (lancée avant l'ajout du suivi par agent).</p>
          )}
          {current?.runs && current.runs.length > 0 && <Rows runs={current.runs} />}
        </div>
      )}
    </div>
  );
}
