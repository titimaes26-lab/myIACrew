import { useSyncExternalStore } from 'react';

const QUERY = '(prefers-color-scheme: dark)';

function subscribe(callback: () => void): () => void {
  const media = window.matchMedia(QUERY);
  media.addEventListener('change', callback);
  return () => media.removeEventListener('change', callback);
}

// Thème de l'OS (clair/sombre), suivi en direct : sert aux bibliothèques qui ne lisent pas nos variables CSS
// (coloration syntaxique). Le reste de l'interface réagit déjà seul via tokens.css.
export function useColorScheme(): 'light' | 'dark' {
  return useSyncExternalStore(
    subscribe,
    () => (window.matchMedia(QUERY).matches ? 'dark' : 'light'),
    () => 'light',
  );
}
