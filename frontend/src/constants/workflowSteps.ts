export interface WorkflowStep {
  key: string;
  label: string;
  icon: string;
}

const DESIGN_STEP: WorkflowStep = { key: 'design', label: 'Conception (specs produit / jeu)', icon: '🎨' };
const ARCHITECTURE_STEP: WorkflowStep = { key: 'architecture', label: 'Architecture technique', icon: '🏗️' };
// Étape séparée de DEVELOPMENT_STEP (voir backend/crewquestion.py : diagnostic_task, lecture
// seule, produit le code complet ; development_task, écriture seule, le committe tel quel) —
// deux tâches CrewAI avec des budgets d'itérations distincts, pour qu'un diagnostic long ne
// puisse jamais épuiser le budget réservé au commit.
const DIAGNOSTIC_STEP: WorkflowStep = { key: 'diagnostic', label: 'Diagnostic & rédaction du code', icon: '🔎' };
// Label volontairement neutre (pas "Commit GitHub") : cette étape écrit aussi sur disque local,
// sans aucune interaction GitHub, quand aucun repository cible n'est configuré (voir
// development_task dans backend/tasksquestion.yaml, branche "Sans repository cible").
const DEVELOPMENT_STEP: WorkflowStep = { key: 'development', label: 'Écriture du code', icon: '💻' };
const QA_STEP: WorkflowStep = { key: 'qa', label: 'Revue QA', icon: '🔍' };

export const WORKFLOW_STEPS: Record<string, WorkflowStep[]> = {
  ANALYSE_ONLY: [DESIGN_STEP, ARCHITECTURE_STEP],
  BUGFIX: [DIAGNOSTIC_STEP, DEVELOPMENT_STEP, QA_STEP],
  FEATURE: [ARCHITECTURE_STEP, DIAGNOSTIC_STEP, DEVELOPMENT_STEP, QA_STEP],
  DESIGN_AND_DEV: [DESIGN_STEP, ARCHITECTURE_STEP, DIAGNOSTIC_STEP, DEVELOPMENT_STEP, QA_STEP],
};

export const DEFAULT_STEPS = WORKFLOW_STEPS.DESIGN_AND_DEV;

// Étapes réellement exécutées : une petite FEATURE (scope PETIT) saute l'architecture, comme côté serveur
// (backend/crewquestion.py, workflow_step_keys).
export function workflowSteps(workflow?: string | null, scope?: string | null): WorkflowStep[] {
  const steps = (workflow && WORKFLOW_STEPS[workflow]) || DEFAULT_STEPS;
  return workflow === 'FEATURE' && scope === 'PETIT' ? steps.filter((step) => step.key !== 'architecture') : steps;
}

// « Conception (specs produit / jeu) » -> « Conception » : le détail reste dans l'infobulle.
export const stepShortLabel = (label: string): string => label.split(' (')[0];

// Libellé court d'une étape par sa clé, quel que soit le workflow (les libellés sont communs).
export function stepShortLabelForKey(key: string): string {
  const step = WORKFLOW_STEPS.DESIGN_AND_DEV.find((candidate) => candidate.key === key);
  return step ? stepShortLabel(step.label) : key;
}
