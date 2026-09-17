import type { ChatTurn } from '../types';
import MarkdownRenderer from './MarkdownRenderer';
import StepIndicator from './StepIndicator';
import { parseCrewResult } from '../utils/parseCrewResult';
import { agentIcon } from '../constants/agentIcons';

const STATUS_LABEL: Record<ChatTurn['status'], string> = {
  clarifying: '❓ Précisions nécessaires',
  running: '🔄 En cours...',
  success: '✅ Terminé',
  failed: '❌ Échec',
};

export default function ChatMessage({ turn }: { turn: ChatTurn }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '20px' }}>
      <div style={{ alignSelf: 'flex-end', maxWidth: '80%', backgroundColor: '#0070f3', color: '#fff', padding: '10px 14px', borderRadius: '12px 12px 2px 12px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
        {turn.userMessage}
      </div>
      <div style={{ alignSelf: 'flex-start', maxWidth: '90%', backgroundColor: '#f8f9fa', border: '1px solid #e1e4e8', borderRadius: '2px 12px 12px 12px', padding: '12px 14px' }}>
        <div style={{ fontSize: '13px', color: '#666', marginBottom: '6px' }}>
          {STATUS_LABEL[turn.status]}
          {turn.workflow ? ` · ${turn.workflow}` : ''}
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
          <StepIndicator key={turn.workflow ?? 'pending'} workflow={turn.workflow} />
        )}

        {turn.result && (
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
        )}
      </div>
    </div>
  );
}
