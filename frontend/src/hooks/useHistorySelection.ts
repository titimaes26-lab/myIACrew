import { useCallback, useState } from 'react';

/** Identifiants de la plage [a, b] (bornes incluses, dans un sens ou l'autre) dans l'ordre affiché. */
export function rangeBetween(orderedIds: number[], a: number, b: number): number[] {
  const from = orderedIds.indexOf(a);
  const to = orderedIds.indexOf(b);
  if (from === -1 || to === -1) return [b].filter((id) => orderedIds.includes(id));
  return orderedIds.slice(Math.min(from, to), Math.max(from, to) + 1);
}

/**
 * Sélection multiple de l'historique. `selectableIds` = lignes affichées ET supprimables
 * (hors exécution en cours), dans l'ordre d'affichage : seules elles peuvent être cochées.
 */
export function useHistorySelection(selectableIds: number[]) {
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);

  // Une ligne disparue ou devenue « en cours » ne reste jamais cochée.
  const effective = new Set([...selected].filter((id) => selectableIds.includes(id)));

  const toggle = useCallback((id: number, shiftKey: boolean) => {
    if (!selectableIds.includes(id)) return;
    setSelected((current) => {
      const next = new Set([...current].filter((value) => selectableIds.includes(value)));
      if (shiftKey && anchor !== null && anchor !== id) {
        // Maj+clic : la plage prend l'état de la case cliquée (cochée → tout cocher).
        const check = !next.has(id);
        for (const rangeId of rangeBetween(selectableIds, anchor, id)) {
          if (check) next.add(rangeId);
          else next.delete(rangeId);
        }
      } else if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
    setAnchor(id);
  }, [selectableIds, anchor]);

  const selectAll = useCallback(() => setSelected(new Set(selectableIds)), [selectableIds]);
  const clear = useCallback(() => {
    setSelected(new Set());
    setAnchor(null);
  }, []);

  return { selected: effective, toggle, selectAll, clear };
}
