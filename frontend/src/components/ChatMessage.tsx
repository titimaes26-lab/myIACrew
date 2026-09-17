import { lazy, Suspense } from 'react';
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

export default function ChatMessage({ turn }: { turn: ChatTurn }) {
  const duration = turn.updatedAt ? formatDuration(turn.createdAt, turn.updatedAt) : null;
  const failure = turn.status === 'failed' && turn.result ? parseFailureDetail(turn.result) : null;

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

        {turn.result && turn.status === 'success' && (
          <Suspense fallback={<p style={{ margin: 0, fontSize: '13px', color: '#666' }}>Chargement du résultat...</p>}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {parseCrewResult(turn.result).map((section, i) => (
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
