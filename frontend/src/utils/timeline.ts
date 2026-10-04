import type { AgentRunView } from '../types';

export interface TimelineSegment {
  key: string;
  label: string;
  // Décalage et durée en secondes depuis le début de l'exécution.
  start: number;
  duration: number;
  kind: 'step' | 'wait';
  incomplete: boolean;
}

export interface Timeline {
  segments: TimelineSegment[];
  total: number;
}

// Sous ce seuil, l'écart entre la durée de l'exécution et la somme des étapes n'est que du bruit de mesure.
const MIN_WAIT_SECONDS = 1;

export const WAIT_LABEL = 'Attente et finalisation';

// Chronologie d'UNE exécution. Les étapes tournent l'une après l'autre : l'étape suivante commence quand la
// précédente finit (ordre du pipeline). Seules les durées par étape sont mesurées, pas leurs instants de début :
// tout le temps qui n'appartient à aucune étape (file d'attente, pauses de quota, relance, résumé final) est
// regroupé dans une dernière barre « Attente et finalisation », jamais réparti au hasard entre les étapes.
export function buildTimeline(runs: AgentRunView[], totalSeconds: number | null): Timeline {
  const segments: TimelineSegment[] = [];
  let cursor = 0;
  for (const run of runs) {
    const duration = Math.max(0, run.duration_seconds ?? 0);
    segments.push({
      key: run.agent, label: run.label, start: cursor, duration, kind: 'step',
      incomplete: run.status === 'incomplete' || run.duration_seconds == null,
    });
    cursor += duration;
  }
  const waiting = totalSeconds == null ? 0 : totalSeconds - cursor;
  if (waiting >= MIN_WAIT_SECONDS) {
    segments.push({ key: 'wait', label: WAIT_LABEL, start: cursor, duration: waiting, kind: 'wait', incomplete: false });
    cursor += waiting;
  }
  return { segments, total: cursor };
}
