import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from './api';
import { WAKE_TIMEOUT_MS } from './utils/serverWake';

function mockFetch(status: number, body: unknown) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(typeof body === 'string' ? body : JSON.stringify(body), { status })));
}

describe('getExecution', () => {
  afterEach(() => vi.unstubAllGlobals());

  it("renvoie null pour le 404 NOT_FOUND de l'application", async () => {
    mockFetch(404, { detail: 'Exécution introuvable.', code: 'NOT_FOUND', retryable: false });
    await expect(apiClient('http://x', 't').getExecution(1)).resolves.toBeNull();
  });

  it('lève une erreur pour un 404 du proxy (corps non reconnu)', async () => {
    mockFetch(404, 'Not Found');
    await expect(apiClient('http://x', 't').getExecution(1)).rejects.toMatchObject({ status: 404 });
  });

  it("renvoie l'exécution quand elle existe", async () => {
    mockFetch(200, { id: 1, status: 'success' });
    await expect(apiClient('http://x', 't').getExecution(1)).resolves.toMatchObject({ id: 1 });
  });
});

describe('réveil du serveur (hébergement gratuit)', () => {
  const sleeping = () => new TypeError('Failed to fetch');
  const response = (body: unknown = []) => new Response(JSON.stringify(body), { status: 200 });

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it('une lecture est rejouée UNE fois quand le serveur dormait puis répond', async () => {
    // 1 requête (échec), 2 sondages (échec, échec), 1 sondage (ok), 1 reprise (ok)
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(sleeping()).mockRejectedValueOnce(sleeping()).mockRejectedValueOnce(sleeping())
      .mockResolvedValueOnce(response({})).mockResolvedValueOnce(response([{ id: 1 }]));
    vi.stubGlobal('fetch', fetchMock);
    const result = apiClient('http://x', 't').listHistory();
    await vi.advanceTimersByTimeAsync(3_000);
    await expect(result).resolves.toEqual([{ id: 1 }]);
    expect(fetchMock.mock.calls.map((c) => c[0])).toEqual(['http://x/api/history', 'http://x/', 'http://x/', 'http://x/', 'http://x/api/history']);
  });

  it("n'est JAMAIS rejouée pour /api/execute (risque de double exécution) et ne sonde rien", async () => {
    const fetchMock = vi.fn().mockRejectedValue(sleeping());
    vi.stubGlobal('fetch', fetchMock);
    await expect(apiClient('http://x', 't').execute({ a: 1 })).rejects.toMatchObject({ code: 'NETWORK_ERROR', retryable: true, message: expect.stringContaining('Impossible de joindre') });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("garde l'erreur actuelle sans rejouer quand le serveur répond déjà", async () => {
    const fetchMock = vi.fn().mockRejectedValueOnce(sleeping()).mockResolvedValue(response({}));
    vi.stubGlobal('fetch', fetchMock);
    await expect(apiClient('http://x', 't').listHistory()).rejects.toMatchObject({ code: 'NETWORK_ERROR', message: expect.stringContaining('Impossible de joindre') });
    expect(fetchMock).toHaveBeenCalledTimes(2);                                      // la requête + UN sondage, aucune reprise
  });

  it('échoue avec un message de réveil quand le serveur ne répond pas dans les 90 s, sans seconde reprise', async () => {
    const fetchMock = vi.fn().mockRejectedValue(sleeping());
    vi.stubGlobal('fetch', fetchMock);
    const result = apiClient('http://x', 't').listHistory();
    const caught = result.catch((e) => e);
    await vi.advanceTimersByTimeAsync(WAKE_TIMEOUT_MS + 6_000);
    expect(await caught).toMatchObject({ code: 'NETWORK_ERROR', retryable: true, message: expect.stringContaining('réveille') });
    expect(fetchMock.mock.calls.filter((c) => c[0] === 'http://x/api/history')).toHaveLength(1);
  });

  it("l'annulation relance AbortError telle quelle, sans sonder", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new DOMException('x', 'AbortError'));
    vi.stubGlobal('fetch', fetchMock);
    await expect(apiClient('http://x', 't').qualify('a', null, false)).rejects.toMatchObject({ name: 'AbortError' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('une erreur HTTP (le serveur a répondu) ne déclenche aucune attente', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: 'nope' }), { status: 500 }));
    vi.stubGlobal('fetch', fetchMock);
    await expect(apiClient('http://x', 't').listHistory()).rejects.toMatchObject({ status: 500 });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("le délai d'attente du réveil est de 90 s (valeur littérale : un changement doit être voulu)", () => {
    expect(WAKE_TIMEOUT_MS).toBe(90_000);
  });

  it('la qualification (sans effet de bord) est rejouée comme une lecture', async () => {
    const report = { workflow_type: 'FEATURE' };
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(sleeping()).mockRejectedValueOnce(sleeping()).mockResolvedValueOnce(response({})).mockResolvedValueOnce(response(report));
    vi.stubGlobal('fetch', fetchMock);
    const result = apiClient('http://x', 't').qualify('a', null, false);
    await vi.advanceTimersByTimeAsync(3_000);
    await expect(result).resolves.toEqual(report);
    expect(fetchMock.mock.calls.filter((c) => c[0] === 'http://x/api/qualify')).toHaveLength(2);
  });

  it("une annulation pendant la reprise est relancée telle quelle (pas transformée en erreur réseau)", async () => {
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(sleeping()).mockRejectedValueOnce(sleeping()).mockResolvedValueOnce(response({}))
      .mockRejectedValueOnce(new DOMException('x', 'AbortError'));
    vi.stubGlobal('fetch', fetchMock);
    const result = apiClient('http://x', 't').listHistory();
    const caught = result.catch((e) => e);
    await vi.advanceTimersByTimeAsync(3_000);
    expect(await caught).toMatchObject({ name: 'AbortError' });
  });
});
