import { useEffect, useState } from 'react';
import { ESTIMATED_STEP_DURATION_MS, WORKFLOW_STEPS } from '../constants/workflow';

// Avance l'indicateur d'étape à un rythme estimé pendant l'exécution des agents.
// Reste bloqué sur la dernière étape tant que la réponse finale n'est pas arrivée.
export function useExecutionSteps(loadingExec: boolean, workflow: string) {
  const steps = WORKFLOW_STEPS[workflow] ?? [];
  const [currentStep, setCurrentStep] = useState(0);

  useEffect(() => {
    if (!loadingExec || steps.length === 0) return;

    const interval = setInterval(() => {
      setCurrentStep((step) => Math.min(step + 1, steps.length - 1));
    }, ESTIMATED_STEP_DURATION_MS);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingExec]);

  return { steps, currentStep, setCurrentStep };
}
