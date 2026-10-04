import { lazy, Suspense, useRef, type ReactNode } from 'react';
import type { CrewResultSection } from '../utils/parseCrewResult';
import { agentIcon } from '../constants/agentIcons';
import AgentSummary from './AgentSummary';
import CopyButton from './CopyButton';
import { extractNextSteps } from '../utils/nextSteps';

// Chargé à la demande : react-syntax-highlighter (Prism + grammaires) ne doit entrer dans le bundle que si un
// résultat d'agent est effectivement affiché.
const MarkdownRenderer = lazy(() => import('./MarkdownRenderer'));

const sectionTitle = (section: CrewResultSection) => section.agentName ?? 'Résultat';

// Résultat d'une exécution, une section repliable par agent. Au-delà d'une section : un sommaire (un clic ouvre et
// amène à la section), « Tout déplier / replier » et « Copier tout » ; chaque section se copie seule.
// <details> reste non contrôlé (nativement accessible au clavier) : la barre agit sur son attribut `open` ; seule la
// DERNIÈRE section est ouverte par défaut (le résumé de synthèse quand il existe, sinon le dernier agent).
export default function ResultSections({ sections, onSuggest }: { sections: CrewResultSection[]; onSuggest?: (text: string) => void }) {
  const container = useRef<HTMLDivElement>(null);
  const items = () => Array.from(container.current?.querySelectorAll<HTMLDetailsElement>(':scope > details.section-details') ?? []);

  const setAllOpen = (open: boolean) => items().forEach((details) => { details.open = open; });
  const goTo = (index: number) => {
    const details = items()[index];
    if (!details) return;
    details.open = true;
    details.scrollIntoView?.({ behavior: 'smooth', block: 'start' });
  };
  const everything = sections.map((section) => `## ${sectionTitle(section)}\n\n${section.content}`).join('\n\n---\n\n');

  return (
    <ResultBoundary>
      {sections.length > 1 && (
        <div className="sections-toolbar">
          <nav className="sections-toc" aria-label="Sommaire du résultat">
            {sections.map((section, i) => (
              <button key={i} type="button" className="sections-toc__item" onClick={() => goTo(i)}>
                <span aria-hidden="true">{agentIcon(sectionTitle(section))}</span> {sectionTitle(section)}
              </button>
            ))}
          </nav>
          <div className="sections-toolbar__actions">
            <button type="button" className="copy-btn" onClick={() => setAllOpen(true)}>Tout déplier</button>
            <button type="button" className="copy-btn" onClick={() => setAllOpen(false)}>Tout replier</button>
            <CopyButton text={everything} label="Copier tout" subject="tout le résultat" />
          </div>
        </div>
      )}
      <div className="sections" ref={container}>
        {sections.map((section, i) => (
          <details key={i} open={i === sections.length - 1} className="section-details">
            <summary>
              <div className="agent-title">
                <span aria-hidden="true">{agentIcon(sectionTitle(section))}</span>
                <span>{sectionTitle(section)}</span>
              </div>
              {section.agentName && (
                <AgentSummary agentName={section.agentName} content={section.content} durationSeconds={section.durationSeconds} />
              )}
            </summary>
            <div className="section-details__body">
              <div className="section-details__actions">
                <CopyButton text={section.content} subject={sectionTitle(section)} />
              </div>
              <MarkdownRenderer content={section.content} />
              {onSuggest && section.agentName === 'Résumé' && <NextSteps content={section.content} onSuggest={onSuggest} />}
            </div>
          </details>
        ))}
      </div>
    </ResultBoundary>
  );
}

// Prochaines étapes du résumé : un clic préremplit la zone de saisie (rien n'est envoyé).
function NextSteps({ content, onSuggest }: { content: string; onSuggest: (text: string) => void }) {
  const steps = extractNextSteps(content);
  if (steps.length === 0) return null;
  return (
    <div className="next-steps" role="group" aria-label="Prochaines étapes proposées">
      <p className="next-steps__title">Prochaines étapes</p>
      {steps.map((step, index) => (
        <button key={index} type="button" className="next-steps__item" onClick={() => onSuggest(step)}
          aria-label={`Préremplir la zone de saisie : ${step}`}>
          <span aria-hidden="true">➡️</span> {step}
        </button>
      ))}
    </div>
  );
}

function ResultBoundary({ children }: { children: ReactNode }) {
  return <Suspense fallback={<p className="note">Chargement du résultat...</p>}>{children}</Suspense>;
}
