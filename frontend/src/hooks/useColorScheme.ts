import { useSyncExternalStore } from 'react';

// Une seule MediaQueryList pour toute l'application (créée une fois), partagée par chaque abonné :
// un résultat peut afficher plusieurs blocs Markdown, chacun lit ce hook.
const media: MediaQueryList | null =
  typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? window.matchMedia('(prefers-color-scheme: dark)')
    : null;

function subscribe(callback: () => void): () => void {
  media?.addEventListener('change', callback);
  return () => media?.removeEventListener('change', callback);
}

// Thème de l'OS (clair/sombre), suivi en direct : sert aux bibliothèques qui ne lisent pas nos variables CSS
// (coloration syntaxique). Le reste de l'interface réagit déjà seul via tokens.css.
export function useColorScheme(): 'light' | 'dark' {
  return useSyncExternalStore(subscribe, () => (media?.matches ? 'dark' : 'light'), () => 'light');
}
