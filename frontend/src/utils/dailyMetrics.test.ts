import { describe, expect, it } from 'vitest';
import type { DailyMetricsRow } from '../types';
import { DAILY_METRICS, dailyMetric } from './dailyMetrics';
import { fillDays } from './metricsSeries';

const day = (overrides: Partial<DailyMetricsRow> = {}): DailyMetricsRow => ({
  date: '2026-10-01', executions: 4, failed: 1, llm_calls: 12, tokens: 3400, median_duration_seconds: 95, ...overrides,
});

describe('DAILY_METRICS', () => {
  it('calcule la valeur du jour pour chaque métrique', () => {
    expect(dailyMetric('llm_calls').value(day())).toBe(12);
    expect(dailyMetric('duration').value(day())).toBe(95);
    expect(dailyMetric('success').value(day())).toBe(75);
    expect(dailyMetric('tokens').value(day())).toBe(3400);
  });

  it('ne trace aucune colonne un jour sans donnée (null), jamais une colonne à zéro', () => {
    const empty = day({ executions: 0, failed: 0, llm_calls: 0, tokens: 0, median_duration_seconds: null });
    for (const metric of DAILY_METRICS) expect(metric.value(empty)).toBeNull();
  });

  it('un taux de succès de 0 % est une vraie valeur (toutes les exécutions ont échoué)', () => {
    expect(dailyMetric('success').value(day({ executions: 2, failed: 2 }))).toBe(0);
  });

  it('formate chaque métrique avec son unité', () => {
    expect(dailyMetric('duration').format(95)).toBe('1m 35s');
    expect(dailyMetric('success').format(75.4)).toBe('75 %');
    expect(dailyMetric('llm_calls').format(1234)).toMatch(/1\s?234/);
  });

  it('donne des graduations qui couvrent le pic, entières pour les décomptes, de 0 à 100 pour un taux', () => {
    expect(dailyMetric('llm_calls').ticks(7).every(Number.isInteger)).toBe(true);
    expect(dailyMetric('llm_calls').ticks(7).at(-1)).toBeGreaterThanOrEqual(7);
    expect(dailyMetric('duration').ticks(95).at(-1)).toBeGreaterThanOrEqual(95);
    expect(dailyMetric('success').ticks(80)).toEqual([0, 25, 50, 75, 100]);
  });

  it('retombe sur la première métrique pour une clé inconnue', () => {
    expect(dailyMetric('inconnue' as never).key).toBe('llm_calls');
  });
});

describe('fillDays', () => {
  it('comble les jours manquants avec des données absentes (durée null)', () => {
    const filled = fillDays([day({ date: '2026-10-01' }), day({ date: '2026-10-03' })]);
    expect(filled.map((d) => d.date)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03']);
    expect(filled[1]).toMatchObject({ executions: 0, median_duration_seconds: null });
  });
});
