import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CONNECTION_LOST_AFTER, useConnectionStatus } from './useConnectionStatus';

describe('useConnectionStatus', () => {
  it('ne signale la perte de connexion qu’après plusieurs échecs consécutifs', () => {
    const { result } = renderHook(() => useConnectionStatus());
    for (let i = 0; i < CONNECTION_LOST_AFTER - 1; i++) act(() => result.current.reportFailure());
    expect(result.current.lost).toBe(false);
    act(() => result.current.reportFailure());
    expect(result.current.lost).toBe(true);
  });

  it('retombe dès qu’une réponse arrive et repart de zéro', () => {
    const { result } = renderHook(() => useConnectionStatus());
    for (let i = 0; i < CONNECTION_LOST_AFTER; i++) act(() => result.current.reportFailure());
    act(() => result.current.reportSuccess());
    expect(result.current.lost).toBe(false);
    act(() => result.current.reportFailure());
    expect(result.current.lost).toBe(false); // le compteur est reparti de zéro
  });

  it('expose des actions stables (utilisables sans relancer un effet)', () => {
    const { result, rerender } = renderHook(() => useConnectionStatus());
    const first = result.current.reportFailure;
    rerender();
    expect(result.current.reportFailure).toBe(first);
  });
});
