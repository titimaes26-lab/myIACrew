import { renderHook, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MetricsApiContext } from './metricsApi';
import { clearStepStatsCache, loadStepStats, useStepStats } from './useStepStats';

const config = { apiUrl: '', accessToken: 't' };
const summary = {
  agents: [
    { agent: 'diagnostic', runs: 12, duration_p50: 100 },
    { agent: 'qa', runs: 12, duration_p50: 20 },
    { agent: 'system', runs: 12, duration_p50: null },
  ],
};
let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  clearStepStatsCache();
  fetchMock = vi.fn(() => Promise.resolve(new Response(JSON.stringify(summary), { status: 200 })));
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('loadStepStats', () => {
  it('lit les médianes par étape, sans celles qui n’ont aucune durée', async () => {
    expect(await loadStepStats(config, 'FEATURE')).toEqual({ diagnostic: { p50: 100, runs: 12 }, qa: { p50: 20, runs: 12 } });
    expect(String(fetchMock.mock.calls[0][0])).toContain('workflow=FEATURE');
  });

  it('mémorise le résultat et fusionne les requêtes simultanées', async () => {
    await Promise.all([loadStepStats(config, 'FEATURE'), loadStepStats(config, 'FEATURE')]);
    await loadStepStats(config, 'FEATURE');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await loadStepStats(config, 'BUGFIX');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('relit après 10 minutes', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await loadStepStats(config, 'FEATURE');
    vi.setSystemTime(Date.now() + 11 * 60 * 1000);
    await loadStepStats(config, 'FEATURE');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('ne cherche rien pour un workflow inconnu', async () => {
    expect(await loadStepStats(config, 'AUTRE')).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('renvoie null en cas d’erreur et ne réessaie pas aussitôt', async () => {
    fetchMock.mockImplementation(() => Promise.resolve(new Response('{}', { status: 500 })));
    expect(await loadStepStats(config, 'FEATURE')).toBeNull();
    expect(await loadStepStats(config, 'FEATURE')).toBeNull();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

describe('useStepStats', () => {
  const wrapper = ({ children }: { children: ReactNode }) => <MetricsApiContext.Provider value={config}>{children}</MetricsApiContext.Provider>;

  it('renvoie les médianes une fois lues', async () => {
    const { result } = renderHook(() => useStepStats('FEATURE', true), { wrapper });
    expect(result.current).toBeNull();
    await waitFor(() => expect(result.current).not.toBeNull());
    expect(result.current?.diagnostic.p50).toBe(100);
  });

  it('ne fait aucune requête sans fournisseur d’API ou tant que c’est désactivé', async () => {
    renderHook(() => useStepStats('FEATURE', true));
    renderHook(() => useStepStats('FEATURE', false), { wrapper });
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
