import type { DailyMetricsRow } from '../types';
import { formatSeconds } from './formatDuration';
import { formatCompact, formatInteger, niceDurationTicks, niceTicks, successPercent } from './metricsFormat';

export type DailyMetricKey = 'llm_calls' | 'duration' | 'success' | 'tokens';

export interface DailyMetricSpec {
  key: DailyMetricKey;
  label: string; // libellé du sélecteur
  title: string; // titre de la carte
  subtitle: string;
  // null = aucune donnée ce jour-là : pas de colonne (jamais une colonne à zéro qui mentirait).
  value: (day: DailyMetricsRow) => number | null;
  format: (value: number) => string;
  ticks: (peak: number) => number[];
  // Étiquette sur le pic : pas pour un taux, où « le plus haut » n'est pas remarquable.
  labelPeak: boolean;
}

const successRate = (day: DailyMetricsRow): number | null => successPercent(day.executions - day.failed, day.executions);

export const DAILY_METRICS: DailyMetricSpec[] = [
  {
    key: 'llm_calls', label: 'Appels LLM', title: 'Appels LLM par jour',
    subtitle: 'Appels réels au modèle, toutes exécutions terminées du jour.',
    value: (day) => (day.llm_calls > 0 ? day.llm_calls : null),
    format: formatInteger, ticks: (peak) => niceTicks(peak, 3, true), labelPeak: true,
  },
  {
    key: 'duration', label: 'Durée médiane', title: 'Durée médiane par jour',
    subtitle: 'Médiane des durées des exécutions terminées du jour.',
    value: (day) => day.median_duration_seconds,
    format: formatSeconds, ticks: (peak) => niceDurationTicks(peak, 3), labelPeak: true,
  },
  {
    key: 'success', label: 'Taux de succès', title: 'Taux de succès par jour',
    subtitle: 'Part des exécutions terminées du jour qui ont réussi.',
    value: successRate,
    format: (value) => `${Math.round(value)} %`, ticks: () => [0, 25, 50, 75, 100], labelPeak: false,
  },
  {
    key: 'tokens', label: 'Tokens', title: 'Tokens par jour',
    subtitle: 'Total des tokens des exécutions du jour dont l\'usage est connu.',
    value: (day) => (day.tokens > 0 ? day.tokens : null),
    format: formatCompact, ticks: (peak) => niceTicks(peak, 3), labelPeak: true,
  },
];

export function dailyMetric(key: DailyMetricKey): DailyMetricSpec {
  return DAILY_METRICS.find((metric) => metric.key === key) ?? DAILY_METRICS[0];
}
