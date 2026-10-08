import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useConversation } from './useConversation';

const runningEntry = {
  id: 7, user_request: 'Ajoute un panier', workflow: 'FEATURE', clarifications: null, result: null, status: 'running',
  conversation_id: 3, repo_owner: null, repo_name: null, created_at: '2026-10-04T10:00:00Z', updated_at: '2026-10-04T10:00:00Z',
  api_calls_count: null, rate_limit_hits: null, total_wait_time_seconds: null, current_step: 'qa', error_code: null, error_retryable: null,
};

let progressUrls: string[];

beforeEach(() => {
  vi.useFakeTimers();
  progressUrls = [];
  vi.stubGlobal('fetch', vi.fn((url: string) => {
    const json = (body: unknown) => Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
    if (url.endsWith('/messages')) return json([runningEntry]);
    if (url.includes('/progress')) {
      progressUrls.push(url);
      // Le serveur ne renvoie que les agents au-delà de `known` : un seul, au premier sondage.
      const fresh = progressUrls.length === 1 ? { 'Agent Alpha': '## Agent Alpha\n\nun' } : {};
      return json({ id: 7, status: 'running', current_step: 'qa', completed_agents: fresh, completed_count: 1, queue_ahead: null });
    }
    return json({});
  }));
});

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('useConversation — sondage incrémental des agents terminés', () => {
  it('redemande seulement les agents suivants et conserve ceux déjà affichés', async () => {
    const { result } = renderHook(() => useConversation('t', ''));
    await act(async () => { await result.current.loadConversation(3); });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    expect(progressUrls[0]).toMatch(/\/progress\?known=0$/);
    expect(Object.keys(result.current.turns[0].completedAgents ?? {})).toEqual(['Agent Alpha']);

    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(progressUrls[1]).toMatch(/\/progress\?known=1$/);
    expect(Object.keys(result.current.turns[0].completedAgents ?? {})).toEqual(['Agent Alpha']);
  });
});
