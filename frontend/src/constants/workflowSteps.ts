export interface WorkflowStep {
  key: string;
  label: string;
  icon: string;
}

const DESIGN_STEP: WorkflowStep = { key: 'design', label: 'Conception (specs produit / jeu)', icon: '🎨' };
const ARCHITECTURE_STEP: WorkflowStep = { key: 'architecture', label: 'Architecture technique', icon: '🏗️' };
const DEVELOPMENT_STEP: WorkflowStep = { key: 'development', label: 'Développement', icon: '💻' };
const QA_STEP: WorkflowStep = { key: 'qa', label: 'Revue QA', icon: '🔍' };

export const WORKFLOW_STEPS: Record<string, WorkflowStep[]> = {
  ANALYSE_ONLY: [DESIGN_STEP, ARCHITECTURE_STEP],
  BUGFIX: [DEVELOPMENT_STEP, QA_STEP],
  FEATURE: [ARCHITECTURE_STEP, DEVELOPMENT_STEP, QA_STEP],
  DESIGN_AND_DEV: [DESIGN_STEP, ARCHITECTURE_STEP, DEVELOPMENT_STEP, QA_STEP],
};

export const DEFAULT_STEPS = WORKFLOW_STEPS.DESIGN_AND_DEV;
