import type { ChatTurn } from '../types';
import { agentIcon } from '../constants/agentIcons';
import { failureHint } from '../utils/errors';
import {
  LONG_FAILURE_TEXT_CHARS, describeDeliveryFailure, failureCause, isTransientFailure, parseInline, splitGithubWork, splitPartialWork, type PartialWork,
} from '../utils/failureView';
import type { FailureDetail } from '../utils/parseFailureDetail';
import CopyButton from './CopyButton';

function Inline({ line }: { line: string }) {
  return (
    <>
      {parseInline(line).map((part, i) => {
        if (part.kind === 'code') return <code key={i}>{part.value}</code>;
        if (part.kind === 'link') {
          return <a key={i} href={part.value} target="_blank" rel="noopener noreferrer">{part.value.replace('https://github.com/', '')}</a>;
        }
        return <span key={i}>{part.value}</span>;
      })}
    </>
  );
}

function GithubWorkCard({ lines }: { lines: string[] }) {
  return (
    <div className="github-work">
      <p className="github-work__title">🐙 Travail déjà présent sur GitHub</p>
      <ul>
        {lines.map((line, index) => (
          <li key={index}><Inline line={line} /></li>
        ))}
      </ul>
    </div>
  );
}

// Ce que les agents déjà terminés avaient produit : reste disponible, « Relancer » reprend à l'étape en échec.
function PartialWorkCard({ work }: { work: PartialWork }) {
  return (
    <div className="github-work">
      <p className="github-work__title">✅ Déjà réalisé avant l'échec</p>
      <p className="muted">{work.summary}</p>
      {work.lines.length > 0 && (
        <ul>
          {work.lines.map((line, index) => (
            <li key={index}><Inline line={line} /></li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Texte technique d'un échec : affiché tel quel s'il est court, replié (avec sa taille) au-delà.
function TechnicalText({ text, summary }: { text: string; summary: string }) {
  if (text.length <= LONG_FAILURE_TEXT_CHARS) return <pre className="mono-block failure__text">{text}</pre>;
  return (
    <details className="failure__details">
      <summary>{summary} ({text.length.toLocaleString('fr-FR')} caractères)</summary>
      <div className="failure__details-actions"><CopyButton text={text} label="Copier" subject={summary} /></div>
      <pre className="mono-block failure__text">{text}</pre>
    </details>
  );
}

// Échec « la livraison n'a pas pu être confirmée » : ce qui s'est passé en une phrase, ce que GitHub contient, le
// rapport de l'agent replié, et quoi faire — plutôt qu'un mur de texte.
function DeliveryFailureBody({ main, githubLines }: { main: string; githubLines: string[] | null }) {
  const view = describeDeliveryFailure(main, githubLines);
  return (
    <>
      <p className="failure__diagnosis"><Inline line={view.summary} /></p>
      {view.reason && <p className="failure__reason">{view.reason}</p>}
      {githubLines && <GithubWorkCard lines={githubLines} />}
      {view.report && <TechnicalText text={view.report} summary="Rapport de l'agent (non vérifié sur GitHub)" />}
      <div className="failure__guide">
        <p className="failure__guide-title">Que faire ?</p>
        <ul>
          <li>Lis le rapport de l'agent : il dit souvent pourquoi rien n'a été écrit (outil GitHub refusé, fichier rejeté par la vérification de syntaxe).</li>
          <li>« Relancer » reprend sur la même branche et donne souvent le même résultat. Si ça se répète, démarre une nouvelle conversation (nouvelle branche) ou supprime la branche sur GitHub.</li>
          {view.branchUrl && (
            <li>
              <a href={view.branchUrl} target="_blank" rel="noopener noreferrer">Ouvrir la branche{view.branch ? ` ${view.branch}` : ''} sur GitHub</a>
            </li>
          )}
        </ul>
      </div>
    </>
  );
}

interface FailureBlockProps {
  turn: Pick<ChatTurn, 'result' | 'errorCode' | 'errorRetryable'>;
  detail: FailureDetail | null;
}

// Un échec d'exécution : panne temporaire (ambre : réessayer suffit sans doute) ou vrai échec (rouge), avec la
// cause en titre, l'étape touchée et, quand le message le contient, la carte de ce qui est déjà sur GitHub.
export default function FailureBlock({ turn, detail }: FailureBlockProps) {
  const transient = isTransientFailure(turn.errorRetryable, turn.errorCode);
  const cause = failureCause(turn.errorCode);
  const isDelivery = turn.errorCode === 'DELIVERY_FAILED';
  // Pas de conseil générique pour une livraison : son bloc « Que faire ? » est plus précis.
  const hint = isDelivery ? null : failureHint(turn.errorCode);
  const { main: withGithub, partial } = splitPartialWork(detail ? detail.message : (turn.result ?? ''));
  const { main, githubLines } = splitGithubWork(withGithub);

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
      {isDelivery ? (
        <DeliveryFailureBody main={main} githubLines={githubLines} />
      ) : (
        <>
          {main && <TechnicalText text={main} summary="Détail technique" />}
          {githubLines && <GithubWorkCard lines={githubLines} />}
        </>
      )}
      {partial && <PartialWorkCard work={partial} />}
      {hint && <p className="failure__hint">{hint}</p>}
    </div>
  );
}
