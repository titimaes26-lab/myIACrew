import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useConversation } from './useConversation';

const runningEntry = {
  id: 7, user_request: 'Ajoute un panier', workflow: 'FEATURE', clarifications: null, result: null, status: 'running',
  conversation_id: 3, repo_owner: null, repo_name: null, created_at: '2026-10-04T10:00:00Z', updated_at: '2026-10-04T10:00:00Z',
  api_calls_count: null, rate_limit_hits: null, total_wait_time_seconds: null, current_step: 'qa', error_code: null, error_retryable: null,
};

let urls: string[];
let executionResponse: () => Response;

beforeEach(() => {
  urls = [];
  executionResponse = () => new Response(JSON.stringify({ ...runningEntry, status: 'success', result: '## QA\n\nGO' }), { status: 200 });
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    urls.push(url);
    const json = (body: unknown, status = 200) => Promise.resolve(new Response(JSON.stringify(body), { status }));
    if (url.endsWith('/messages')) return json([runningEntry]);
    if (url.includes('/progress')) return json({ id: 7, status: 'success', current_step: null });
    if (url.endsWith('/api/executions/7')) return Promise.resolve(executionResponse());
    return json({}, 404);
  }));
});

afterEach(() => { vi.unstubAllGlobals(); });

async function openRunningConversation() {
  const hook = renderHook(() => useConversation('t', ''));
  await act(async () => { await hook.result.current.loadConversation(3); });
  expect(hook.result.current.turns).toHaveLength(1);
  return hook;
}

describe('useConversation — resynchronisation d’un tour en cours', () => {
  it('relit seulement l’exécution suivie, pas toute la conversation, puis affiche son résultat', async () => {
    const { result } = await openRunningConversation();
    await waitFor(() => expect(result.current.turns[0].status).toBe('success'));
    expect(result.current.turns[0].result).toContain('GO');
    expect(urls.filter((url) => url.endsWith('/api/executions/7'))).toHaveLength(1);
    // /messages n'a servi qu'à ouvrir la conversation, jamais à la resynchronisation.
    expect(urls.filter((url) => url.endsWith('/messages'))).toHaveLength(1);
  });

  it('une exécution disparue de la base (404) sort le tour de l’état « en cours »', async () => {
    executionResponse = () => new Response(JSON.stringify({ detail: 'Exécution introuvable.', code: 'NOT_FOUND', retryable: false }), { status: 404 });
    const { result } = await openRunningConversation();
    await waitFor(() => expect(result.current.turns[0].status).toBe('cancelled'));
    expect(result.current.turns[0].result).toMatch(/n'existe plus/);
  });

  it('un 404 du proxy (redéploiement) ne déclare pas l’exécution supprimée', async () => {
    executionResponse = () => new Response('Not Found', { status: 404 });
    const { result } = await openRunningConversation();
    await new Promise((resolve) => setTimeout(resolve, 100));
    expect(result.current.turns[0].status).toBe('running');
  });

  it('une erreur serveur ne casse rien : le tour reste en cours et sera retenté', async () => {
    executionResponse = () => new Response('{}', { status: 500 });
    const { result } = await openRunningConversation();
    await waitFor(() => expect(urls.some((url) => url.endsWith('/api/executions/7'))).toBe(true));
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(result.current.turns[0].status).toBe('running');
  });
});
