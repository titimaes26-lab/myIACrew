import type { ChatTurn, ExecutionHistoryEntry } from '../../types';

export const CONFIRM_LAUNCH_KEY = 'studio.confirmLaunch';

// Préférence « confirmer avant de lancer » : activée par défaut ; localStorage peut être indisponible (navigation
// privée, stockage bloqué), l'interface fonctionne alors avec la valeur par défaut.
export function readConfirmLaunch(): boolean {
  try {
    return window.localStorage.getItem(CONFIRM_LAUNCH_KEY) !== 'off';
  } catch {
    return true;
  }
}

export interface PendingLaunch {
  tempId: string;
  request: string;
  // Corps commun de /api/execute (conversation, repository, reprise) fixé au moment de l'envoi.
  executePayload: Record<string, unknown>;
}

export function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

export function historyEntryToTurn(entry: ExecutionHistoryEntry): ChatTurn {
  return {
    id: entry.id,
    userMessage: entry.user_request,
    status: entry.status,
    workflow: entry.workflow,
    result: entry.result,
    createdAt: entry.created_at,
    updatedAt: entry.status !== 'running' ? entry.updated_at : undefined,
    apiCallsCount: entry.api_calls_count,
    rateLimitHits: entry.rate_limit_hits,
    totalWaitTimeSeconds: entry.total_wait_time_seconds,
    currentStep: entry.current_step,
    errorCode: entry.error_code,
    errorRetryable: entry.error_retryable,
    scope: entry.scope,
  };
}
