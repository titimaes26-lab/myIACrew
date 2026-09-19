import type { ExecutionHistoryEntry, QualificationReport, RepoTargetSuggestion } from './types';

export interface ExecuteResponse {
  status: string;
  id: number;
  conversation_id: number;
  workflow: string;
  result: string;
  api_calls_count?: number | null;
  rate_limit_hits?: number | null;
  total_wait_time_seconds?: number | null;
}

export class ApiError extends Error {
  conversationId?: number;
}

export interface ConversationProgress {
  id: number | null;
  status: string | null;
  current_step: string | null;
}

function authHeaders(accessToken: string): HeadersInit {
  return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${accessToken}` };
}

async function parseJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    const err = new ApiError(errData.detail || `Erreur serveur (${res.status})`);
    const conversationIdHeader = res.headers.get('X-Conversation-Id');
    if (conversationIdHeader) err.conversationId = Number(conversationIdHeader);
    throw err;
  }
  return res.json();
}

export function apiClient(apiUrl: string, accessToken: string) {
  return {
    qualify: (user_request: string, signal?: AbortSignal) =>
      fetch(`${apiUrl}/api/qualify`, {
        method: 'POST',
        headers: authHeaders(accessToken),
        body: JSON.stringify({ user_request }),
        signal,
      }).then((res) => parseJsonOrThrow<QualificationReport>(res)),

    execute: (payload: Record<string, unknown>, signal?: AbortSignal) =>
      fetch(`${apiUrl}/api/execute`, {
        method: 'POST',
        headers: authHeaders(accessToken),
        body: JSON.stringify(payload),
        signal,
      }).then((res) => parseJsonOrThrow<ExecuteResponse>(res)),

    listHistory: () =>
      fetch(`${apiUrl}/api/history`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<ExecutionHistoryEntry[]>(res)),

    getConversationMessages: (conversationId: number) =>
      fetch(`${apiUrl}/api/conversations/${conversationId}/messages`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<ExecutionHistoryEntry[]>(res)),

    getConversationProgress: (conversationId: number) =>
      fetch(`${apiUrl}/api/conversations/${conversationId}/progress`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<ConversationProgress>(res)),

    listRepoTargets: () =>
      fetch(`${apiUrl}/api/repo-targets`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<RepoTargetSuggestion[]>(res)),

    deleteHistoryEntry: (id: number) =>
      fetch(`${apiUrl}/api/history/${id}`, { method: 'DELETE', headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<{ status: string; id: number }>(res)),
  };
}
