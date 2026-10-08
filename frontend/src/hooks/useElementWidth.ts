import { useEffect, useState } from 'react';

// Largeur courante d'un élément (suit les redimensionnements) : sert à décider combien d'étiquettes
// d'axe tiennent sans se chevaucher. Référence-CALLBACK (et non objet ref) : l'élément peut être
// démonté puis remonté (bascule graphique/tableau) ; chaque nouvel élément est ré-observé.
// `ref` est stable (c'est un setState) : il peut être fusionné avec d'autres références.
export function useElementWidth() {
  const [element, setElement] = useState<HTMLElement | null>(null);
  const [width, setWidth] = useState(0);

  useEffect(() => {
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);

  return { width, ref: setElement };
}
