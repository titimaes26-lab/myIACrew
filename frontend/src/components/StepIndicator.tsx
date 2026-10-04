import { useEffect, useState } from 'react';
import { workflowSteps, stepShortLabel } from '../constants/workflowSteps';
import { formatSeconds } from '../utils/formatDuration';
import { estimateRemaining, roundEstimate } from '../utils/estimateRemaining';
import { useStepStats } from '../hooks/useStepStats';
import { toServerDate } from '../utils/serverDate';

const STEP_ADVANCE_MS = 9000;
const TICK_MS = 1000;

function elapsedSecondsSince(since: string): number {
  return (Date.now() - toServerDate(since).getTime()) / 1000;
}

interface StepIndicatorProps {
  workflow?: string;
  // PETIT : petite FEATURE, étape d'architecture sautée (voir workflowSteps).
  scope?: string | null;
  // Timestamp de départ du tour (turn.createdAt), utilisé plutôt que l'instant de montage
  // du composant : celui-ci peut être remonté (changement de `key`) une fois le workflow
  // connu, ce qui décalerait un chrono basé sur le montage.
  since: string;
  // Étape réellement en cours côté serveur (persistée en base, sondée par useConversation.ts),
  // absente tant qu'aucun sondage n'a encore abouti — voir le repli sur l'estimation par temps
  // plus bas, seul mode disponible pour le tout premier message d'une conversation (son id
  // n'est connu qu'une fois /api/execute résolu, donc rien à sonder avant cela).
  currentStepKey?: string | null;
  // Étapes réutilisées d'une exécution précédente (reprise) : affichées comme terminées, à part.
  reusedSteps?: string[];
}

export default function StepIndicator({ workflow, scope, since, currentStepKey, reusedSteps }: StepIndicatorProps) {
  const steps = workflowSteps(workflow, scope);
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
  const reused = new Set(reusedSteps ?? []);
  // Les étapes reprises d'une exécution précédente sont déjà terminées : l'étape « courante » ne peut pas
  // être l'une d'elles (sinon aucune pastille ne serait en cours pendant l'estimation par temps).
  const firstOpenIndex = Math.max(0, steps.findIndex((step) => !reused.has(step.key)));
  const baseIndex = hasRealProgress ? realIndex : isPausedForRetry ? 0 : isQueued ? -1 : estimatedIndex;
  const activeIndex = baseIndex < 0 ? baseIndex : Math.max(baseIndex, firstOpenIndex);

  // Début de l'étape en cours, en secondes depuis le début du tour. Connu seulement si on l'a vu commencer : la
  // première étape non reprise commence avec le tour ; une étape suivante est vue changer (au plus 3 s de retard,
  // le sondage) ; une frise montée en cours de route (rechargement) n'a aucun moyen de le savoir : pas d'estimation.
  const [stepStart, setStepStart] = useState<{ key: string | null; startElapsed: number | null; seen: boolean }>({ key: null, startElapsed: null, seen: false });
  // Le suivi réel disparaît (pause de quota, relance automatique, file d'attente) : la même étape qui revient ensuite
  // est une NOUVELLE exécution de cette étape, son début est à remesurer (clé oubliée, `seen` conservé).
  if (!hasRealProgress && stepStart.key !== null) {
    setStepStart({ ...stepStart, key: null });
  }
  if (hasRealProgress && stepStart.key !== currentStepKey) {
    setStepStart({
      key: currentStepKey ?? null,
      startElapsed: stepStart.seen ? elapsed : (realIndex === firstOpenIndex ? 0 : null),
      seen: true,
    });
  }
  const stepElapsed = hasRealProgress && stepStart.key === currentStepKey && stepStart.startElapsed !== null
    ? Math.max(0, elapsed - stepStart.startElapsed)
    : null;
  const stats = useStepStats(workflow, hasRealProgress);
  const estimate = stats && currentStepKey && stepElapsed !== null
    ? estimateRemaining({ stepKeys: steps.map((step) => step.key), currentKey: currentStepKey, reused: reusedSteps, stats, currentElapsed: stepElapsed })
    : null;
  const currentLabel = steps[realIndex] ? stepShortLabel(steps[realIndex].label) : null;

  return (
    <div className="steps">
      {/* role="status"/aria-live seulement ici, PAS sur le conteneur entier : le chrono ci-
          dessous change chaque seconde (TICK_MS), et un lecteur d'écran annoncerait sinon ce
          texte à chaque tick pendant toute la durée d'une exécution (plusieurs minutes). Ce
          bloc-ci ne change, lui, qu'à chaque transition d'étape réelle. */}
      <div role="status" aria-live="polite">
        <ol className="stepper" aria-label="Progression de l'exécution">
          {steps.map((step, i) => {
            // Étape reprise d'une exécution précédente : déjà terminée, mais signalée à part.
            const isReused = reused.has(step.key);
            const done = isReused || i < activeIndex;
            const current = !done && i === activeIndex;
            const state = isReused ? 'reused' : done ? 'done' : current ? 'current' : 'todo';
            return (
              <li
                key={step.key}
                className={`stepper__item stepper__item--${state}`}
                aria-current={current ? 'step' : undefined}
                title={step.label}
              >
                <span className="stepper__node" aria-hidden="true">
                  {isReused ? '↻' : done ? '✓' : current ? step.icon : i + 1}
                </span>
                <span className="stepper__label">{stepShortLabel(step.label)}</span>
                <span className="sr-only">
                  {isReused ? ' (réutilisée de la tentative précédente)' : done ? ' (terminée)' : current ? ' (en cours)' : ' (à venir)'}
                </span>
              </li>
            );
          })}
        </ol>
      </div>
      {workflow === 'FEATURE' && scope === 'PETIT' && (
        <p className="steps__caption">Petite modification : l'étape d'architecture est sautée.</p>
      )}
      <p className="steps__caption">
        {hasRealProgress
          ? `En cours depuis ${formatSeconds(elapsed)} — progression suivie en direct.`
          : isPausedForRetry
            // Générique plutôt que "après une limite de quota atteinte" : ce message peut
            // aussi, brièvement, correspondre à un échec définitif (non lié au quota) qui n'a
            // pas encore fini d'être enregistré côté serveur — pas seulement à une pause avant
            // une nouvelle tentative sur limite de quota (voir crewquestion.py, qui efface
            // current_step sur TOUTE exception, retentée ou non).
            ? `En pause — nouvelle tentative éventuelle en cours. Si l'exécution reprend, ce sera depuis ${reused.size > 0 ? 'la première étape non reprise' : 'la toute première étape'}.`
            : isQueued
              ? "En file d'attente — d'autres exécutions occupent déjà ce service. Celle-ci démarrera automatiquement dès qu'un emplacement se libère."
              : `En cours depuis ${formatSeconds(elapsed)} — progression estimée, l'étape réellement en cours côté serveur peut différer.`}
      </p>
      {stepElapsed !== null && currentLabel && (
        <p className="steps__caption">
          Étape « {currentLabel} » depuis {formatSeconds(stepElapsed)}
          {estimate && (
            estimate.overrun
              ? ` — plus long que d'habitude (médiane ${formatSeconds(estimate.currentMedian)}) ; environ ${formatSeconds(roundEstimate(estimate.remainingSeconds))} pour la suite.`
              : ` — temps restant estimé : environ ${formatSeconds(roundEstimate(estimate.remainingSeconds))} (médiane des 30 derniers jours, hors pauses de quota).`
          )}
        </p>
      )}
    </div>
  );
}
