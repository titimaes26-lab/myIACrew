// Séquence d'agents par workflow, alignée sur run_dynamic_crew (backend/crewquestion.py).
// Le backend ne fait pas de streaming de progression : cette séquence sert à afficher une
// estimation de l'étape en cours pendant l'attente de la réponse finale de /api/execute.
export const WORKFLOW_STEPS: Record<string, string[]> = {
  ANALYSE_ONLY: ['Game Designer — Spécifications', 'Architecte — Structure technique'],
  BUGFIX: ['Développeur — Implémentation', 'QA — Revue qualité'],
  FEATURE: ['Architecte — Structure technique', 'Développeur — Implémentation', 'QA — Revue qualité'],
  DESIGN_AND_DEV: ['Game Designer — Spécifications', 'Architecte — Structure technique', 'Développeur — Implémentation', 'QA — Revue qualité'],
};

export const ESTIMATED_STEP_DURATION_MS = 25000;
