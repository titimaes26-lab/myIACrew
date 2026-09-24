import { parseAgentMetadata } from '../utils/parseAgentMetadata';

interface AgentSummaryProps {
  agentName: string;
  content: string;
}

export function AgentSummary({ agentName, content }: AgentSummaryProps) {
  const metadata = parseAgentMetadata(content, agentName);

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap', marginLeft: '8px', fontSize: '12px' }}>
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
