import { lazy, memo, Suspense, useMemo } from 'react';
import type { ChatTurn } from '../types';
import StepIndicator from './StepIndicator';
import { parseCrewResult } from '../utils/parseCrewResult';
import { parseFailureDetail } from '../utils/parseFailureDetail';
import { formatDuration, formatTime } from '../utils/formatDuration';
import { agentIcon } from '../constants/agentIcons';

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

function ChatMessage({ turn }: { turn: ChatTurn }) {
  const duration = turn.updatedAt ? formatDuration(turn.createdAt, turn.updatedAt) : null;
  const failure = turn.status === 'failed' && turn.result ? parseFailureDetail(turn.result) : null;
  const metricsLabel = formatMetrics(turn);
  // Calculé une seule fois et réutilisé pour le useMemo ci-dessous ET le rendu JSX plus
  // bas, plutôt que dupliqué aux deux endroits : sinon les deux pourraient diverger si
  // l'un est modifié sans l'autre (ex: JSX étendu à un autre statut sans mettre à jour
  // la condition du useMemo), laissant `sections` vide pour un cas que le JSX affiche.
  const isSuccess = Boolean(turn.result) && turn.status === 'success';
  // Le parsing par regex du résultat complet (qui peut contenir du code source entier sur
  // un workflow FEATURE/DESIGN_AND_DEV) ne doit être refait que si turn.result change, pas
  // à chaque rendu de ce composant.
  const sections = useMemo(() => (isSuccess ? parseCrewResult(turn.result!) : []), [isSuccess, turn.result]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '20px' }}>
      <div style={{ alignSelf: 'flex-end', maxWidth: '80%', backgroundColor: '#0070f3', color: '#fff', padding: '10px 14px', borderRadius: '12px 12px 2px 12px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {turn.userMessage}
      </div>
      <div style={{ alignSelf: 'flex-start', maxWidth: '90%', backgroundColor: '#f8f9fa', border: '1px solid #e1e4e8', borderRadius: '2px 12px 12px 12px', padding: '12px 14px' }}>
        <div style={{ fontSize: '13px', color: '#666', marginBottom: '6px' }}>
          {STATUS_LABEL[turn.status]}
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

        {turn.status === 'running' && (
          <StepIndicator key={turn.workflow ?? 'pending'} workflow={turn.workflow} since={turn.createdAt} />
        )}

        {turn.result && turn.status === 'failed' && failure && (
          <div style={{ backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '8px', padding: '10px 12px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 600, color: '#991b1b', marginBottom: '6px' }}>
              <span>{agentIcon(failure.agentRole)}</span>
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
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '13px', margin: 0, fontFamily: 'monospace' }}>
            {turn.result}
          </pre>
        )}

        {turn.result && turn.status === 'cancelled' && (
          <p style={{ margin: 0, fontSize: '13px', color: '#666', fontStyle: 'italic' }}>{turn.result}</p>
        )}

        {isSuccess && (
          <Suspense fallback={<p style={{ margin: 0, fontSize: '13px', color: '#666' }}>Chargement du résultat...</p>}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {sections.map((section, i) => (
                <div
                  key={i}
                  style={{ backgroundColor: '#fff', border: '1px solid #e1e4e8', borderRadius: '8px', padding: '10px 12px' }}
                >
                  {section.agentName && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '13px', fontWeight: 600, color: '#444', marginBottom: '6px' }}>
                      <span>{agentIcon(section.agentName)}</span>
                      <span>{section.agentName}</span>
                    </div>
                  )}
                  <MarkdownRenderer content={section.content} />
                </div>
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
