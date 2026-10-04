import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useConversation } from './useConversation';

const repo = { owner: 'o', name: 'r', branch: 'main' };

interface Call { url: string; body: Record<string, unknown> | null }
let calls: Call[];
let report: Record<string, unknown>;

function install() {
  calls = [];
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
    const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    if (url.includes('/api/qualify')) return json(report);
    if (url.includes('/api/execute')) return json({ status: 'running', id: 7, conversation_id: 3 });
    if (url.includes('/progress')) return json({ id: 7, status: 'running', current_step: null });
    return json([]);
  }));
}

const executeCalls = () => calls.filter((call) => call.url.includes('/api/execute'));

beforeEach(() => {
  window.localStorage.clear();
  report = { summary: 'Un bouton', request_type: 'FEATURE', scope: 'PETIT', confidence: 0.9, is_clear: true, questions: [] };
  install();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function send(result: { current: ReturnType<typeof useConversation> }, text = 'Ajoute un bouton') {
  await act(async () => { await result.current.sendMessage(text, repo); });
}

describe('useConversation — aperçu avant lancement', () => {
  it('propose le lancement au lieu d’exécuter, avec type, taille et repository', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    await send(result);
    expect(executeCalls()).toHaveLength(0);
    const turn = result.current.turns[0];
    expect(turn.status).toBe('clarifying');
    expect(turn.launchPreview).toEqual({ workflow: 'FEATURE', scope: 'PETIT', repoLabel: 'o/r · main' });
    expect(result.current.pendingLaunch).not.toBeNull();
    expect(result.current.sending).toBe(false);
  });

  it('« Lancer » exécute avec le type et la taille choisis, puis suit le tour', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    await send(result);
    await act(async () => { await result.current.confirmLaunch({ workflow: 'FEATURE', scope: 'GRAND' }); });
    expect(executeCalls()).toHaveLength(1);
    expect(executeCalls()[0].body).toMatchObject({ user_request: 'Ajoute un bouton', target_workflow: 'FEATURE', scope: 'GRAND', repo_owner: 'o' });
    await waitFor(() => expect(result.current.turns[0].id).toBe(7));
    expect(result.current.turns[0]).toMatchObject({ status: 'running', scope: 'GRAND' });
    expect(result.current.turns[0].launchPreview).toBeUndefined();
    expect(result.current.pendingLaunch).toBeNull();
  });

  it('un autre type choisi dans l’aperçu remplace celui de la qualification et n’envoie pas de taille', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    await send(result);
    await act(async () => { await result.current.confirmLaunch({ workflow: 'BUGFIX', scope: 'PETIT' }); });
    expect(executeCalls()[0].body).toMatchObject({ target_workflow: 'BUGFIX' });
    expect(executeCalls()[0].body).not.toHaveProperty('scope');
  });

  it('« Annuler » n’exécute rien et marque le tour annulé', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    await send(result);
    act(() => { result.current.cancelLaunch(); });
    expect(executeCalls()).toHaveLength(0);
    expect(result.current.turns[0]).toMatchObject({ status: 'cancelled' });
    expect(result.current.pendingLaunch).toBeNull();
  });

  it('une nouvelle demande tapée remplace l’aperçu en attente', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    await send(result, 'première demande');
    await send(result, 'seconde demande');
    expect(result.current.turns.map((turn) => turn.status)).toEqual(['cancelled', 'clarifying']);
    expect(result.current.turns[0].result).toMatch(/Remplacée/);
    expect(result.current.pendingLaunch?.request).toBe('seconde demande');
  });

  it('décocher la confirmation lance directement, et le choix est mémorisé', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    act(() => { result.current.setConfirmBeforeLaunch(false); });
    expect(window.localStorage.getItem('studio.confirmLaunch')).toBe('off');
    await send(result);
    expect(executeCalls()).toHaveLength(1);
    expect(executeCalls()[0].body).toMatchObject({ target_workflow: 'FEATURE', scope: 'PETIT' });
    expect(result.current.pendingLaunch).toBeNull();

    const reloaded = renderHook(() => useConversation('t', ''));
    expect(reloaded.result.current.confirmBeforeLaunch).toBe(false);
  });

  it('une reprise (« Relancer ») ne repasse pas par l’aperçu et garde la taille du tour en échec', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    act(() => {
      result.current.prepareRetry({ id: 5, status: 'failed', userMessage: 'Ajoute un bouton', scope: 'GRAND', createdAt: new Date().toISOString() });
    });
    await send(result);
    expect(executeCalls()).toHaveLength(1);
    expect(executeCalls()[0].body).toMatchObject({ resume_from_execution_id: 5, scope: 'GRAND' });
  });

  it('la préférence reste « confirmer » si le stockage est indisponible', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('bloqué'); });
    const { result } = renderHook(() => useConversation('t', ''));
    expect(result.current.confirmBeforeLaunch).toBe(true);
    vi.restoreAllMocks();
  });

  it('une erreur du serveur au lancement marque le tour en échec, sans bloquer la saisie', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    await send(result);
    (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify({ detail: 'Quota épuisé', code: 'QUOTA_EXHAUSTED', retryable: true }), { status: 429 })));
    await act(async () => { await result.current.confirmLaunch({ workflow: 'FEATURE', scope: 'PETIT' }); });
    expect(result.current.turns[0]).toMatchObject({ status: 'failed', errorCode: 'QUOTA_EXHAUSTED' });
    expect(result.current.sending).toBe(false);
  });
});
