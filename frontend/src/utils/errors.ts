import { ApiError } from '../api';

export interface DisplayedError {
  message: string;
  code: string | null;
  retryable: boolean;
}

// Normalise n'importe quelle valeur interceptée par un catch en erreur affichable.
export function toDisplayedError(err: unknown, fallback: string): DisplayedError {
  if (err instanceof ApiError) {
    return { message: err.message || fallback, code: err.code, retryable: err.retryable };
  }
  return { message: err instanceof Error && err.message ? err.message : fallback, code: null, retryable: false };
}

// Conseil affiché sous un échec d'exécution selon son code (voir backend/errors.py).
export function failureHint(code: string | null | undefined): string | null {
  switch (code) {
    case 'QUOTA_EXHAUSTED':
    case 'LLM_UNAVAILABLE':
    case 'LLM_TIMEOUT':
    case 'GITHUB_UNAVAILABLE':
      return 'Panne temporaire : « Réessayer » relance la demande telle quelle, idéalement dans quelques minutes.';
    case 'GUARDRAIL_FAILED':
      return 'Le résultat n\'a pas passé les contrôles de qualité : reformulez ou précisez la demande avant de réessayer.';
    default:
      return null;
  }
}
