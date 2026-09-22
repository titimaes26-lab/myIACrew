import type { ExecutionHistoryEntry, QualificationReport, RepoTargetSuggestion } from './types';

// /api/execute répond désormais IMMÉDIATEMENT (l'exécution réelle du crew tourne en tâche de
// fond côté backend, voir _execute_crew_and_persist dans main.py) : ce corps de réponse ne
// contient donc plus jamais le résultat final, seulement de quoi rattacher ce tour à son id réel
// en base. Le résultat proprement dit n'arrive que via le sondage de progression déjà existant
// (useConversation.ts), qui détecte la fin de l'exécution en base indépendamment de cette
// requête d'origine — nécessaire pour que le résultat reste consultable même si la connexion à
// cette requête est coupée entretemps (écran verrouillé, onglet fermé).
export interface ExecuteAcceptedResponse {
  status: string;
  id: number;
  conversation_id: number;
}

export class ApiError extends Error {}

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
    throw new ApiError(errData.detail || `Erreur serveur (${res.status})`);
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
      }).then((res) => parseJsonOrThrow<ExecuteAcceptedResponse>(res)),

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
