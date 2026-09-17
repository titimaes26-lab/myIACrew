export interface QualificationReport {
  summary: string;
  is_clear: boolean;
  request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
  questions: string[];
}

export interface ExecutionHistoryEntry {
  id: number;
  user_request: string;
  workflow: string;
  clarifications: string | null;
  result: string | null;
  status: 'running' | 'success' | 'failed';
  conversation_id: number | null;
  repo_owner: string | null;
  repo_name: string | null;
  created_at: string;
  updated_at: string;
}

export interface RepoTarget {
  owner: string;
  name: string;
  branch: string;
}

export interface RepoTargetSuggestion {
  repo_owner: string;
  repo_name: string;
  base_branch: string | null;
}

export interface ChatTurn {
  id: number | string;
  userMessage: string;
  // 'cancelled' est un état frontend uniquement : annuler n'interrompt que l'attente
  // côté navigateur, pas forcément l'exécution côté serveur (voir useConversation.ts).
  status: 'clarifying' | 'running' | 'success' | 'failed' | 'cancelled';
  workflow?: string;
  agentSummary?: string;
  questions?: string[];
  result?: string | null;
  createdAt: string;
  // Renseigné dès que le tour quitte l'état "running" (succès, échec ou clarification
  // demandée), pour pouvoir afficher la durée écoulée depuis createdAt.
  updatedAt?: string;
}
