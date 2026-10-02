import { useEffect, useState, type RefObject } from 'react';

// Largeur courante d'un élément (suit les redimensionnements) : sert à décider combien d'étiquettes
// d'axe tiennent sans se chevaucher. 0 tant que la première mesure n'est pas faite.
export function useElementWidth(ref: RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.round(entry.contentRect.width)));
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);

  return width;
}
