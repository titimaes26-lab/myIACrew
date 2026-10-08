import { parseAgentMetadata } from '../utils/parseAgentMetadata';
import { formatSeconds } from '../utils/formatDuration';
import Badge from './ui/Badge';
import { statusTone } from '../utils/statusTone';

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
    <div className="agent-summary">
      {/* Temps d'exécution de l'agent */}
      {typeof durationSeconds === 'number' && (
        <span className="muted">⏱ {formatSeconds(durationSeconds)}</span>
      )}

      {/* Status badge (verdict QA ou confiance) */}
      {metadata.status && (
        <Badge tone={statusTone(metadata.status)}>
          {metadata.status}
        </Badge>
      )}

      {/* Fichiers count */}
      {metadata.files && metadata.files.length > 0 && (
        <span className="muted">
          📁 {metadata.files.length} fichier{metadata.files.length !== 1 ? 's' : ''}
        </span>
      )}

      {/* Décisions/questions count */}
      {metadata.decisions && metadata.decisions.length > 0 && (
        <span className="muted">
          ✓ {metadata.decisions.length} décision{metadata.decisions.length !== 1 ? 's' : ''}
        </span>
      )}

      {metadata.questions && metadata.questions.length > 0 && (
        <span className="muted">
          ❓ {metadata.questions.length} question{metadata.questions.length !== 1 ? 's' : ''}
        </span>
      )}
    </div>
  );
}

export default AgentSummary;
