import { useEffect, useState } from 'react';
import { WORKFLOW_STEPS, DEFAULT_STEPS } from '../constants/workflowSteps';
import { formatSeconds } from '../utils/formatDuration';
import { toServerDate } from '../utils/serverDate';

const STEP_ADVANCE_MS = 9000;
const TICK_MS = 1000;

function elapsedSecondsSince(since: string): number {
  return (Date.now() - toServerDate(since).getTime()) / 1000;
}

interface StepIndicatorProps {
  workflow?: string;
  // Timestamp de départ du tour (turn.createdAt), utilisé plutôt que l'instant de montage
  // du composant : celui-ci peut être remonté (changement de `key`) une fois le workflow
  // connu, ce qui décalerait un chrono basé sur le montage.
  since: string;
  // Étape réellement en cours côté serveur (persistée en base, sondée par useConversation.ts),
  // absente tant qu'aucun sondage n'a encore abouti — voir le repli sur l'estimation par temps
  // plus bas, seul mode disponible pour le tout premier message d'une conversation (son id
  // n'est connu qu'une fois /api/execute résolu, donc rien à sonder avant cela).
  currentStepKey?: string | null;
}

export default function StepIndicator({ workflow, since, currentStepKey }: StepIndicatorProps) {
  const steps = (workflow && WORKFLOW_STEPS[workflow]) || DEFAULT_STEPS;
  // Signal réel distinct des vraies étapes du workflow (jamais une clé de WORKFLOW_STEPS, voir
  // constants/workflowSteps.ts) : persisté côté backend (_execute_crew_and_persist, main.py) tant
  // que cette exécution attend son tour derrière _execution_semaphore (au plus 2 exécutions de
  // crew en vol simultanément, toutes conversations confondues — voir sa définition). Sans ce
  // signal dédié, l'estimation par temps ci-dessous ferait défiler puis "terminer" toutes les
  // étapes en quelques dizaines de secondes alors qu'aucune n'a même commencé, l'exécution étant
  // encore purement en attente d'un emplacement.
  const isQueued = currentStepKey === 'queued';
  const realIndex = currentStepKey && !isQueued ? steps.findIndex((step) => step.key === currentStepKey) : -1;
  const hasRealProgress = realIndex >= 0;

  const [estimatedIndex, setEstimatedIndex] = useState(0);
  const [elapsed, setElapsed] = useState(() => elapsedSecondsSince(since));
  // Distingue "jamais eu de signal réel pour ce tour" (retombe sur l'estimation par temps,
  // seul mode possible tant que /api/execute n'a pas résolu pour le tout premier message d'une
  // conversation) de "en avait un, mais plus maintenant" (le backend efface current_step
  // pendant une pause avant une nouvelle tentative sur limite de quota — voir crewquestion.py) :
  // le second cas ne doit PAS retomber sur l'estimation par temps, qui laisserait croire à tort
  // à une progression "normale" alors que l'exécution est en réalité à l'arrêt, en pause.
  const [hadRealProgress, setHadRealProgress] = useState(hasRealProgress);
  if (hasRealProgress && !hadRealProgress) setHadRealProgress(true);
  const isPausedForRetry = !hasRealProgress && hadRealProgress;

  useEffect(() => {
    const tickTimer = setInterval(() => setElapsed(elapsedSecondsSince(since)), TICK_MS);
    // L'avancement PAR TEMPS ne tourne que tant qu'aucun signal réel n'est JAMAIS arrivé pour ce
    // tour : une fois hasRealProgress vrai, le laisser tourner ferait dépasser l'affichage
    // au-delà de l'étape réellement en cours dès que le serveur met plus de STEP_ADVANCE_MS à
    // passer à la suivante (les étapes CrewAI durent typiquement plusieurs dizaines de
    // secondes). Et une fois hadRealProgress vrai, cet avancement ne doit plus JAMAIS reprendre
    // (même pendant isPausedForRetry) : afficher une progression par temps après en avoir eu une
    // réelle laisserait croire à tort que l'exécution continue normalement pendant une pause.
    // isQueued (voir sa définition) : ce signal réel n'est justement PAS une progression, donc ne
    // doit pas non plus déclencher l'avancement par temps — sans quoi une simple attente de
    // quelques dizaines de secondes derrière _execution_semaphore suffirait à afficher toutes les
    // étapes comme "terminées" avant même que le crew n'ait été instancié.
    if (hasRealProgress || hadRealProgress || isQueued) return () => clearInterval(tickTimer);
    const stepTimer = setInterval(() => {
      setEstimatedIndex((i) => Math.min(i + 1, steps.length - 1));
    }, STEP_ADVANCE_MS);
    return () => {
      clearInterval(stepTimer);
      clearInterval(tickTimer);
    };
  }, [steps.length, since, hasRealProgress, hadRealProgress, isQueued]);

  // Pendant une pause, retryDelay recommence réellement à la toute première étape (voir
  // crewquestion.py : selected_tasks est entièrement reconstruit à chaque nouvelle tentative) —
  // afficher 0 ici est donc FIDÈLE à ce qui va se passer, pas une régression à masquer.
  // isQueued : -1 (aucune étape "courante"), pour que toutes s'affichent comme pas encore
  // commencées (⏳) — fidèle elle aussi, puisque le crew n'a justement pas encore été instancié.
  const activeIndex = hasRealProgress ? realIndex : isPausedForRetry ? 0 : isQueued ? -1 : estimatedIndex;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '4px 0' }}>
      {/* role="status"/aria-live seulement ici, PAS sur le conteneur entier : le chrono ci-
          dessous change chaque seconde (TICK_MS), et un lecteur d'écran annoncerait sinon ce
          texte à chaque tick pendant toute la durée d'une exécution (plusieurs minutes). Ce
          bloc-ci ne change, lui, qu'à chaque transition d'étape réelle. */}
      <div role="status" aria-live="polite" style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {steps.map((step, i) => {
          const done = i < activeIndex;
          const current = i === activeIndex;
          return (
            <div
              key={step.key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '13px',
                color: done ? '#16a34a' : current ? '#0070f3' : '#999',
              }}
            >
              <span aria-hidden="true">{done ? '✅' : current ? step.icon : '⏳'}</span>
              <span style={{ fontWeight: current ? 600 : 400 }}>
                {step.label}
                {current ? '…' : ''}
                {done ? ' (terminé)' : ''}
              </span>
            </div>
          );
        })}
      </div>
      <p style={{ fontSize: '11px', color: '#999', margin: '4px 0 0 0', fontStyle: 'italic' }}>
        {hasRealProgress
          ? `En cours depuis ${formatSeconds(elapsed)} — progression suivie en direct.`
          : isPausedForRetry
            // Générique plutôt que "après une limite de quota atteinte" : ce message peut
            // aussi, brièvement, correspondre à un échec définitif (non lié au quota) qui n'a
            // pas encore fini d'être enregistré côté serveur — pas seulement à une pause avant
            // une nouvelle tentative sur limite de quota (voir crewquestion.py, qui efface
            // current_step sur TOUTE exception, retentée ou non).
            ? "En pause — nouvelle tentative éventuelle en cours. Si l'exécution reprend, ce sera depuis la toute première étape."
            : isQueued
              ? "En file d'attente — d'autres exécutions occupent déjà ce service. Celle-ci démarrera automatiquement dès qu'un emplacement se libère."
              : `En cours depuis ${formatSeconds(elapsed)} — progression estimée, l'étape réellement en cours côté serveur peut différer.`}
      </p>
    </div>
  );
}
