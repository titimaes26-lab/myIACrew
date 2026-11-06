import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PROBE_TIMEOUT_MS, WAKE_INTERVAL_MS, WAKE_TIMEOUT_MS, isServerWaking, probeServer, recoverFromSleep, waitForServer, wakeServer } from './serverWake';

const API = 'http://api.test';
const ok = () => new Response('{}', { status: 200 });

beforeEach(() => vi.useFakeTimers());
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

describe('probeServer', () => {
  it('est vrai pour un 2xx sur la racine, faux sinon, sans jamais lever', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(ok()).mockResolvedValueOnce(new Response('', { status: 503 })).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    await expect(probeServer(API)).resolves.toBe(true);
    await expect(probeServer(API)).resolves.toBe(false);
    await expect(probeServer(API)).resolves.toBe(false);
    expect(fetchMock.mock.calls[0][0]).toBe(`${API}/`);
  });

  it("abandonne un sondage qui ne répond jamais après le délai du sondage", async () => {
    vi.stubGlobal('fetch', vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
      init.signal?.addEventListener('abort', () => reject(new DOMException('x', 'AbortError')));
    })));
    const result = probeServer(API);
    await vi.advanceTimersByTimeAsync(PROBE_TIMEOUT_MS + 1);
    await expect(result).resolves.toBe(false);
  });

  it('wakeServer envoie un seul sondage et ignore son échec', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('Failed to fetch'));
    vi.stubGlobal('fetch', fetchMock);
    wakeServer(API);
    await vi.advanceTimersByTimeAsync(10);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('waitForServer', () => {
  it("sonde toutes les 3 s jusqu'à la réponse du serveur, puis masque l'état « en réveil »", async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(new TypeError('x')).mockRejectedValueOnce(new TypeError('x')).mockResolvedValue(ok());
    vi.stubGlobal('fetch', fetchMock);
    const result = waitForServer(API);
    await vi.advanceTimersByTimeAsync(0);
    expect(isServerWaking()).toBe(true);
    await vi.advanceTimersByTimeAsync(WAKE_INTERVAL_MS * 2);
    await expect(result).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(isServerWaking()).toBe(false);
  });

  it('renonce après le délai maximal de 90 s (faux) et masque l\'état', async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError('x'));
    vi.stubGlobal('fetch', fetchMock);
    const result = waitForServer(API);
    await vi.advanceTimersByTimeAsync(WAKE_TIMEOUT_MS + WAKE_INTERVAL_MS);
    await expect(result).resolves.toBe(false);
    expect(isServerWaking()).toBe(false);
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(WAKE_TIMEOUT_MS / WAKE_INTERVAL_MS - 1);
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(WAKE_TIMEOUT_MS / WAKE_INTERVAL_MS + 1);
  });

  it("l'annulation interrompt l'attente avec AbortError et masque l'état", async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('x')));
    const controller = new AbortController();
    const result = waitForServer(API, { signal: controller.signal });
    const caught = result.catch((e) => e);
    await vi.advanceTimersByTimeAsync(WAKE_INTERVAL_MS);
    controller.abort();
    await vi.advanceTimersByTimeAsync(1);
    expect(await caught).toMatchObject({ name: 'AbortError' });
    expect(isServerWaking()).toBe(false);
  });
});

describe('recoverFromSleep', () => {
  it("« awake » quand le serveur répond déjà (rien à rejouer)", async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(ok()));
    await expect(recoverFromSleep(API)).resolves.toBe('awake');
  });

  it("« woke » quand le serveur dormait puis répond", async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValueOnce(new TypeError('x')).mockRejectedValueOnce(new TypeError('x')).mockResolvedValue(ok()));
    const result = recoverFromSleep(API);
    await vi.advanceTimersByTimeAsync(WAKE_INTERVAL_MS * 3);
    await expect(result).resolves.toBe('woke');
  });

  it("« still-down » quand il ne répond pas dans les 90 s", async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('x')));
    const result = recoverFromSleep(API);
    await vi.advanceTimersByTimeAsync(WAKE_TIMEOUT_MS + WAKE_INTERVAL_MS * 2);
    await expect(result).resolves.toBe('still-down');
  });
});
