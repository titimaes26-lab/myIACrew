import type { ChatTurn } from '../types';
import { agentIcon } from '../constants/agentIcons';
import { failureHint } from '../utils/errors';
import { failureCause, isTransientFailure, parseInline, splitGithubWork } from '../utils/failureView';
import type { FailureDetail } from '../utils/parseFailureDetail';

function GithubWorkCard({ lines }: { lines: string[] }) {
  return (
    <div className="github-work">
      <p className="github-work__title">🐙 Travail déjà présent sur GitHub</p>
      <ul>
        {lines.map((line) => (
          <li key={line}>
            {parseInline(line).map((part, i) => {
              if (part.kind === 'code') return <code key={i}>{part.value}</code>;
              if (part.kind === 'link') {
                return <a key={i} href={part.value} target="_blank" rel="noopener noreferrer">{part.value.replace('https://github.com/', '')}</a>;
              }
              return <span key={i}>{part.value}</span>;
            })}
          </li>
        ))}
      </ul>
    </div>
  );
}

interface FailureBlockProps {
  turn: Pick<ChatTurn, 'result' | 'errorCode' | 'errorRetryable'>;
  detail: FailureDetail | null;
}

// Un échec d'exécution : panne temporaire (ambre : réessayer suffit sans doute) ou vrai échec (rouge), avec la
// cause en titre, l'étape touchée et, quand le message le contient, la carte de ce qui est déjà sur GitHub.
export default function FailureBlock({ turn, detail }: FailureBlockProps) {
  const transient = isTransientFailure(turn.errorRetryable);
  const cause = failureCause(turn.errorCode);
  const hint = failureHint(turn.errorCode);
  const { main, githubLines } = splitGithubWork(detail ? detail.message : (turn.result ?? ''));

  return (
    <div role="alert" className={`failure${transient ? ' failure--transient' : ''}`}>
      <div className="failure__title">
        <span aria-hidden="true">{cause.icon}</span>
        <span>{cause.title}</span>
      </div>
      {detail && (
        <p className="failure__step">
          <span aria-hidden="true">{agentIcon(detail.agentRole)}</span>{' '}
          Étape {detail.stepIndex}/{detail.totalSteps} — {detail.agentRole}
        </p>
      )}
      {main && <pre className="mono-block failure__text">{main}</pre>}
      {githubLines && <GithubWorkCard lines={githubLines} />}
      {hint && <p className="failure__hint">{hint}</p>}
    </div>
  );
}
