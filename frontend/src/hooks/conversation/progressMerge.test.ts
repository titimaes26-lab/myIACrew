import { describe, expect, it } from 'vitest';
import { applyProgress, mergeResyncedTurns } from './progressMerge';
import { entry, turn } from './fixtures';

const progress = (overrides = {}) => ({ id: 7, status: 'running', current_step: 'diagnostic', completed_agents: {}, ...overrides });

describe('applyProgress', () => {
  it('pose l’étape courante et la position dans la file sur les tours en cours', () => {
    const [updated] = applyProgress([turn()], progress({ current_step: 'queued', queue_ahead: 2 }));
    expect(updated).toMatchObject({ currentStep: 'queued', queueAhead: 2 });
  });

  it('ajoute les agents terminés sans effacer ceux déjà connus', () => {
    const [updated] = applyProgress(
      [turn({ currentStep: 'diagnostic', completedAgents: { Architecte: '## Architecte' } })],
      progress({ completed_agents: { Analyste: '## Analyste' } }),
    );
    expect(updated.completedAgents).toEqual({ Architecte: '## Architecte', Analyste: '## Analyste' });
  });

  it('ramène les agents connus au total annoncé par le serveur quand il en compte moins', () => {
    const local = { Architecte: '## Architecte', Analyste: '## Analyste', Testeur: '## Testeur' };
    const [updated] = applyProgress([turn({ currentStep: 'diagnostic', completedAgents: local })], progress({ completed_count: 1 }));
    expect(updated.completedAgents).toEqual({ Architecte: '## Architecte' });
  });

  it('garde les agents connus quand le total du serveur est cohérent ou absent', () => {
    const local = { Architecte: '## Architecte', Analyste: '## Analyste' };
    const base = turn({ currentStep: 'diagnostic', completedAgents: local });
    for (const extra of [{ completed_count: 2 }, { completed_count: 5 }, {}]) {
      expect(applyProgress([base], progress(extra))[0]).toBe(base);
    }
  });

  it('laisse intacts (même objet) les tours sans changement et les tours terminés', () => {
    const unchanged = turn({ currentStep: 'diagnostic', queueAhead: null });
    const done = turn({ id: 8, status: 'success' });
    const result = applyProgress([unchanged, done], progress());
    expect(result[0]).toBe(unchanged);
    expect(result[1]).toBe(done);
  });

  it('remet la position à null quand elle disparaît', () => {
    const [updated] = applyProgress([turn({ currentStep: 'queued', queueAhead: 1 })], progress({ current_step: 'queued', queue_ahead: null }));
    expect(updated.queueAhead).toBeNull();
  });
});

describe('mergeResyncedTurns', () => {
  const ids = (...values: number[]) => new Set(values);

  it('remplace un tour en cours par son état serveur en gardant son createdAt local', () => {
    const local = turn({ createdAt: '2026-10-04T10:00:00Z' });
    const { turns, failedMessage } = mergeResyncedTurns([local], ids(7), [entry({ status: 'success', result: '## QA\n\nGO', created_at: '2026-10-04T10:00:09Z' })]);
    expect(turns[0]).toMatchObject({ status: 'success', result: '## QA\n\nGO', createdAt: '2026-10-04T10:00:00Z' });
    expect(failedMessage).toBeNull();
  });

  it('renvoie le message d’un échec découvert pour la bannière d’erreur', () => {
    const { turns, failedMessage } = mergeResyncedTurns([turn()], ids(7), [entry({ status: 'failed', result: 'Échec à l\'étape 2/4', error_code: 'GUARDRAIL_FAILED' })]);
    expect(turns[0]).toMatchObject({ status: 'failed', errorCode: 'GUARDRAIL_FAILED' });
    expect(failedMessage).toBe('Échec à l\'étape 2/4');
    expect(mergeResyncedTurns([turn()], ids(7), [entry({ status: 'failed', result: null })]).failedMessage).toBe('Une erreur est survenue.');
  });

  it('marque annulé un tour dont l’exécution a disparu de la base', () => {
    const { turns } = mergeResyncedTurns([turn()], ids(7), []);
    expect(turns[0]).toMatchObject({ status: 'cancelled' });
    expect(turns[0].result).toMatch(/n'existe plus en base/);
  });

  it('ne touche ni aux tours non relus, ni aux tours à identifiant temporaire, ni aux tours déjà terminés', () => {
    const notFetched = turn({ id: 9 });
    const temporary = turn({ id: 'temp-1-1' });
    const finished = turn({ id: 7, status: 'success' });
    const { turns } = mergeResyncedTurns([notFetched, temporary, finished], ids(7), []);
    expect(turns[0]).toBe(notFetched);
    expect(turns[1]).toBe(temporary);
    expect(turns[2]).toBe(finished);
  });
});
