import { describe, expect, it } from 'vitest';
import { extractNextSteps, MAX_NEXT_STEP_CHARS } from './nextSteps';

const summary = (block: string) => `Faits\n\n### Ce qui a été fait\nX\n### Pourquoi ces choix\nY\n### À faire ensuite\n${block}`;

describe('extractNextSteps', () => {
  it('lit les puces du bloc « À faire ensuite »', () => {
    expect(extractNextSteps(summary('- Tester le panier\n* Ajouter un test\n'))).toEqual(['Tester le panier', 'Ajouter un test']);
  });

  it('ignore « Rien de particulier. » et un résumé sans le bloc', () => {
    expect(extractNextSteps(summary('Rien de particulier.'))).toEqual([]);
    expect(extractNextSteps(summary('- Rien de particulier.'))).toEqual([]);
    expect(extractNextSteps('### Ce qui a été fait\n- une puce')).toEqual([]);
  });

  it('s’arrête au titre suivant, plafonne à 3 puces et retire la mise en forme', () => {
    const text = `### À faire ensuite\n- **Un**\n- Deux\n- Trois\n- Quatre\n`;
    expect(extractNextSteps(text)).toEqual(['Un', 'Deux', 'Trois']);
    expect(extractNextSteps('### À faire ensuite\n- a\n### Autre\n- b')).toEqual(['a']);
  });

  it('tronque une puce trop longue', () => {
    const [step] = extractNextSteps(`### À faire ensuite\n- ${'x'.repeat(500)}`);
    expect(step).toHaveLength(MAX_NEXT_STEP_CHARS);
    expect(step.endsWith('…')).toBe(true);
  });
});

describe('avec le résultat réel du serveur', () => {
  it('la section « Résumé » produite par parseCrewResult donne ses étapes', async () => {
    const { parseCrewResult } = await import('./parseCrewResult');
    const raw = '## Architecte Logiciel React / TypeScript\n\nPlan\n\n<!--crew-summary-->\n\n## Résumé\n\n**Faits :** 1 étape terminée\n\n### À faire ensuite\n- Tester le panier';
    const summary = parseCrewResult(raw).find((section) => section.agentName === 'Résumé');
    expect(summary).toBeDefined();
    expect(extractNextSteps(summary!.content)).toEqual(['Tester le panier']);
  });
});
