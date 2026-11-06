import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import ServerWakingBanner from './ServerWakingBanner';
import { waitForServer } from '../utils/serverWake';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('ServerWakingBanner', () => {
  it("n'affiche rien quand le serveur n'est pas en réveil", () => {
    const { container } = render(<ServerWakingBanner />);
    expect(container).toBeEmptyDOMElement();
  });

  it("affiche l'information (role=status) pendant l'attente puis la retire", async () => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('x')).mockResolvedValue(new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })));
    render(<ServerWakingBanner />);
    let pending: Promise<boolean> = Promise.resolve(false);
    await act(async () => { pending = waitForServer('http://api.test'); await vi.advanceTimersByTimeAsync(0); });
    expect(screen.getByRole('status')).toHaveTextContent(/Le serveur se réveille/);
    await act(async () => { await vi.advanceTimersByTimeAsync(3_000); });
    await expect(pending).resolves.toBe(true);
    expect(screen.queryByRole('status')).toBeNull();
  });
});
