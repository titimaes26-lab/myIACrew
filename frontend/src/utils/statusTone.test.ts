import { describe, expect, it } from 'vitest';
import { statusTone } from './statusTone';

describe('statusTone', () => {
  it.each([
    ['GO', 'success'],
    ['GO_AVEC_RESERVES', 'warning'],
    ['GO AVEC RÉSERVES', 'warning'],
    ['NO_GO', 'danger'],
    ['NO GO', 'danger'],
    ['NON-GO', 'danger'],
    ['NON CONFORME', 'danger'],
    ['Confiance: 80%', 'info'],
    ['GOOD', 'info'],
  ])('%s -> %s', (status, tone) => {
    expect(statusTone(status)).toBe(tone);
  });
});
