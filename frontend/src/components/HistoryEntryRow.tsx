import type { MouseEvent } from 'react';
import type { ExecutionHistoryEntry } from '../types';
import { toServerDate } from '../utils/serverDate';

const STATUS_LABEL: Record<ExecutionHistoryEntry['status'], string> = {
  running: '🔄 En cours',
  success: '✅ Succès',
  failed: '❌ Échec',
};

interface HistoryEntryRowProps {
  entry: ExecutionHistoryEntry;
  checked: boolean;
  busy: boolean;
  onToggle: (id: number, shiftKey: boolean) => void;
  onResume: (conversationId: number) => void;
  onDelete: (id: number) => void;
}

export default function HistoryEntryRow({ entry, checked, busy, onToggle, onResume, onDelete }: HistoryEntryRowProps) {
  const running = entry.status === 'running';
  const date = toServerDate(entry.created_at).toLocaleString('fr-FR');

  // onClick (et non onChange) : seul l'événement souris porte shiftKey ; Espace déclenche aussi un click.
  const handleClick = (event: MouseEvent<HTMLInputElement>) => onToggle(entry.id, event.shiftKey);

  return (
    <li
      style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '10px', padding: '10px', border: '1px solid #e1e4e8', borderRadius: '6px', backgroundColor: checked ? '#eff6ff' : undefined }}
    >
      <input
        type="checkbox"
        checked={checked}
        disabled={running || busy}
        title={running ? 'Une exécution en cours ne peut pas être supprimée.' : undefined}
        aria-label={`Sélectionner l'exécution du ${date} : ${entry.user_request.slice(0, 60)}`}
        onClick={handleClick}
        onChange={() => undefined}
        style={{ marginTop: '3px', flexShrink: 0 }}
      />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div style={{ fontSize: '13px', color: '#666' }}>
          {date} · {entry.workflow} · {STATUS_LABEL[entry.status]}
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
            onClick={() => onResume(entry.conversation_id!)}
            style={{ padding: '6px 10px', backgroundColor: '#e0f2fe', color: '#075985', border: '1px solid #7dd3fc', borderRadius: '6px', cursor: 'pointer', fontSize: '13px' }}
          >
            Reprendre
          </button>
        )}
        {!running && (
          <button
            type="button"
            onClick={() => onDelete(entry.id)}
            disabled={busy}
            style={{ padding: '6px 10px', backgroundColor: '#fef2f2', color: '#991b1b', border: '1px solid #fca5a5', borderRadius: '6px', cursor: busy ? 'not-allowed' : 'pointer', fontSize: '13px' }}
          >
            Supprimer
          </button>
        )}
      </div>
    </li>
  );
}
