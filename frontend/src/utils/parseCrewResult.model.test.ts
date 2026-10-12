import { describe, expect, it } from 'vitest';
import { extractAgentDuration, extractAgentMeta, parseCrewResult } from './parseCrewResult';

describe('modèle utilisé par l’agent (marqueur du backend)', () => {
  const raw = '## A\n<!--agent-duration:42.50-->\n<!--agent-model:gemini-3.8-flash-->\n\ntexte';

  it('extrait durée et modèle et retire les deux marqueurs du texte', () => {
    expect(extractAgentMeta(raw)).toEqual({ durationSeconds: 42.5, model: 'gemini-3.8-flash', content: '## A\ntexte' });
    expect(extractAgentMeta('## A\n<!--agent-model:gemini-3.5-flash-lite-->\n\nt')).toEqual({
      durationSeconds: null, model: 'gemini-3.5-flash-lite', content: '## A\nt',
    });
    expect(extractAgentMeta('sans marqueur')).toEqual({ durationSeconds: null, model: null, content: 'sans marqueur' });
  });

  it('extractAgentDuration garde sa forme et ne laisse plus le marqueur du modèle dans le texte', () => {
    expect(extractAgentDuration(raw)).toEqual({ durationSeconds: 42.5, content: '## A\ntexte' });
  });

  it('parseCrewResult porte le modèle de chaque section, null pour une section sans marqueur', () => {
    const sections = parseCrewResult(
      '## Lead Product / Game Designer\n<!--agent-duration:3.00-->\n<!--agent-model:gemini-3.8-flash-->\n\nspec\n\n---\n\n'
      + '## Développeur Fullstack React / TypeScript\n<!--agent-model:gemini-3.5-flash-lite-->\n\ncode\n\n<!--crew-summary-->\n\n## Résumé\n\nok',
    );
    expect(sections.map((s) => [s.agentName, s.model])).toEqual([
      ['Lead Product / Game Designer', 'gemini-3.8-flash'],
      ['Développeur Fullstack React / TypeScript', 'gemini-3.5-flash-lite'],
      ['Résumé', null],
    ]);
    expect(sections[0].content).toBe('spec');
    expect(sections.every((s) => !s.content.includes('agent-model'))).toBe(true);
  });

  it('ne lit que le bloc d’en-tête : un marqueur cité dans le texte de l’agent n’en est pas un', () => {
    const quoted = 'Le backend écrit <!--agent-model:gemini-fake--> après le titre.';
    expect(extractAgentMeta(`## A\n\n${quoted}`)).toEqual({ durationSeconds: null, model: null, content: `## A\n\n${quoted}` });
    // Marqueur cité sur sa propre ligne, plus bas : laissé dans le texte, et le vrai modèle d'en-tête reste celui retenu.
    const body = 'intro\n<!--agent-model:gemini-fake-->\n<!--agent-duration:999-->\nsuite';
    expect(extractAgentMeta(`## A\n<!--agent-model:gemini-3.8-flash-->\n\n${body}`)).toEqual({
      durationSeconds: null, model: 'gemini-3.8-flash', content: `## A\n${body}`,
    });
  });

  it('un texte d’agent qui COMMENCE par un marqueur (après la ligne vide) n’est pas pris pour l’en-tête', () => {
    const body = '<!--agent-model:gemini-fake-->\nsuite';
    expect(extractAgentMeta(`## A\n<!--agent-duration:1.00-->\n\n${body}`)).toEqual({
      durationSeconds: 1, model: null, content: `## A\n${body}`,
    });
  });

  it('lit l’en-tête de la valeur brute du suivi progressif (titre, ligne vide, marqueurs)', () => {
    expect(extractAgentMeta('## A\n\n<!--agent-duration:4.00-->\n<!--agent-model:gemini-3.8-flash-->\n\ntexte')).toEqual({
      durationSeconds: 4, model: 'gemini-3.8-flash', content: '## A\ntexte',
    });
  });
});
