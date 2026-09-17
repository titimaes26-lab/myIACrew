import { useEffect, useState } from 'react';
import { apiClient } from '../api';
import type { ExecutionHistoryEntry } from '../types';

interface HistoryPanelProps {
  apiUrl: string;
  accessToken: string;
  onResumeConversation: (conversationId: number) => void;
}

const STATUS_LABEL: Record<ExecutionHistoryEntry['status'], string> = {
  running: '🔄 En cours',
  success: '✅ Succès',
  failed: '❌ Échec',
};

export default function HistoryPanel({ apiUrl, accessToken, onResumeConversation }: HistoryPanelProps) {
  const [entries, setEntries] = useState<ExecutionHistoryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    const api = apiClient(apiUrl, accessToken);

    api.listHistory()
      .then((data) => {
        if (!cancelled) setEntries(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Impossible de charger l'historique.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [apiUrl, accessToken]);

  const handleDelete = async (id: number) => {
    if (!window.confirm('Supprimer cette exécution de l\'historique ?')) return;

    setDeletingId(id);
    setError(null);
    try {
      await apiClient(apiUrl, accessToken).deleteHistoryEntry(id);
      setEntries((current) => current.filter((entry) => entry.id !== id));
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Suppression impossible.');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div style={{ marginTop: '20px', backgroundColor: '#fff', padding: '15px', borderRadius: '6px', border: '1px solid #e1e4e8' }}>
      <p style={{ margin: '0 0 10px 0', fontWeight: 'bold' }}>📜 Historique des exécutions</p>

      {loading && <p style={{ color: '#666', fontSize: '14px' }}>Chargement...</p>}
      {error && <p style={{ color: '#991b1b', fontSize: '14px' }}>❌ {error}</p>}
      {!loading && !error && entries.length === 0 && (
        <p style={{ color: '#666', fontSize: '14px' }}>Aucune exécution pour l'instant.</p>
      )}

      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '10px' }}>
        {entries.map((entry) => (
          <li
            key={entry.id}
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '10px', padding: '10px', border: '1px solid #e1e4e8', borderRadius: '6px' }}
          >
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: '13px', color: '#666' }}>
                {new Date(entry.created_at).toLocaleString('fr-FR')} · {entry.workflow} · {STATUS_LABEL[entry.status]}
                {entry.repo_owner && entry.repo_name && ` · ${entry.repo_owner}/${entry.repo_name}`}
              </div>
              <div style={{ fontSize: '14px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {entry.user_request}
              </div>
            </div>
            <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
              {entry.conversation_id !== null && (
                <button
                  type="button"
                  onClick={() => onResumeConversation(entry.conversation_id!)}
                  style={{ padding: '6px 10px', backgroundColor: '#e0f2fe', color: '#075985', border: '1px solid #7dd3fc', borderRadius: '6px', cursor: 'pointer', fontSize: '13px' }}
                >
                  Reprendre
                </button>
              )}
              <button
                type="button"
                onClick={() => handleDelete(entry.id)}
                disabled={deletingId === entry.id}
                style={{ padding: '6px 10px', backgroundColor: '#fef2f2', color: '#991b1b', border: '1px solid #fca5a5', borderRadius: '6px', cursor: deletingId === entry.id ? 'not-allowed' : 'pointer', fontSize: '13px' }}
              >
                {deletingId === entry.id ? '...' : 'Supprimer'}
              </button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
