export interface QualificationReport {
  summary: string;
  // Justification rédigée AVANT le verdict (voir AnalysisReport, backend/crewquestion.py).
  reasoning?: string;
  alternative_type?: QualificationReport['request_type'] | null;
  request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
  // 0 à 1 : sous le seuil du backend, is_clear est forcé à false (questions posées).
  confidence?: number;
  is_clear: boolean;
  questions: string[];
  // Vrai si le backend n'a pas pu qualifier la demande : request_type n'est alors qu'un défaut.
  fallback?: boolean;
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
  api_calls_count: number | null;
  rate_limit_hits: number | null;
  total_wait_time_seconds: number | null;
  // Clé de l'étape CrewAI en cours (ex: 'design', 'development'...), voir WORKFLOW_STEPS ;
  // null dès que l'exécution quitte "running" (succès OU échec) — voir
  // ExecutionHistory.current_step côté backend pour le détail du pourquoi.
  current_step: string | null;
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
  // Coût/performance de cette exécution (absent pour les tours qui n'ont jamais atteint
  // run_dynamic_crew, ex: clarification demandée avant l'exécution).
  apiCallsCount?: number | null;
  rateLimitHits?: number | null;
  totalWaitTimeSeconds?: number | null;
  // Progression réelle sondée en base (voir useConversation.ts) plutôt qu'estimée par le
  // temps écoulé : absente tant qu'aucun sondage n'a encore abouti (ex: tout début d'une
  // conversation, où conversationId n'est connu qu'une fois /api/execute résolu), auquel
  // cas StepIndicator retombe sur l'estimation par temps.
  currentStep?: string | null;
  // Vrai après un clic sur "Annuler" (cancelSending, useConversation.ts) pendant que ce tour
  // est encore "running" : contrairement à status === 'cancelled', ne change PAS `status` lui-
  // même, précisément pour que hasRunningTurn/busy et le sondage de progression restent actifs
  // (l'exécution continue réellement côté serveur, et /api/execute refuserait de toute façon un
  // nouveau message tant qu'elle n'est pas terminée — voir la garde de concurrence par
  // conversation côté backend). Purement un drapeau d'AFFICHAGE local : remplacé sans action
  // particulière dès que le sondage détecte la fin réelle de l'exécution (historyEntryToTurn
  // reconstruit alors ce tour depuis zéro, sans ce champ).
  dismissedLocally?: boolean;
  // Résultats partiels des agents terminés, reçus progressivement via le sondage /progress
  // (dict {agent_name: markdown_content}). Accumulé au fur et à mesure des completions,
  // fusionné avec result final si présent.
  completedAgents?: Record<string, string>;
}
