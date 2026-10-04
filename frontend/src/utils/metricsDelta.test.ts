import { describe, expect, it } from 'vitest';
import { computeDelta } from './metricsDelta';

describe('computeDelta', () => {
  it('ne donne rien sans valeur à comparer', () => {
    expect(computeDelta(5, null, 'down')).toBeNull();
    expect(computeDelta(null, 5, 'down')).toBeNull();
    expect(computeDelta(undefined, undefined, 'up')).toBeNull();
  });

  it('ne calcule pas de variation relative depuis zéro', () => {
    expect(computeDelta(3, 0, 'down')).toBeNull();
    expect(computeDelta(3, 0, 'down', 'points')).not.toBeNull(); // un écart en points reste défini
  });

  it('lit une baisse de durée comme une amélioration (better = down)', () => {
    const delta = computeDelta(82, 100, 'down');
    expect(delta).toMatchObject({ direction: 'down', tone: 'good', text: '▼ −18 %' });
    expect(delta?.label).toBe('en baisse de 18 % par rapport à la période précédente');
  });

  it('lit une hausse de durée comme une dégradation', () => {
    expect(computeDelta(130, 100, 'down')).toMatchObject({ direction: 'up', tone: 'bad', text: '▲ +30 %' });
  });

  it('lit une hausse du taux de succès comme une amélioration, en points', () => {
    expect(computeDelta(90, 80, 'up', 'points')).toMatchObject({ direction: 'up', tone: 'good', text: '▲ +10 pts' });
    expect(computeDelta(79, 80, 'up', 'points')).toMatchObject({ direction: 'down', tone: 'bad', text: '▼ −1 pt' });
  });

  it('reste neutre quand aucun sens n’est meilleur (nombre d’exécutions)', () => {
    expect(computeDelta(20, 10, 'none')).toMatchObject({ direction: 'up', tone: 'neutral', text: '▲ +100 %' });
  });

  it('signale « stable » sous 0,5 % d’écart plutôt qu’un « +0 % »', () => {
    expect(computeDelta(100.2, 100, 'down')).toMatchObject({ direction: 'flat', tone: 'neutral', text: '= stable' });
    expect(computeDelta(80, 80, 'up', 'points')?.direction).toBe('flat');
  });
});
