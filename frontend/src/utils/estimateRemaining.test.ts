import { describe, expect, it } from 'vitest';
import { estimateRemaining, roundEstimate, type StepStats } from './estimateRemaining';

const keys = ['architecture', 'diagnostic', 'development', 'qa'];
const stats: StepStats = {
  architecture: { p50: 60, runs: 10 }, diagnostic: { p50: 100, runs: 10 },
  development: { p50: 40, runs: 10 }, qa: { p50: 20, runs: 10 }, system: { p50: 10, runs: 10 },
};

describe('estimateRemaining', () => {
  it('additionne le reste de l’étape courante, les étapes suivantes et la finalisation', () => {
    const estimate = estimateRemaining({ stepKeys: keys, currentKey: 'diagnostic', stats, currentElapsed: 30 });
    expect(estimate).toEqual({ remainingSeconds: 70 + 40 + 20 + 10, overrun: false, currentMedian: 100 });
  });

  it('compte à 0 le reste d’une étape qui dépasse sa médiane, et le signale', () => {
    const estimate = estimateRemaining({ stepKeys: keys, currentKey: 'diagnostic', stats, currentElapsed: 150 });
    expect(estimate).toEqual({ remainingSeconds: 40 + 20 + 10, overrun: true, currentMedian: 100 });
  });

  it('ignore les étapes reprises et traite une finalisation non mesurée comme nulle', () => {
    const { system: _system, ...withoutSystem } = stats;
    const estimate = estimateRemaining({ stepKeys: keys, currentKey: 'architecture', reused: ['diagnostic'], stats: withoutSystem, currentElapsed: 0 });
    expect(estimate?.remainingSeconds).toBe(60 + 40 + 20);
  });

  it.each([
    ['étape courante inconnue', { currentKey: 'design' }],
    ['durée écoulée négative', { currentElapsed: -1 }],
    ['durée écoulée non finie', { currentElapsed: Number.NaN }],
    ['étape courante sans mesure', { stats: { ...stats, diagnostic: { p50: 0, runs: 10 } } }],
    ['médiane trop peu fondée', { stats: { ...stats, diagnostic: { p50: 100, runs: 2 } } }],
    ['étape suivante sans mesure', { stats: { ...stats, qa: { p50: 20, runs: 1 } } }],
  ])('ne donne aucune estimation : %s', (_label, override) => {
    expect(estimateRemaining({ stepKeys: keys, currentKey: 'diagnostic', stats, currentElapsed: 30, ...override })).toBeNull();
  });
});

describe('roundEstimate', () => {
  it('arrondit à la dizaine de secondes, jamais sous 10', () => {
    expect(roundEstimate(94)).toBe(90);
    expect(roundEstimate(96)).toBe(100);
    expect(roundEstimate(2)).toBe(10);
  });
});
