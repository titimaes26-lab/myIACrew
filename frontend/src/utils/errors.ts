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
      return 'Panne temporaire : « Relancer » relance la demande telle quelle, idéalement dans quelques minutes.';
    case 'INTERRUPTED':
      return 'Le serveur a été interrompu pendant l\'exécution : « Relancer » relance la demande et reprend le travail déjà poussé sur la branche.';
    case 'GUARDRAIL_FAILED':
      return 'Le résultat n\'a pas passé les contrôles de qualité : reformulez ou précisez la demande avant de réessayer.';
    default:
      return null;
  }
}

// Vrai pour une panne de connexion ou de serveur (réseau coupé, 5xx, délai, trop de requêtes), faux
// pour une erreur permanente côté client (401, 404...) qu'un nouvel essai ne réglera pas.
export function isConnectionFailure(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false;
  if (err.code === 'NETWORK_ERROR') return true;
  return err.status !== null && (err.status >= 500 || err.status === 408 || err.status === 429);
}
