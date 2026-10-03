import { useEffect, useRef } from 'react';
import Button from './ui/Button';

interface HistorySelectionBarProps {
  selectableCount: number;
  selectedCount: number;
  busy: boolean;
  onSelectAll: () => void;
  onClear: () => void;
  onDelete: () => void;
}

export default function HistorySelectionBar({
  selectableCount, selectedCount, busy, onSelectAll, onClear, onDelete,
}: HistorySelectionBarProps) {
  const checkboxRef = useRef<HTMLInputElement>(null);
  const allSelected = selectableCount > 0 && selectedCount === selectableCount;

  useEffect(() => {
    if (checkboxRef.current) checkboxRef.current.indeterminate = selectedCount > 0 && !allSelected;
  }, [selectedCount, allSelected]);

  if (selectableCount === 0) return null;

  return (
    <div className="selection-bar">
      <label>
        <input
          ref={checkboxRef}
          type="checkbox"
          checked={allSelected}
          disabled={busy}
          onChange={() => (allSelected ? onClear() : onSelectAll())}
        />
        Tout sélectionner
      </label>
      {selectedCount > 0 && (
        <>
          <span aria-live="polite" className="muted">
            {selectedCount} sélectionnée{selectedCount > 1 ? 's' : ''}
          </span>
          <Button variant="danger" onClick={onDelete} disabled={busy}>
            {busy ? '...' : `Supprimer la sélection (${selectedCount})`}
          </Button>
          <Button onClick={onClear} disabled={busy}>
            Annuler la sélection
          </Button>
        </>
      )}
    </div>
  );
}
