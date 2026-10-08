export interface StepStat {
  // Durée médiane de l'étape sur la période observée, en secondes, et nombre d'exécutions qui la fondent.
  p50: number;
  runs: number;
}

export type StepStats = Record<string, StepStat>;

// En dessous, une médiane ne dit rien : mieux vaut ne pas estimer que d'afficher un chiffre trompeur.
export const MIN_RUNS_FOR_ESTIMATE = 3;

// Dernière « étape » du pipeline côté mesures : synthèse du résultat et mise en forme, après la dernière tâche.
const FINALIZATION_KEY = 'system';

export interface RemainingEstimate {
  remainingSeconds: number;
  // Vrai quand l'étape en cours dure déjà plus que sa médiane : son reste est alors compté à 0.
  overrun: boolean;
  currentMedian: number;
}

function usable(stat: StepStat | undefined): stat is StepStat {
  return stat !== undefined && Number.isFinite(stat.p50) && stat.p50 > 0 && stat.runs >= MIN_RUNS_FOR_ESTIMATE;
}

// Temps restant estimé d'une exécution en cours : reste de l'étape courante (médiane − déjà écoulé, jamais négatif)
// + médiane de chaque étape suivante non reprise + finalisation si elle est mesurée. null dès qu'une médiane
// nécessaire manque ou repose sur trop peu d'exécutions : pas d'estimation inventée. Les pauses de quota ne sont
// pas prévisibles et ne sont pas comptées.
export function estimateRemaining(params: {
  stepKeys: string[];
  currentKey: string;
  reused?: string[];
  stats: StepStats;
  currentElapsed: number;
}): RemainingEstimate | null {
  const { stepKeys, currentKey, stats, currentElapsed } = params;
  const index = stepKeys.indexOf(currentKey);
  if (index < 0 || !Number.isFinite(currentElapsed) || currentElapsed < 0) return null;
  const current = stats[currentKey];
  if (!usable(current)) return null;

  const reused = new Set(params.reused ?? []);
  let later = 0;
  for (const key of stepKeys.slice(index + 1)) {
    if (reused.has(key)) continue;
    const stat = stats[key];
    if (!usable(stat)) return null;
    later += stat.p50;
  }
  const finalization = stats[FINALIZATION_KEY];
  const tail = usable(finalization) ? finalization.p50 : 0;

  const overrun = currentElapsed > current.p50;
  return {
    remainingSeconds: Math.max(0, current.p50 - currentElapsed) + later + tail,
    overrun,
    currentMedian: current.p50,
  };
}

// Arrondi à la dizaine de secondes : une estimation n'a pas la précision de la seconde.
export function roundEstimate(seconds: number): number {
  return Math.max(10, Math.round(seconds / 10) * 10);
}
