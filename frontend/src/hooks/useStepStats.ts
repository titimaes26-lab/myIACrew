import { useContext, useEffect, useState } from 'react';
import { apiClient } from '../api';
import type { MetricsWorkflowFilter } from '../types';
import type { StepStats } from '../utils/estimateRemaining';
import { MetricsApiContext, type MetricsApiConfig } from './metricsApi';

const WORKFLOWS = new Set<string>(['ANALYSE_ONLY', 'BUGFIX', 'FEATURE', 'DESIGN_AND_DEV']);
const PERIOD_DAYS = 30;
// Fin du jeton d'accès dans la clé du cache : les mesures sont propres à chaque utilisateur, un autre compte connecté
// dans le même onglet ne doit pas lire celles du précédent (le jeton change aussi à chaque renouvellement : une relecture).
const TOKEN_TAIL_CHARS = 16;
const cacheKey = (config: MetricsApiConfig, workflow: string) => `${config.apiUrl}|${config.accessToken.slice(-TOKEN_TAIL_CHARS)}|${workflow}`;
const SUCCESS_TTL_MS = 10 * 60 * 1000;
// Un échec (serveur injoignable, aucune donnée) n'est pas retenté à chaque rendu : on réessaie plus tard.
const FAILURE_TTL_MS = 60 * 1000;

interface CacheEntry {
  at: number;
  stats: StepStats | null;
}

// Cache de module : les médianes par étape changent lentement, inutile de les relire à chaque tour ni à chaque
// remontage de la frise ; les requêtes simultanées pour un même workflow sont fusionnées.
const cache = new Map<string, CacheEntry>();
const inflight = new Map<string, Promise<StepStats | null>>();

export function clearStepStatsCache(): void {
  cache.clear();
  inflight.clear();
}

export async function loadStepStats(config: MetricsApiConfig, workflow: string): Promise<StepStats | null> {
  if (!WORKFLOWS.has(workflow)) return null;
  const key = cacheKey(config, workflow);
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < (hit.stats ? SUCCESS_TTL_MS : FAILURE_TTL_MS)) return hit.stats;
  const pending = inflight.get(key);
  if (pending) return pending;

  const request = apiClient(config.apiUrl, config.accessToken)
    .getMetricsSummary(PERIOD_DAYS, workflow as MetricsWorkflowFilter)
    .then((summary) => {
      const stats: StepStats = {};
      for (const agent of summary.agents) {
        if (agent.duration_p50 != null) stats[agent.agent] = { p50: agent.duration_p50, runs: agent.runs };
      }
      cache.set(key, { at: Date.now(), stats });
      return stats;
    })
    .catch(() => {
      cache.set(key, { at: Date.now(), stats: null });
      return null;
    })
    .finally(() => { inflight.delete(key); });
  inflight.set(key, request);
  return request;
}

// Médianes par étape du workflow (30 derniers jours), ou null tant qu'elles ne sont pas lues ou indisponibles.
// Sans fournisseur d'API (MetricsApiContext absent), jamais de requête.
export function useStepStats(workflow: string | undefined, enabled: boolean): StepStats | null {
  const config = useContext(MetricsApiContext);
  const [loaded, setLoaded] = useState<{ key: string; stats: StepStats | null } | null>(null);
  const apiUrl = config?.apiUrl;
  const accessToken = config?.accessToken;

  useEffect(() => {
    if (!enabled || !workflow || apiUrl === undefined || accessToken === undefined) return;
    let active = true;
    loadStepStats({ apiUrl, accessToken }, workflow).then((stats) => {
      if (active) setLoaded({ key: cacheKey({ apiUrl, accessToken }, workflow), stats });
    });
    return () => { active = false; };
  }, [enabled, workflow, apiUrl, accessToken]);

  const wanted = workflow && apiUrl !== undefined && accessToken !== undefined ? cacheKey({ apiUrl, accessToken }, workflow) : null;
  return loaded && wanted && loaded.key === wanted ? loaded.stats : null;
}
