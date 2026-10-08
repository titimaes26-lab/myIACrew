import type { AgentRunView } from '../../types';
import { formatSeconds } from '../../utils/formatDuration';
import { buildTimeline } from '../../utils/timeline';

// Chronologie d'une exécution : une barre par étape, décalée de la fin de la précédente, puis une barre
// « Attente et finalisation » pour le temps qui n'appartient à aucune étape (voir buildTimeline). Une seule
// teinte pour les étapes ; l'attente est hachurée et grise, jamais confondue avec un agent.
export default function ExecutionTimeline({ runs, totalSeconds }: { runs: AgentRunView[]; totalSeconds: number | null }) {
  const { segments, total } = buildTimeline(runs, totalSeconds);
  if (segments.length === 0 || total <= 0) return null;
  const percent = (seconds: number) => `${(seconds / total) * 100}%`;

  return (
    <div className="viz-timeline" role="group" aria-label="Chronologie de l'exécution">
      {segments.map((segment) => (
        <div key={segment.key} className="viz-trow" aria-label={`${segment.label} : ${segment.incomplete && segment.duration === 0 ? 'durée inconnue' : formatSeconds(segment.duration)}`}>
          <span className="viz-tlabel">{segment.label}</span>
          <div className="viz-tplot">
            <div
              className={`viz-tbar${segment.kind === 'wait' ? ' viz-tbar--wait' : ''}${segment.incomplete ? ' viz-tbar--incomplete' : ''}`}
              style={{ left: percent(segment.start), width: `max(${percent(segment.duration)}, 3px)` }}
            />
          </div>
          <span className="viz-tvalue">{segment.incomplete && segment.duration === 0 ? '—' : formatSeconds(segment.duration)}</span>
        </div>
      ))}
      <div className="viz-ttotal">Durée totale : {formatSeconds(total)}</div>
    </div>
  );
}
