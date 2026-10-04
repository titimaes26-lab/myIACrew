import { describe, expect, it } from 'vitest';
import { failureCause, isTransientFailure, parseInline, splitGithubWork, splitPartialWork } from './failureView';

describe('isTransientFailure', () => {
  it('fait confiance à retryable quand il est connu', () => {
    expect(isTransientFailure(true, 'GUARDRAIL_FAILED')).toBe(true);
    expect(isTransientFailure(false, 'QUOTA_EXHAUSTED')).toBe(false);
  });

  it('retombe sur le code de la cause quand retryable est absent (ligne ancienne)', () => {
    expect(isTransientFailure(null, 'QUOTA_EXHAUSTED')).toBe(true);
    expect(isTransientFailure(undefined, 'INTERRUPTED')).toBe(true);
    expect(isTransientFailure(undefined, 'GUARDRAIL_FAILED')).toBe(false);
    expect(isTransientFailure(undefined, undefined)).toBe(false);
  });
});

describe('failureCause', () => {
  it('donne une icône et un titre par code, et un repli pour un code inconnu', () => {
    expect(failureCause('QUOTA_EXHAUSTED').title).toBe('Quota du modèle épuisé');
    expect(failureCause('DELIVERY_FAILED')).toEqual({ icon: '📦', title: 'Livraison GitHub non confirmée' });
    expect(isTransientFailure(undefined, 'DELIVERY_FAILED')).toBe(false);
    expect(failureCause('CODE_FUTUR').title).toBe("Échec de l'exécution");
    expect(failureCause(null).icon).toBe('❌');
  });
});

describe('splitGithubWork', () => {
  const marker = '--- Travail déjà présent sur GitHub ---';

  it('laisse intact un message sans bloc GitHub', () => {
    expect(splitGithubWork('boum')).toEqual({ main: 'boum', githubLines: null });
  });

  it('sépare le message principal des lignes du bloc, sans les tirets', () => {
    const result = splitGithubWork(`Erreur\n\n${marker}\n- Branche \`b\` : 2 commits\n- Pull Request ouverte : https://github.com/o/r/pull/1`);
    expect(result.main).toBe('Erreur');
    expect(result.githubLines).toEqual(['Branche `b` : 2 commits', 'Pull Request ouverte : https://github.com/o/r/pull/1']);
  });

  it('rattache une ligne sans tiret à la ligne précédente (message sur plusieurs lignes)', () => {
    const result = splitGithubWork(`x\n\n${marker}\n- Impossible de vérifier (boum\nligne 2) : la branche\n- Reprise : ok`);
    expect(result.githubLines).toEqual(['Impossible de vérifier (boum ligne 2) : la branche', 'Reprise : ok']);
  });
});

describe('parseInline', () => {
  it('reconnaît le code entre accents graves et les liens github.com', () => {
    expect(parseInline('Voir `x` et https://github.com/o/r')).toEqual([
      { kind: 'text', value: 'Voir ' },
      { kind: 'code', value: 'x' },
      { kind: 'text', value: ' et ' },
      { kind: 'link', value: 'https://github.com/o/r' },
    ]);
  });

  it('laisse la ponctuation de fin de phrase hors du lien', () => {
    const parts = parseInline('PR : https://github.com/o/r/pull/12.');
    expect(parts).toContainEqual({ kind: 'link', value: 'https://github.com/o/r/pull/12' });
    expect(parts[parts.length - 1]).toEqual({ kind: 'text', value: '.' });
  });

  it("ne transforme jamais une autre adresse ni du HTML en lien", () => {
    expect(parseInline('http://evil.example/x <a href="x">')).toEqual([{ kind: 'text', value: 'http://evil.example/x <a href="x">' }]);
  });
});

describe('splitPartialWork', () => {
  const partial = "--- Déjà réalisé avant l'échec ---\n2 étapes terminées avant l'échec\n- Architecte : Plan\n- Analyste : Cause";

  it('sépare le bloc de la fin du message', () => {
    expect(splitPartialWork('boum')).toEqual({ main: 'boum', partial: null });
    const { main, partial: work } = splitPartialWork(`boum\n\n${partial}`);
    expect(main).toBe('boum');
    expect(work).toEqual({ summary: "2 étapes terminées avant l'échec", lines: ['Architecte : Plan', 'Analyste : Cause'] });
  });

  it('laisse le bloc GitHub intact pour splitGithubWork, qui le précède', () => {
    const github = '--- Travail déjà présent sur GitHub ---\n- Rien n\'a été poussé';
    const { main } = splitPartialWork(`boum\n\n${github}\n\n${partial}`);
    expect(splitGithubWork(main)).toEqual({ main: 'boum', githubLines: ["Rien n'a été poussé"] });
  });
});

describe('EXECUTION_TIMEOUT', () => {
  it('est une panne passagère avec sa propre cause', () => {
    expect(isTransientFailure(null, 'EXECUTION_TIMEOUT')).toBe(true);
    expect(failureCause('EXECUTION_TIMEOUT').title).toContain('durée maximale');
  });
});

describe('RATE_LIMITED', () => {
  it('est une panne passagère avec sa propre cause', () => {
    expect(isTransientFailure(null, 'RATE_LIMITED')).toBe(true);
    expect(failureCause('RATE_LIMITED').title).toBe('Trop de demandes');
  });
});
