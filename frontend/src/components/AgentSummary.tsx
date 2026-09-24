import { parseAgentMetadata } from '../utils/parseAgentMetadata';
import { formatSeconds } from '../utils/formatDuration';

interface AgentSummaryProps {
  agentName: string;
  content: string;
  // Temps d'exécution de l'agent en secondes (voir CrewResultSection dans parseCrewResult.ts) ;
  // absent/null tant que l'agent n'a pas terminé ou si la durée n'a pas pu être mesurée.
  durationSeconds?: number | null;
}

export function AgentSummary({ agentName, content, durationSeconds }: AgentSummaryProps) {
  const metadata = parseAgentMetadata(content, agentName);

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginLeft: '8px', fontSize: '12px' }}>
      {/* Temps d'exécution de l'agent */}
      {typeof durationSeconds === 'number' && (
        <span style={{ color: '#666' }}>⏱ {formatSeconds(durationSeconds)}</span>
      )}

      {/* Status badge (verdict QA ou confiance) */}
      {metadata.status && (
        <span
          style={{
            backgroundColor: metadata.status.includes('GO') ? '#d1fae5' : metadata.status.includes('NON') ? '#fee2e2' : '#e0e7ff',
            color: metadata.status.includes('GO') ? '#065f46' : metadata.status.includes('NON') ? '#7f1d1d' : '#1e40af',
            padding: '2px 6px',
            borderRadius: '3px',
            fontWeight: 500,
          }}
        >
          {metadata.status}
        </span>
      )}

      {/* Fichiers count */}
      {metadata.files && metadata.files.length > 0 && (
        <span style={{ color: '#666' }}>
          📁 {metadata.files.length} fichier{metadata.files.length !== 1 ? 's' : ''}
        </span>
      )}

      {/* Décisions/questions count */}
      {metadata.decisions && metadata.decisions.length > 0 && (
        <span style={{ color: '#666' }}>
          ✓ {metadata.decisions.length} décision{metadata.decisions.length !== 1 ? 's' : ''}
        </span>
      )}

      {metadata.questions && metadata.questions.length > 0 && (
        <span style={{ color: '#666' }}>
          ❓ {metadata.questions.length} question{metadata.questions.length !== 1 ? 's' : ''}
        </span>
      )}
    </div>
  );
}

export default AgentSummary;
