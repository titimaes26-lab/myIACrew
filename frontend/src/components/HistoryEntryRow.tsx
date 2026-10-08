import type { MouseEvent } from 'react';
import Button from './ui/Button';
import type { HistoryListEntry } from '../types';
import { toServerDate } from '../utils/serverDate';

const STATUS_LABEL: Record<HistoryListEntry['status'], string> = {
  running: '🔄 En cours',
  success: '✅ Succès',
  failed: '❌ Échec',
};

interface HistoryEntryRowProps {
  entry: HistoryListEntry;
  checked: boolean;
  busy: boolean;
  deleting: boolean;
  onToggle: (id: number, shiftKey: boolean) => void;
  onResume: (conversationId: number) => void;
  onDelete: (id: number) => void;
}

export default function HistoryEntryRow({ entry, checked, busy, deleting, onToggle, onResume, onDelete }: HistoryEntryRowProps) {
  const running = entry.status === 'running';
  const date = toServerDate(entry.created_at).toLocaleString('fr-FR');

  // onClick (et non onChange) : seul l'événement souris porte shiftKey ; Espace déclenche aussi un click.
  const handleClick = (event: MouseEvent<HTMLInputElement>) => onToggle(entry.id, event.shiftKey);

  return (
    <li className={checked ? 'history-row history-row--selected' : 'history-row'}>
      <input
        type="checkbox"
        checked={checked}
        disabled={running || busy}
        title={running ? 'Une exécution en cours ne peut pas être supprimée.' : undefined}
        aria-label={`Sélectionner l'exécution du ${date} : ${entry.user_request.slice(0, 60)}`}
        onClick={handleClick}
        // Maj+clic ne doit pas aussi sélectionner le texte entre les deux lignes.
        onMouseDown={(event) => { if (event.shiftKey) event.preventDefault(); }}
        onChange={() => undefined}
      />
      <div className="grow">
        <div className="history-row__meta">
          {date} · {entry.workflow} · {STATUS_LABEL[entry.status]}
          {entry.repo_owner && entry.repo_name && ` · ${entry.repo_owner}/${entry.repo_name}`}
        </div>
        <div className="history-row__text">
          {entry.user_request}
        </div>
      </div>
      <div className="history-row__actions">
        {entry.conversation_id !== null && (
          <Button variant="info" onClick={() => onResume(entry.conversation_id!)}>
            Reprendre
          </Button>
        )}
        {!running && (
          <Button variant="danger" onClick={() => onDelete(entry.id)} disabled={busy}>
            {deleting ? '...' : 'Supprimer'}
          </Button>
        )}
      </div>
    </li>
  );
}
