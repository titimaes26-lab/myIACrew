import { lazy, memo, Suspense, useMemo } from 'react';
import type { ChatTurn } from '../types';
import StepIndicator from './StepIndicator';
import { parseCrewResult, extractAgentDuration } from '../utils/parseCrewResult';
import { parseFailureDetail } from '../utils/parseFailureDetail';
import { formatDuration, formatTime } from '../utils/formatDuration';
import { agentIcon } from '../constants/agentIcons';
import AgentSummary from './AgentSummary';

// Chargé à la demande : react-syntax-highlighter (Prism + grammaires) ne doit entrer
// dans le bundle que si un résultat d'agent est effectivement affiché.
const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'));

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
  // fois par conversation, imposée côté serveur — voir backend/main.py), MAIS AUSSI tant qu'une
  // clarification est en attente de réponse : "Relancer" préremplit la zone de saisie avec le
  // texte d'UN AUTRE tour, qui serait alors envoyé comme réponse à cette clarification-là plutôt
  // que comme la nouvelle demande affichée (sendMessage route tout texte tapé pendant qu'une
  // clarification est en attente vers cette clarification, quel que soit son contenu réel — voir
  // Studio.tsx).
  retryDisabled: boolean;
}

function ChatMessage({ turn, onRetry, retryDisabled }: ChatMessageProps) {
  const duration = turn.updatedAt ? formatDuration(turn.createdAt, turn.updatedAt) : null;
  const failure = turn.status === 'failed' && turn.result ? parseFailureDetail(turn.result) : null;
  const metricsLabel = formatMetrics(turn);
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
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '20px' }}>
      <div style={{ alignSelf: 'flex-end', maxWidth: '80%', backgroundColor: '#0070f3', color: '#fff', padding: '10px 14px', borderRadius: '12px 12px 2px 12px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {turn.userMessage}
      </div>
      <div style={{ alignSelf: 'flex-start', maxWidth: '90%', backgroundColor: '#f8f9fa', border: '1px solid #e1e4e8', borderRadius: '2px 12px 12px 12px', padding: '12px 14px' }}>
        <div style={{ fontSize: '13px', color: '#666', marginBottom: '6px' }} role="status" aria-live="polite">
          {/* dismissedLocally (voir sa définition dans types.ts) : affiché comme "Annulé" bien
              que turn.status reste 'running' en interne (le sondage de progression continue) —
              purement pour ne pas afficher "En cours..." indéfiniment à l'utilisateur après son
              clic sur "Annuler". */}
          {turn.status === 'running' && turn.dismissedLocally ? '🚫 Annulé' : STATUS_LABEL[turn.status]}
          {turn.workflow ? ` · ${turn.workflow}` : ''}
          {` · ${formatTime(turn.createdAt)}`}
          {duration ? ` · ${duration}` : ''}
          {metricsLabel ? ` · ${metricsLabel}` : ''}
        </div>

        {turn.agentSummary && <p style={{ margin: '0 0 8px 0' }}>{turn.agentSummary}</p>}

        {turn.questions && turn.questions.length > 0 && (
          <ul style={{ margin: '0 0 8px 0', paddingLeft: '20px' }}>
            {turn.questions.map((q, i) => (
              <li key={i} style={{ marginBottom: '4px' }}>{q}</li>
            ))}
          </ul>
        )}

        {turn.status === 'running' && !turn.dismissedLocally && (
          <StepIndicator key={turn.workflow ?? 'pending'} workflow={turn.workflow} since={turn.createdAt} currentStepKey={turn.currentStep} />
        )}

        {turn.status === 'running' && turn.dismissedLocally && (
          <p style={{ margin: 0, fontSize: '13px', color: '#666', fontStyle: 'italic' }}>
            Annulé côté interface. L'exécution continue côté serveur : le résultat, une fois prêt,
            sera visible dans l'historique de cette conversation.
          </p>
        )}

        {turn.result && turn.status === 'failed' && failure && (
          <div role="alert" style={{ backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '8px', padding: '10px 12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 600, color: '#991b1b', marginBottom: '6px' }}>
              <span aria-hidden="true">{agentIcon(failure.agentRole)}</span>
              <span>
                Échec à l'étape {failure.stepIndex}/{failure.totalSteps} — {failure.agentRole}
              </span>
            </div>
            <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '13px', margin: 0, fontFamily: 'monospace', color: '#7f1d1d' }}>
              {failure.message}
            </pre>
          </div>
        )}

        {turn.result && turn.status === 'failed' && !failure && (
          <pre role="alert" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '13px', margin: 0, fontFamily: 'monospace' }}>
            {turn.result}
          </pre>
        )}

        {turn.status === 'failed' && onRetry && (
          <button
            type="button"
            onClick={() => onRetry(turn)}
            disabled={retryDisabled}
            style={{
              marginTop: '8px',
              padding: '5px 12px',
              fontSize: '13px',
              backgroundColor: '#fff',
              color: '#991b1b',
              border: '1px solid #fca5a5',
              borderRadius: '6px',
              cursor: retryDisabled ? 'not-allowed' : 'pointer',
            }}
          >
            🔁 Relancer cette demande
          </button>
        )}

        {turn.result && turn.status === 'cancelled' && (
          <p style={{ margin: 0, fontSize: '13px', color: '#666', fontStyle: 'italic' }}>{turn.result}</p>
        )}

        {(isSuccess || isRunningWithAgents) && (
          <Suspense fallback={<p style={{ margin: 0, fontSize: '13px', color: '#666' }}>Chargement du résultat...</p>}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {sections.map((section, i) => (
                // <details>/<summary> plutôt qu'un état React local : un résultat FEATURE/
                // DESIGN_AND_DEV peut empiler plusieurs sections contenant du code source
                // complet, repliables au clic sans code de gestion d'état supplémentaire et
                // nativement accessibles au clavier/lecteur d'écran. Seule la DERNIÈRE section
                // est ouverte par défaut (le résumé de synthèse quand il existe — parseCrewResult
                // l'ajoute toujours en dernier — sinon le dernier agent à s'être exprimé) : les
                // précédentes, déjà "dépassées" par la suite du résultat, restent repliées pour
                // éviter un mur de texte sur un DESIGN_AND_DEV à 4 sections. `open` n'est lu par
                // React qu'au premier rendu de ce <details> (non contrôlé ensuite) : un repli/
                // dépli manuel par l'utilisateur reste donc acquis malgré un re-rendu du parent.
                <details
                  key={i}
                  open={i === sections.length - 1}
                  style={{ backgroundColor: '#fff', border: '1px solid #e1e4e8', borderRadius: '8px', padding: '10px 12px' }}
                >
                  <summary
                    style={{
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      gap: '6px',
                      fontSize: '13px',
                      fontWeight: 600,
                      color: '#444',
                      justifyContent: 'space-between',
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span aria-hidden="true">{agentIcon(section.agentName ?? 'Résultat')}</span>
                      <span>{section.agentName ?? 'Résultat'}</span>
                    </div>
                    {section.agentName && (
                      <AgentSummary
                        agentName={section.agentName}
                        content={section.content}
                        durationSeconds={section.durationSeconds}
                      />
                    )}
                  </summary>
                  <div style={{ marginTop: '8px' }}>
                    <MarkdownRenderer content={section.content} />
                  </div>
                </details>
              ))}
            </div>
          </Suspense>
        )}
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
