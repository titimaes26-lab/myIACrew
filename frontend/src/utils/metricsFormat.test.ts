import { describe, expect, it } from 'vitest';
import { formatPercent, niceTicks, percentOf } from './metricsFormat';

describe('niceTicks', () => {
  it('donne des graduations décimales propres par défaut', () => {
    expect(niceTicks(10, 5)).toEqual([0, 2, 4, 6, 8, 10]);
    expect(niceTicks(0)).toEqual([0, 1]);
  });

  it('ne produit jamais de graduation fractionnaire en mode entier (décomptes)', () => {
    for (const max of [1, 2, 3, 7, 12]) {
      expect(niceTicks(max, 4, true).every(Number.isInteger)).toBe(true);
    }
    expect(niceTicks(1, 4, true)).toEqual([0, 1]);
    expect(niceTicks(2, 4, true)).toEqual([0, 1, 2]);
  });

  it('couvre toujours la valeur maximale', () => {
    expect(niceTicks(7, 4, true).at(-1)).toBeGreaterThanOrEqual(7);
  });
});

describe('percentOf / formatPercent', () => {
  it('borne la largeur entre 0 et 100 %', () => {
    expect(percentOf(50, 100)).toBe('50%');
    expect(percentOf(500, 100)).toBe('100%');
    expect(percentOf(-5, 100)).toBe('0%');
  });

  it("affiche un tiret quand le total est nul", () => {
    expect(formatPercent(1, 0)).toBe('—');
  });
});
