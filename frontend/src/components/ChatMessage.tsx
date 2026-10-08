import { memo, useMemo } from 'react';
import type { ChatTurn, LaunchControls } from '../types';
import StepIndicator from './StepIndicator';
import { parseCrewResult, extractAgentDuration } from '../utils/parseCrewResult';
import { parseFailureDetail } from '../utils/parseFailureDetail';
import { isTransientFailure } from '../utils/failureView';
import FailureBlock from './FailureBlock';
import { stepShortLabelForKey } from '../constants/workflowSteps';
import { elapsedSeconds, formatDuration, formatTime } from '../utils/formatDuration';
import ResultSections from './ResultSections';
import LaunchPreview from './LaunchPreview';
import Button from './ui/Button';
import ExecutionBreakdown from './metrics/ExecutionBreakdown';

const STATUS_LABEL: Record<ChatTurn['status'], string> = {
  clarifying: '❓ Précisions nécessaires',
  running: '🔄 En cours...',
  success: '✅ Terminé',
  failed: '❌ Échec',
  cancelled: '🚫 Annulé',
};

function formatMetrics(turn: ChatTurn): string | null {
  // == null (pas !turn.apiCallsCount) : une exécution avec 0 appel réel doit afficher
  // "0 appel API", pas être traitée comme si la métrique était absente.
  if (turn.apiCallsCount == null) return null;

  const parts = [`🔢 ${turn.apiCallsCount} appel${turn.apiCallsCount === 1 ? '' : 's'} API`];
  if (turn.rateLimitHits) {
    parts.push(`⏳ ${turn.rateLimitHits} pause${turn.rateLimitHits === 1 ? '' : 's'} quota`);
  }
  // Temps d'attente total (pacing interne systématique + pauses quota confondus) :
  // affiché séparément de "pauses quota" ci-dessus plutôt qu'entre parenthèses juste
  // après, ce qui laisserait croire à tort que cette durée n'est due qu'aux pauses
  // quota alors qu'elle inclut aussi l'espacement volontaire entre chaque appel.
  const waitSeconds = Math.round(turn.totalWaitTimeSeconds ?? 0);
  if (waitSeconds >= 1) {
    parts.push(`⏱️ ${waitSeconds}s d'attente au total`);
  }
  return parts.join(' · ');
}

interface ChatMessageProps {
  turn: ChatTurn;
  // Optionnel : absent (HistoryPanel n'affiche pas de ChatMessage) ou non fourni ne change
  // rien d'autre que masquer le bouton "Relancer", jamais une erreur.
  onRetry?: (turn: ChatTurn) => void;
  // Désactive "Relancer" pendant qu'un autre envoi est déjà en cours (une seule exécution à la
  // fois par conversation, imposée côté serveur — voir backend/routes_execute.py), MAIS AUSSI tant qu'une
  // clarification est en attente de réponse : "Relancer" préremplit la zone de saisie avec le
  // texte d'UN AUTRE tour, qui serait alors envoyé comme réponse à cette clarification-là plutôt
  // que comme la nouvelle demande affichée (sendMessage route tout texte tapé pendant qu'une
  // clarification est en attente vers cette clarification, quel que soit son contenu réel — voir
  // Studio.tsx).
  retryDisabled: boolean;
  // Actions de l'aperçu avant lancement ; absentes, l'aperçu n'est tout simplement pas affiché.
  launch?: LaunchControls;
  // Clic sur une prochaine étape du résumé : préremplit la zone de saisie (jamais d'envoi automatique).
  onSuggest?: (text: string) => void;
}

function ChatMessage({ turn, onRetry, retryDisabled, launch, onSuggest }: ChatMessageProps) {
  const duration = turn.updatedAt ? formatDuration(turn.createdAt, turn.updatedAt) : null;
  const failure = turn.status === 'failed' && turn.result ? parseFailureDetail(turn.result) : null;
  const metricsLabel = formatMetrics(turn);
  // Pendant l'exécution, la frise (StepIndicator) montre déjà les étapes reprises : la note ne sert qu'après.
  const resumedLabel = turn.resumedSteps?.length && turn.status !== 'running'
    ? turn.resumedSteps.map(stepShortLabelForKey).join(', ')
    : null;
  // Calculé une seule fois et réutilisé pour le useMemo ci-dessous ET le rendu JSX plus
  // bas, plutôt que dupliqué aux deux endroits : sinon les deux pourraient diverger si
  // l'un est modifié sans l'autre (ex: JSX étendu à un autre statut sans mettre à jour
  // la condition du useMemo), laissant `sections` vide pour un cas que le JSX affiche.
  const isSuccess = Boolean(turn.result) && turn.status === 'success';
  const isRunningWithAgents = turn.status === 'running' && turn.completedAgents && Object.keys(turn.completedAgents).length > 0;

  // Le parsing par regex du résultat complet (qui peut contenir du code source entier sur
  // un workflow FEATURE/DESIGN_AND_DEV) ne doit être refait que si turn.result change, pas
  // à chaque rendu de ce composant.
  // Affiche aussi les agents complétés progressivement pendant l'exécution (isRunningWithAgents).
  const sections = useMemo(() => {
    const allSections: ReturnType<typeof parseCrewResult> = [];

    // Ajouter les agents progressivement reçus
    if (turn.completedAgents) {
      Object.entries(turn.completedAgents).forEach(([agentName, rawContent]) => {
        const { durationSeconds, content } = extractAgentDuration(rawContent);
        allSections.push({ agentName, content, durationSeconds });
      });
    }

    // Ajouter les sections finales du résultat complet (si succès)
    if (isSuccess && turn.result) {
      const finalSections = parseCrewResult(turn.result);
      // Fusionner en évitant les doublons (certains agents peuvent être dans completedAgents ET dans result)
      finalSections.forEach((section) => {
        if (!allSections.some((s) => s.agentName === section.agentName)) {
          allSections.push(section);
        }
      });
    }

    return allSections;
  }, [isSuccess, turn.result, turn.completedAgents]);

  return (
    <div className="turn">
      <div className="bubble bubble--user">{turn.userMessage}</div>
      <div className="bubble bubble--agent">
        <div className="turn-meta" role="status" aria-live="polite">
          {/* dismissedLocally (voir sa définition dans types.ts) : affiché comme "Annulé" bien
              que turn.status reste 'running' en interne (le sondage de progression continue) —
              purement pour ne pas afficher "En cours..." indéfiniment à l'utilisateur après son
              clic sur "Annuler". */}
          {turn.status === 'running' && turn.dismissedLocally
            ? '🚫 Annulé'
            // L'aperçu avant lancement réutilise le statut « clarifying » : il ne demande aucune précision, il attend un « Lancer ».
            : turn.launchPreview ? '🚀 En attente de confirmation' : STATUS_LABEL[turn.status]}
          {turn.workflow ? ` · ${turn.workflow}` : ''}
          {` · ${formatTime(turn.createdAt)}`}
          {duration ? ` · ${duration}` : ''}
          {metricsLabel ? ` · ${metricsLabel}` : ''}
        </div>

        {resumedLabel && (
          <p className="note note--xs">
            ↻ Reprise : {resumedLabel} déjà réussie{turn.resumedSteps && turn.resumedSteps.length > 1 ? 's' : ''} lors de la tentative précédente, réutilisée{turn.resumedSteps && turn.resumedSteps.length > 1 ? 's' : ''}.
          </p>
        )}

        {typeof turn.id === 'number' && (turn.status === 'success' || turn.status === 'failed') && (
          <ExecutionBreakdown executionId={turn.id} totalSeconds={turn.updatedAt ? elapsedSeconds(turn.createdAt, turn.updatedAt) : null} />
        )}

        {turn.agentSummary && <p className="paragraph">{turn.agentSummary}</p>}

        {turn.launchPreview && launch && <LaunchPreview preview={turn.launchPreview} controls={launch} />}

        {turn.questions && turn.questions.length > 0 && (
          <ul className="question-list">
            {turn.questions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        )}

        {turn.status === 'running' && !turn.dismissedLocally && (
          <StepIndicator key={turn.workflow ?? 'pending'} workflow={turn.workflow} scope={turn.scope} since={turn.createdAt} currentStepKey={turn.currentStep} queueAhead={turn.queueAhead} reusedSteps={turn.resumedSteps} />
        )}

        {turn.status === 'running' && turn.dismissedLocally && (
          <p className="note">
            Annulé côté interface. L'exécution continue côté serveur : le résultat, une fois prêt,
            sera visible dans l'historique de cette conversation.
          </p>
        )}

        {turn.status === 'failed' && <FailureBlock turn={turn} detail={failure} />}

        {turn.status === 'failed' && onRetry && (
          <Button
            variant={isTransientFailure(turn.errorRetryable, turn.errorCode) ? 'primary' : 'danger'}
            size="sm"
            className="retry-btn"
            onClick={() => onRetry(turn)}
            disabled={retryDisabled}
          >
            🔁 Relancer cette demande
          </Button>
        )}

        {turn.result && turn.status === 'cancelled' && (
          <p className="note">{turn.result}</p>
        )}

        {(isSuccess || isRunningWithAgents) && <ResultSections sections={sections} onSuggest={onSuggest} />}
      </div>
    </div>
  );
}

// memo() : sans ça, chaque mutation de `turns` dans useConversation (nouveau tour ajouté,
// tour "running" -> "success") remonte un nouveau tableau et re-rendrait TOUS les messages
// déjà affichés, même ceux dont le `turn` n'a pas changé de référence (useConversation
// renvoie le même objet pour les tours non modifiés) — coûteux sur une longue conversation
// vu le markdown/la coloration syntaxique à refaire pour chacun.
export default memo(ChatMessage);
