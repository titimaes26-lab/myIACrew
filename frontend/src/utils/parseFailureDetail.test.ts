import { describe, expect, it } from 'vitest';
import { extractAgentDuration, parseCrewResult } from './parseCrewResult';
import { parseFailureDetail } from './parseFailureDetail';

describe('parseFailureDetail', () => {
  it("lit l'étape, le rôle et le message d'un échec d'étape", () => {
    expect(parseFailureDetail("Échec à l'étape 2/5 (Architecte) : Le quota est épuisé.\n\nsuite")).toEqual({
      stepIndex: 2, totalSteps: 5, agentRole: 'Architecte', message: 'Le quota est épuisé.\n\nsuite',
    });
  });

  it('renvoie null pour un échec hors étape', () => {
    expect(parseFailureDetail('boum')).toBeNull();
  });
});

describe('extractAgentDuration', () => {
  it('extrait la durée et retire le marqueur', () => {
    expect(extractAgentDuration('## A\n<!--agent-duration:42.50-->\n\ntexte')).toEqual({ durationSeconds: 42.5, content: '## A\ntexte' });
    expect(extractAgentDuration('sans marqueur')).toEqual({ durationSeconds: null, content: 'sans marqueur' });
  });
});

describe('parseCrewResult', () => {
  it('découpe sur les rôles connus uniquement', () => {
    const sections = parseCrewResult('## Lead Product / Game Designer\n\nspec\n\n---\n\n## Recommandations\n\nrestent dans la section');
    expect(sections).toHaveLength(1);
    expect(sections[0].agentName).toBe('Lead Product / Game Designer');
    expect(sections[0].content).toContain('Recommandations');
  });
});
