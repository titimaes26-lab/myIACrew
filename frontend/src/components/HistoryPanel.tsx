import { useEffect, useMemo, useState } from 'react';
import Card from './ui/Card';
import Alert from './ui/Alert';
import { apiClient } from '../api';
import type { ExecutionHistoryEntry } from '../types';
import { useHistorySelection } from '../hooks/useHistorySelection';
import HistoryEntryRow from './HistoryEntryRow';
import HistorySelectionBar from './HistorySelectionBar';
import ErrorBanner from './ErrorBanner';
import { toDisplayedError, type DisplayedError } from '../utils/errors';

interface HistoryPanelProps {
  apiUrl: string;
  accessToken: string;
  onResumeConversation: (conversationId: number) => void;
}

const SKIP_REASON_LABEL = { running: 'en cours', not_found: 'introuvable' } as const;

function plural(count: number, singular: string, pluralForm: string): string {
  return `${count} ${count > 1 ? pluralForm : singular}`;
}

export default function HistoryPanel({ apiUrl, accessToken, onResumeConversation }: HistoryPanelProps) {
  const [entries, setEntries] = useState<ExecutionHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<(DisplayedError & { kind: 'load' | 'action' }) | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const selectableIds = useMemo(
    () => entries.filter((entry) => entry.status !== 'running').map((entry) => entry.id),
    [entries],
  );
  const { selected, toggle, selectAll, clear } = useHistorySelection(selectableIds);

  useEffect(() => {
    let cancelled = false;
    const api = apiClient(apiUrl, accessToken);

    api.listHistory()
      .then((data) => {
        if (!cancelled) setEntries(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError({ ...toDisplayedError(err, "Impossible de charger l'historique."), kind: 'load' });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiUrl, accessToken, reloadKey]);

  const handleDelete = async (id: number) => {
    if (!window.confirm('Supprimer cette exécution de l\'historique ?')) return;

    setBusy(true);
    setDeletingId(id);
    setError(null);
    setNotice(null);
    try {
      await apiClient(apiUrl, accessToken).deleteHistoryEntry(id);
      setEntries((current) => current.filter((entry) => entry.id !== id));
    } catch (err: unknown) {
      setError({ ...toDisplayedError(err, 'Suppression impossible.'), kind: 'action' });
    } finally {
      setBusy(false);
      setDeletingId(null);
    }
  };

  const handleBulkDelete = async () => {
    const ids = [...selected];
    if (ids.length === 0) return;
    if (!window.confirm(
      ids.length > 1
        ? `Supprimer ces ${ids.length} exécutions de l'historique ?`
        : 'Supprimer cette exécution de l\'historique ?',
    )) return;

    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await apiClient(apiUrl, accessToken).deleteHistoryEntries(ids);
      const removed = new Set(result.deleted);
      // Les lignes ignorées (en cours, déjà supprimées ailleurs) restent affichées, sauf « introuvable ».
      result.skipped.filter((item) => item.reason === 'not_found').forEach((item) => removed.add(item.id));
      setEntries((current) => current.filter((entry) => !removed.has(entry.id)));
      clear();
      if (result.skipped.length > 0) {
        // Une ligne ignorée a changé côté serveur (ex: passée « en cours ») : on recharge la liste.
        apiClient(apiUrl, accessToken).listHistory().then(setEntries).catch(() => undefined);
        const reasons = [...new Set(result.skipped.map((item) => SKIP_REASON_LABEL[item.reason]))].join(', ');
        setNotice(
          `${plural(result.deleted.length, 'supprimée', 'supprimées')}, `
          + `${plural(result.skipped.length, 'ignorée', 'ignorées')} : ${reasons}`,
        );
      }
    } catch (err: unknown) {
      setError({ ...toDisplayedError(err, 'Suppression impossible.'), kind: 'action' });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card className="stack--section">
      <p className="history-title">📜 Historique des exécutions</p>

      {loading && <p className="muted">Chargement...</p>}
      {error && (
        <ErrorBanner
          message={error.message}
          retryable={error.retryable}
          // Seul l'échec de chargement de la liste se relance d'un clic : une suppression se refait depuis la ligne.
          onRetry={error.kind === 'load' ? () => { setError(null); setLoading(true); setReloadKey((k) => k + 1); } : undefined}
        />
      )}
      {notice && <Alert tone="warning">ℹ️ {notice}</Alert>}
      {!loading && !error && entries.length === 0 && (
        <p className="muted">Aucune exécution pour l'instant.</p>
      )}

      <HistorySelectionBar
        selectableCount={selectableIds.length}
        selectedCount={selected.size}
        busy={busy}
        onSelectAll={selectAll}
        onClear={clear}
        onDelete={handleBulkDelete}
      />

      <ul className="history-list">
        {entries.map((entry) => (
          <HistoryEntryRow
            key={entry.id}
            entry={entry}
            checked={selected.has(entry.id)}
            busy={busy}
            deleting={deletingId === entry.id}
            onToggle={toggle}
            onResume={onResumeConversation}
            onDelete={handleDelete}
          />
        ))}
      </ul>
    </Card>
  );
}
