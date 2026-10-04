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
  // Taille d'une FEATURE : PETIT (ajustement local) saute l'étape d'architecture ; absent = parcours complet.
  scope?: 'PETIT' | 'GRAND';
}

// Ce que la qualification propose de lancer, que l'utilisateur confirme ou corrige avant l'exécution.
export interface LaunchPreview {
  workflow: QualificationReport['request_type'];
  scope?: 'PETIT' | 'GRAND';
  // « propriétaire/repo · branche » ; null sans repository cible (espace de travail local).
  repoLabel: string | null;
}

export interface LaunchChoice {
  workflow: QualificationReport['request_type'];
  scope?: 'PETIT' | 'GRAND';
}

// Actions de l'aperçu avant lancement, transmises du hook jusqu'à chaque message (voir LaunchPreview).
export interface LaunchControls {
  onLaunch: (choice: LaunchChoice) => void;
  onCancel: () => void;
  alwaysConfirm: boolean;
  onAlwaysConfirmChange: (value: boolean) => void;
  disabled: boolean;
}

export interface BulkDeleteResult {
  deleted: number[];
  skipped: { id: number; reason: 'running' | 'not_found' }[];
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
  // Cause d'un échec (voir backend/errors.py) ; null hors statut 'failed' ou pour les anciennes lignes.
  error_code: string | null;
  error_retryable: boolean | null;
  // Taille de qualification d'une FEATURE (PETIT : étape d'architecture sautée) ; null pour tout autre workflow.
  scope?: string | null;
}

// Une ligne de la liste de l'historique : l'exécution SANS son résultat ni ses précisions (texte volumineux, jamais
// envoyé par GET /api/history). Le résultat s'obtient avec la conversation ou par GET /api/executions/{id}.
export type HistoryListEntry = Omit<ExecutionHistoryEntry, 'result' | 'clarifications'>;

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
  errorCode?: string | null;
  errorRetryable?: boolean | null;
  // PETIT : l'étape d'architecture est sautée (voir workflowSteps).
  scope?: string | null;
  // Aperçu avant lancement (voir LaunchPreview) : présent tant que la qualification attend la confirmation de
  // l'utilisateur ; le tour est alors 'clarifying' (jamais 'running').
  launchPreview?: LaunchPreview;
  // Étapes déjà réussies d'une exécution précédente, réutilisées par cette reprise (voir « Relancer »).
  resumedSteps?: string[];
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
  // Nombre d'exécutions devant ce tour tant qu'il attend son créneau (étape « queued »), sondé avec la progression.
  queueAhead?: number | null;
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

// --- Performance par agent (GET /api/metrics/summary, GET /api/executions/{id}/agent-runs) ---

export type MetricsWorkflowFilter = 'ALL' | 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';

export interface AgentMetricsRow {
  agent: string;
  label: string;
  runs: number;
  incomplete: number;
  duration_p50: number | null;
  duration_p95: number | null;
  avg_llm_calls: number | null;
  llm_errors: number;
  // Exécutions dont le fournisseur a renvoyé l'usage de tokens : 0 => tokens inconnus (pas « 0 token »).
  token_runs: number;
  avg_prompt_tokens: number | null;
  avg_completion_tokens: number | null;
  avg_tool_calls: number | null;
  tool_errors: number;
}

export interface DailyMetricsRow {
  date: string;
  executions: number;
  failed: number;
  llm_calls: number;
  tokens: number;
  // Exécutions du jour qui portent un verdict QA, et parmi elles celles en « GO » (qualité dans le temps).
  qa_total: number;
  qa_go: number;
  // Médiane des durées d'exécution du jour ; null quand aucune durée n'est mesurable ce jour-là.
  median_duration_seconds: number | null;
}

export type QaVerdict = 'GO' | 'GO_AVEC_RESERVES' | 'NO_GO';

export type ExecutionSortKey = 'created_at' | 'duration' | 'llm_calls' | 'tokens';

export interface ExecutionRow {
  id: number;
  conversation_id: number | null;
  user_request: string;
  workflow: string;
  status: 'success' | 'failed';
  created_at: string;
  duration_seconds: number | null;
  llm_calls: number | null;
  tokens: number | null;
  error_code: string | null;
  qa_verdict: QaVerdict | null;
  attempts: number;
  reused_steps: number;
  repo: string | null;
}

export interface ExecutionsPage {
  total: number;
  items: ExecutionRow[];
}

export interface FailureCauseRow {
  code: string;
  label: string;
  count: number;
}

export interface MetricsSummary {
  period_days: number;
  workflow: string | null;
  executions: {
    total: number;
    success: number;
    failed: number;
    median_duration_seconds: number | null;
    avg_llm_calls: number | null;
    avg_tokens: number | null;
    token_executions: number;
    rate_limit_hits: number;
    wait_seconds: number | null;
    // Raisonnement : exécutions relancées automatiquement / reprises d'étapes déjà réussies.
    auto_retried: number;
    resumed: number;
    // Qualité : verdicts QA des résultats.
    qa_verdicts: Record<QaVerdict, number>;
    // Coût estimé (devise `currency`) ; null sans tarif configuré ou sans tokens connus.
    total_cost: number | null;
    avg_cost: number | null;
  };
  agents: AgentMetricsRow[];
  failures: FailureCauseRow[];
  currency?: string | null;
  // Vrai quand la limite d'exécutions analysées est atteinte (la période n'est alors pas couverte en entier).
  truncated?: boolean;
  // Mêmes statistiques d'exécutions sur la période PRÉCÉDENTE de même durée ; null sans donnée comparable.
  previous?: MetricsSummary['executions'] | null;
  // Vrai quand la comparaison est abandonnée à cause de la limite alors que la période courante est complète.
  comparison_limited?: boolean;
  daily: DailyMetricsRow[];
}

export interface AgentRunView {
  agent: string;
  label: string;
  status: 'completed' | 'incomplete' | 'n/a';
  duration_seconds: number | null;
  llm_calls: number;
  llm_errors: number;
  tokens_known: boolean;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  tool_calls: number;
  tool_errors: number;
}
