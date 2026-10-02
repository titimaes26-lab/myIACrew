import { useCallback, useRef, useState, type ReactNode } from 'react';

export interface TooltipState {
  x: number;
  y: number;
  content: ReactNode;
}

// Position relative au conteneur du graphique (jamais à la page) : l'infobulle suit le pointeur
// à la souris, et se pose au-dessus de l'élément au focus clavier. Le contenu est un nœud React :
// les libellés (noms d'agents, dates) sont échappés, jamais injectés comme HTML.
export function useChartTooltip() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<TooltipState | null>(null);

  const place = useCallback((clientX: number, clientY: number, content: ReactNode) => {
    const box = containerRef.current?.getBoundingClientRect();
    if (!box) return;
    // Évite que l'infobulle (centrée sur x) ne déborde du conteneur à gauche ou à droite.
    const x = Math.min(Math.max(clientX - box.left, 90), Math.max(box.width - 90, 90));
    setTip({ x, y: clientY - box.top, content });
  }, []);

  const showAtPointer = useCallback(
    (event: { clientX: number; clientY: number }, content: ReactNode) => place(event.clientX, event.clientY, content),
    [place],
  );

  const showAtElement = useCallback(
    (element: HTMLElement, content: ReactNode) => {
      const rect = element.getBoundingClientRect();
      place(rect.left + rect.width / 2, rect.top + rect.height / 2, content);
    },
    [place],
  );

  const hide = useCallback(() => setTip(null), []);

  return { containerRef, tip, showAtPointer, showAtElement, hide };
}
