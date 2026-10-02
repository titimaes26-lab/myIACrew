import { formatSeconds } from './formatDuration';

const NUMBER = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 1 });
const INTEGER = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const COMPACT = new Intl.NumberFormat('fr-FR', { notation: 'compact', maximumFractionDigits: 1 });

export const NO_VALUE = '—';

export function formatNumber(value: number | null | undefined): string {
  return value == null ? NO_VALUE : NUMBER.format(value);
}

export function formatInteger(value: number | null | undefined): string {
  return value == null ? NO_VALUE : INTEGER.format(value);
}

// 12 345 -> « 12 k » : les tokens se comptent en milliers, jamais en unités sur un graphique.
export function formatCompact(value: number | null | undefined): string {
  return value == null ? NO_VALUE : COMPACT.format(value);
}

export function formatSecondsOrDash(value: number | null | undefined): string {
  return value == null ? NO_VALUE : formatSeconds(value);
}

export function formatPercent(part: number, total: number): string {
  return total === 0 ? NO_VALUE : `${INTEGER.format((part / total) * 100)} %`;
}

// « 2026-10-01 » -> « 1 oct. » (date calendaire du serveur, sans conversion de fuseau).
export function formatDay(isoDate: string): string {
  const [year, month, day] = isoDate.split('-').map(Number);
  return new Date(Date.UTC(year, month - 1, day)).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short', timeZone: 'UTC' });
}

// Graduations « propres » (0, 10, 20…) : au plus `count` + 1 valeurs, la dernière >= max.
export function niceTicks(max: number, count = 4): number[] {
  if (!Number.isFinite(max) || max <= 0) return [0, 1];
  const rough = max / count;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= rough) ?? 10 * magnitude;
  const ticks: number[] = [];
  for (let value = 0; value < max + step * 0.999; value += step) ticks.push(Math.round(value * 1e6) / 1e6);
  return ticks;
}

// Graduations de durée en pas « humains » (15 s, 30 s, 1 min, 2 min…) plutôt qu'en pas décimaux
// (100 s, 200 s) qui tombent sur « 1m 40s » : le plus petit pas qui tient en `count` intervalles.
const DURATION_STEPS = [5, 10, 15, 30, 60, 120, 180, 300, 600, 900, 1800, 3600];

export function niceDurationTicks(maxSeconds: number, count = 4): number[] {
  if (!Number.isFinite(maxSeconds) || maxSeconds <= 0) return [0, 1];
  const step = DURATION_STEPS.find((candidate) => Math.ceil(maxSeconds / candidate) <= count)
    ?? Math.ceil(maxSeconds / count / 3600) * 3600;
  const intervals = Math.ceil(maxSeconds / step);
  return Array.from({ length: intervals + 1 }, (_, index) => index * step);
}

// Position d'une valeur sur un axe 0..max, bornée à [0 %, 100 %].
export function percentOf(value: number, max: number): string {
  return `${Math.min(100, Math.max(0, (value / max) * 100))}%`;
}
