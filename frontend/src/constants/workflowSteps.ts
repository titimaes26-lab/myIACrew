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
const DEVELOPMENT_STEP: WorkflowStep = { key: 'development', label: 'Commit GitHub', icon: '💻' };
const QA_STEP: WorkflowStep = { key: 'qa', label: 'Revue QA', icon: '🔍' };

export const WORKFLOW_STEPS: Record<string, WorkflowStep[]> = {
  ANALYSE_ONLY: [DESIGN_STEP, ARCHITECTURE_STEP],
  BUGFIX: [DIAGNOSTIC_STEP, DEVELOPMENT_STEP, QA_STEP],
  FEATURE: [ARCHITECTURE_STEP, DIAGNOSTIC_STEP, DEVELOPMENT_STEP, QA_STEP],
  DESIGN_AND_DEV: [DESIGN_STEP, ARCHITECTURE_STEP, DIAGNOSTIC_STEP, DEVELOPMENT_STEP, QA_STEP],
};

export const DEFAULT_STEPS = WORKFLOW_STEPS.DESIGN_AND_DEV;
