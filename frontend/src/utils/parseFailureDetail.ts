export interface FailureDetail {
  stepIndex: number;
  totalSteps: number;
  agentRole: string;
  message: string;
}

// Reconnaît le format produit par main.py::execute_workflow pour un CrewStepError
// (backend/crewquestion.py) : "Échec à l'étape N/M (Rôle) : message d'origine".
// Les échecs antérieurs à cette fonctionnalité, ou survenant hors de run_dynamic_crew,
// ne matchent pas et sont affichés tels quels par l'appelant.
const FAILURE_PATTERN = /^Échec à l'étape (\d+)\/(\d+) \(([^)]+)\)\s*:\s*([\s\S]*)$/;

export function parseFailureDetail(raw: string): FailureDetail | null {
  const match = raw.match(FAILURE_PATTERN);
  if (!match) return null;

  return {
    stepIndex: Number(match[1]),
    totalSteps: Number(match[2]),
    agentRole: match[3].trim(),
    message: match[4].trim(),
  };
}
