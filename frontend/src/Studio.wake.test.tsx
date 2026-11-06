import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';

vi.mock('./utils/serverWake', async (importOriginal) => ({ ...(await importOriginal<typeof import('./utils/serverWake')>()), wakeServer: vi.fn() }));
vi.mock('./supabaseClient', () => ({ supabase: { auth: { signOut: vi.fn() } } }));
vi.mock('./hooks/useConversation', () => ({
  useConversation: () => ({
    turns: [], sending: false, hasRunningTurn: false, error: null, connectionLost: false, prepareRetry: vi.fn(), pendingClarification: null,
    pendingLaunch: null, confirmBeforeLaunch: false, setConfirmBeforeLaunch: vi.fn(), confirmLaunch: vi.fn(), cancelLaunch: vi.fn(),
    workflowType: 'AUTO', setWorkflowType: vi.fn(), conversationResetSignal: 0, sendMessage: vi.fn(), cancelSending: vi.fn(),
    startNewConversation: vi.fn(), loadConversation: vi.fn(),
  }),
}));

import Studio from './Studio';
import { wakeServer, waitForServer } from './utils/serverWake';

afterEach(() => vi.clearAllMocks());

describe('Studio : réveil du serveur à l\'ouverture', () => {
  it("envoie UN sondage de réveil au montage, sur l'adresse de l'API, et pas à chaque rendu", () => {
    const { rerender } = render(<Studio accessToken="t" userEmail="a@b.c" />);
    rerender(<Studio accessToken="t2" userEmail="a@b.c" />);
    expect(wakeServer).toHaveBeenCalledTimes(1);
    expect(wakeServer).toHaveBeenCalledWith('http://localhost:8000');
  });

  it("affiche le bandeau « le serveur se réveille » pendant l'attente", async () => {
    vi.useFakeTimers();
    let up = false;                                                              // les autres appels du Studio ne doivent pas consommer la panne simulée
    vi.stubGlobal('fetch', vi.fn(async () => { if (!up) throw new TypeError('x'); return new Response('{}', { status: 200 }); }));
    render(<Studio accessToken="t" userEmail="a@b.c" />);
    expect(screen.queryByText(/Le serveur se réveille/)).toBeNull();
    let pending: Promise<boolean> = Promise.resolve(false);
    await act(async () => { pending = waitForServer('http://api.test'); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByText(/Le serveur se réveille/)).toBeInTheDocument();
    up = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    await pending;
    expect(screen.queryByText(/Le serveur se réveille/)).toBeNull();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
});
