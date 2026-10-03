import { toDisplayedError } from '../utils/errors';
import { useEffect, useMemo, useState } from 'react';
import { apiClient } from '../api';
import type { MetricsSummary, MetricsWorkflowFilter } from '../types';

interface Loaded {
  key: string;
  data: MetricsSummary | null;
  error: string | null;
}

// Les données déjà reçues restent affichées pendant un rechargement (le graphique garde son cadre,
// atténué) : `loading` se déduit de la clé de la dernière réponse, sans setState synchrone dans l'effet.
export function useMetricsSummary(apiUrl: string, accessToken: string, days: number, workflow: MetricsWorkflowFilter) {
  const key = `${days}|${workflow}`;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const api = useMemo(() => apiClient(apiUrl, accessToken), [apiUrl, accessToken]);

  useEffect(() => {
    const controller = new AbortController();
    api.getMetricsSummary(days, workflow, controller.signal)
      .then((data) => setLoaded({ key, data, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded((previous) => ({
          key,
          data: previous?.data ?? null,
          error: toDisplayedError(err, 'Impossible de charger les métriques.').message,
        }));
      });
    return () => controller.abort();
  }, [api, days, workflow, key]);

  return { data: loaded?.data ?? null, error: loaded?.error ?? null, loading: loaded?.key !== key };
}
