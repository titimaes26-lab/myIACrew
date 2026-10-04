import { describe, expect, it } from 'vitest';
import { ApiError } from '../api';
import { failureHint, isConnectionFailure, toDisplayedError } from './errors';

describe('isConnectionFailure', () => {
  it('compte les vraies pannes : réseau, 5xx, délai, trop de requêtes', () => {
    expect(isConnectionFailure(new ApiError('x', { code: 'NETWORK_ERROR' }))).toBe(true);
    expect(isConnectionFailure(new ApiError('x', { status: 503 }))).toBe(true);
    expect(isConnectionFailure(new ApiError('x', { status: 408 }))).toBe(true);
    expect(isConnectionFailure(new ApiError('x', { status: 429 }))).toBe(true);
  });

  it('ne compte pas une erreur permanente côté client ni une erreur inconnue', () => {
    expect(isConnectionFailure(new ApiError('x', { status: 401 }))).toBe(false);
    expect(isConnectionFailure(new ApiError('x', { status: 404 }))).toBe(false);
    expect(isConnectionFailure(new Error('x'))).toBe(false);
  });
});

describe('toDisplayedError', () => {
  it('reprend le message, le code et retryable d’une ApiError', () => {
    const shown = toDisplayedError(new ApiError('Quota', { code: 'QUOTA_EXHAUSTED', retryable: true }), 'repli');
    expect(shown).toEqual({ message: 'Quota', code: 'QUOTA_EXHAUSTED', retryable: true });
  });

  it('utilise le repli pour une valeur qui n’est pas une erreur', () => {
    expect(toDisplayedError('boum', 'repli')).toEqual({ message: 'repli', code: null, retryable: false });
  });
});

describe('failureHint', () => {
  it('renvoie un conseil par cause connue et renvoie au bouton « Relancer »', () => {
    expect(failureHint('QUOTA_EXHAUSTED')).toContain('Relancer');
    expect(failureHint('INTERRUPTED')).toContain('Relancer');
    expect(failureHint('GUARDRAIL_FAILED')).toContain('reformulez');
    expect(failureHint('DELIVERY_FAILED')).toContain('rapport de l\'agent');
    expect(failureHint('INTERNAL_ERROR')).toBeNull();
    expect(failureHint(null)).toBeNull();
  });
});
