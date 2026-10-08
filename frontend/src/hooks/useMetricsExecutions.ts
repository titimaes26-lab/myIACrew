import { useEffect, useMemo, useState } from 'react';
import { apiClient } from '../api';
import type { ExecutionSortKey, ExecutionsPage, MetricsWorkflowFilter } from '../types';
import { toDisplayedError } from '../utils/errors';

export type ExecutionStatusFilter = 'all' | 'success' | 'failed';

export interface ExecutionsQuery {
  days: number;
  workflow: MetricsWorkflowFilter;
  status: ExecutionStatusFilter;
  sort: ExecutionSortKey;
  order: 'asc' | 'desc';
  limit: number;
  offset: number;
}

interface Loaded {
  key: string;
  page: ExecutionsPage | null;
  error: string | null;
}

// Comme useMetricsSummary : la page déjà reçue reste affichée (atténuée) pendant un rechargement ;
// `loading` se déduit de la clé de la dernière réponse.
export function useMetricsExecutions(apiUrl: string, accessToken: string, query: ExecutionsQuery) {
  const { days, workflow, status, sort, order, limit, offset } = query;
  const key = `${days}|${workflow}|${status}|${sort}|${order}|${limit}|${offset}`;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const api = useMemo(() => apiClient(apiUrl, accessToken), [apiUrl, accessToken]);

  useEffect(() => {
    const controller = new AbortController();
    api.getMetricsExecutions({ days, workflow, status, sort, order, limit, offset }, controller.signal)
      .then((page) => setLoaded({ key, page, error: null }))
      .catch((err: unknown) => {
        if (controller.signal.aborted) return;
        setLoaded((previous) => ({
          key, page: previous?.page ?? null, error: toDisplayedError(err, 'Impossible de charger les exécutions.').message,
        }));
      });
    return () => controller.abort();
  }, [api, days, workflow, status, sort, order, limit, offset, key]);

  return { page: loaded?.page ?? null, error: loaded?.error ?? null, loading: loaded?.key !== key };
}
