import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from './api';

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
