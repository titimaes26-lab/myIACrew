import { afterEach, describe, expect, it, vi } from 'vitest';
import { CONFIRM_LAUNCH_KEY, historyEntryToTurn, isAbortError, readConfirmLaunch } from './turnMapping';
import { entry } from './fixtures';

describe('historyEntryToTurn', () => {
  it('reprend les champs de la ligne serveur, sans date de mise à jour tant que l’exécution tourne', () => {
    const running = historyEntryToTurn(entry({ current_step: 'qa', scope: 'PETIT' }));
    expect(running).toMatchObject({ id: 7, userMessage: 'Ajoute un panier', status: 'running', workflow: 'FEATURE', currentStep: 'qa', scope: 'PETIT' });
    expect(running.updatedAt).toBeUndefined();
  });

  it('garde la date de mise à jour, la cause et le résultat d’un échec', () => {
    const failed = historyEntryToTurn(entry({ status: 'failed', result: 'boum', error_code: 'QUOTA_EXHAUSTED', error_retryable: true }));
    expect(failed).toMatchObject({ status: 'failed', result: 'boum', errorCode: 'QUOTA_EXHAUSTED', errorRetryable: true, updatedAt: '2026-10-04T10:05:00Z' });
  });
});

describe('isAbortError', () => {
  it('ne reconnaît que l’annulation d’une requête', () => {
    expect(isAbortError(new DOMException('annulé', 'AbortError'))).toBe(true);
    expect(isAbortError(new DOMException('autre', 'NetworkError'))).toBe(false);
    expect(isAbortError(new Error('AbortError'))).toBe(false);
  });
});

describe('readConfirmLaunch', () => {
  afterEach(() => { vi.unstubAllGlobals(); window.localStorage.clear(); });

  it('confirme par défaut, sauf choix explicite « off »', () => {
    expect(readConfirmLaunch()).toBe(true);
    window.localStorage.setItem(CONFIRM_LAUNCH_KEY, 'off');
    expect(readConfirmLaunch()).toBe(false);
    window.localStorage.setItem(CONFIRM_LAUNCH_KEY, 'on');
    expect(readConfirmLaunch()).toBe(true);
  });

  it('retombe sur « confirmer » quand le stockage est indisponible', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('stockage bloqué'); });
    expect(readConfirmLaunch()).toBe(true);
    vi.restoreAllMocks();
  });
});
