import type { DailyMetricsRow } from '../types';

const MAX_FILLED_DAYS = 120;

// Comble les jours sans exécution (zéro) : l'axe du temps reste proportionnel, une pause n'est pas masquée.
export function fillDays(daily: DailyMetricsRow[]): DailyMetricsRow[] {
  if (daily.length < 2) return daily;
  const toUtc = (iso: string) => {
    const [year, month, day] = iso.split('-').map(Number);
    return Date.UTC(year, month - 1, day);
  };
  const first = toUtc(daily[0].date);
  const last = toUtc(daily[daily.length - 1].date);
  const span = Math.round((last - first) / 86_400_000) + 1;
  if (span > MAX_FILLED_DAYS) return daily;
  const byDate = new Map(daily.map((row) => [row.date, row]));
  return Array.from({ length: span }, (_, index) => {
    const date = new Date(first + index * 86_400_000).toISOString().slice(0, 10);
    return byDate.get(date) ?? { date, executions: 0, failed: 0, llm_calls: 0, tokens: 0, qa_total: 0, qa_go: 0, median_duration_seconds: null };
  });
}
