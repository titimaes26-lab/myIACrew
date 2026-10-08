import type { ChatTurn, ExecutionHistoryEntry } from '../../types';

// Données communes aux tests de ce dossier.
export const entry = (overrides: Partial<ExecutionHistoryEntry> = {}): ExecutionHistoryEntry => ({
  id: 7, user_request: 'Ajoute un panier', workflow: 'FEATURE', clarifications: null, result: null, status: 'running',
  conversation_id: 3, repo_owner: null, repo_name: null, created_at: '2026-10-04T10:00:01Z', updated_at: '2026-10-04T10:05:00Z',
  api_calls_count: null, rate_limit_hits: null, total_wait_time_seconds: null, current_step: null, error_code: null,
  error_retryable: null, scope: null, ...overrides,
});

export const turn = (overrides: Partial<ChatTurn> = {}): ChatTurn => ({
  id: 7, userMessage: 'Ajoute un panier', status: 'running', createdAt: '2026-10-04T10:00:00Z', ...overrides,
});
