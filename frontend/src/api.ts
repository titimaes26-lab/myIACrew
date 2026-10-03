import type { AgentRunView, BulkDeleteResult, ExecutionHistoryEntry, MetricsSummary, MetricsWorkflowFilter, QualificationReport, RepoTargetSuggestion } from './types';

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
  // Étapes réutilisées d'une exécution en échec reprise (vide hors reprise).
  resumed_steps?: string[];
}

// code/retryable viennent du backend (voir backend/errors.py) ; NETWORK_ERROR est produit ici quand
// le serveur est injoignable (aucune réponse HTTP).
export class ApiError extends Error {
  readonly status: number | null;
  readonly code: string | null;
  readonly retryable: boolean;

  constructor(message: string, options: { status?: number | null; code?: string | null; retryable?: boolean } = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = options.status ?? null;
    this.code = options.code ?? null;
    this.retryable = options.retryable ?? false;
  }
}

// fetch qui transforme un échec réseau (TypeError « Failed to fetch ») en ApiError lisible ;
// une annulation (AbortError) est relancée telle quelle : ce n'est pas une erreur à afficher.
async function request(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (err: unknown) {
    if (err instanceof DOMException && err.name === 'AbortError') throw err;
    throw new ApiError('Impossible de joindre le serveur. Vérifiez votre connexion puis réessayez.', {
      code: 'NETWORK_ERROR',
      retryable: true,
    });
  }
}

export interface ConversationProgress {
  id: number | null;
  status: string | null;
  current_step: string | null;
  completed_agents?: Record<string, string>;
}

function authHeaders(accessToken: string): HeadersInit {
  return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${accessToken}` };
}

async function parseJsonOrThrow<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new ApiError(typeof errData.detail === 'string' && errData.detail ? errData.detail : `Erreur serveur (${res.status})`, {
      status: res.status,
      code: typeof errData.code === 'string' ? errData.code : null,
      retryable: errData.retryable === true,
    });
  }
  return res.json();
}

export function apiClient(apiUrl: string, accessToken: string) {
  return {
    // conversation_id : permet à qualification_agent de tenir compte des tours précédents
    // (ex: "corrige ça" juste après une FEATURE doit être qualifié BUGFIX, pas DESIGN_AND_DEV).
    // has_repo_target : sans repository cible, une demande de BUGFIX/FEATURE déclenche une question sur le repo.
    qualify: (user_request: string, conversation_id: number | null, has_repo_target: boolean, signal?: AbortSignal) =>
      request(`${apiUrl}/api/qualify`, {
        method: 'POST',
        headers: authHeaders(accessToken),
        body: JSON.stringify({ user_request, conversation_id: conversation_id ?? undefined, has_repo_target }),
        signal,
      }).then((res) => parseJsonOrThrow<QualificationReport>(res)),

    execute: (payload: Record<string, unknown>, signal?: AbortSignal) =>
      request(`${apiUrl}/api/execute`, {
        method: 'POST',
        headers: authHeaders(accessToken),
        body: JSON.stringify(payload),
        signal,
      }).then((res) => parseJsonOrThrow<ExecuteAcceptedResponse>(res)),

    listHistory: () =>
      request(`${apiUrl}/api/history`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<ExecutionHistoryEntry[]>(res)),

    getConversationMessages: (conversationId: number) =>
      request(`${apiUrl}/api/conversations/${conversationId}/messages`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<ExecutionHistoryEntry[]>(res)),

    getConversationProgress: (conversationId: number) =>
      request(`${apiUrl}/api/conversations/${conversationId}/progress`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<ConversationProgress>(res)),

    listRepoTargets: () =>
      request(`${apiUrl}/api/repo-targets`, { headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<RepoTargetSuggestion[]>(res)),

    getMetricsSummary: (days: number, workflow: MetricsWorkflowFilter, signal?: AbortSignal) => {
      // Décalage du navigateur à l'est d'UTC : les jours du graphique quotidien sont des jours LOCAUX.
      const tzOffset = -new Date().getTimezoneOffset();
      const query = new URLSearchParams({ days: String(days), tz_offset: String(tzOffset) });
      if (workflow !== 'ALL') query.set('workflow', workflow);
      return request(`${apiUrl}/api/metrics/summary?${query}`, { headers: authHeaders(accessToken), signal })
        .then((res) => parseJsonOrThrow<MetricsSummary>(res));
    },

    getExecutionAgentRuns: (executionId: number, signal?: AbortSignal) =>
      request(`${apiUrl}/api/executions/${executionId}/agent-runs`, { headers: authHeaders(accessToken), signal })
        .then((res) => parseJsonOrThrow<AgentRunView[]>(res)),

    deleteHistoryEntry: (id: number) =>
      request(`${apiUrl}/api/history/${id}`, { method: 'DELETE', headers: authHeaders(accessToken) })
        .then((res) => parseJsonOrThrow<{ status: string; id: number }>(res)),

    deleteHistoryEntries: (ids: number[]) =>
      request(`${apiUrl}/api/history/bulk-delete`, {
        method: 'POST',
        headers: authHeaders(accessToken),
        body: JSON.stringify({ ids }),
      }).then((res) => parseJsonOrThrow<BulkDeleteResult>(res)),
  };
}
