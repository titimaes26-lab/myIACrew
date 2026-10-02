import { useEffect, useRef } from 'react';

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
    <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '10px', marginBottom: '10px', fontSize: '13px' }}>
      <label style={{ display: 'flex', alignItems: 'center', gap: '6px', cursor: 'pointer' }}>
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
          <span aria-live="polite" style={{ color: '#666' }}>
            {selectedCount} sélectionnée{selectedCount > 1 ? 's' : ''}
          </span>
          <button
            type="button"
            onClick={onDelete}
            disabled={busy}
            style={{ padding: '6px 10px', backgroundColor: '#fef2f2', color: '#991b1b', border: '1px solid #fca5a5', borderRadius: '6px', cursor: busy ? 'not-allowed' : 'pointer', fontSize: '13px' }}
          >
            {busy ? '...' : `Supprimer la sélection (${selectedCount})`}
          </button>
          <button
            type="button"
            onClick={onClear}
            disabled={busy}
            style={{ padding: '6px 10px', backgroundColor: '#f6f8fa', color: '#24292f', border: '1px solid #d0d7de', borderRadius: '6px', cursor: busy ? 'not-allowed' : 'pointer', fontSize: '13px' }}
          >
            Annuler la sélection
          </button>
        </>
      )}
    </div>
  );
}
