import { act, renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { rangeBetween, useHistorySelection } from './useHistorySelection';

describe('rangeBetween', () => {
  const ids = [10, 20, 30, 40, 50];

  it('renvoie la plage bornes incluses, dans un sens comme dans l’autre', () => {
    expect(rangeBetween(ids, 20, 40)).toEqual([20, 30, 40]);
    expect(rangeBetween(ids, 40, 20)).toEqual([20, 30, 40]);
  });

  it('se limite à l’identifiant cliqué quand l’ancre n’est plus dans la liste', () => {
    expect(rangeBetween(ids, 99, 30)).toEqual([30]);
    expect(rangeBetween(ids, 99, 98)).toEqual([]);
  });
});

describe('useHistorySelection', () => {
  const selectable = [1, 2, 4, 5]; // 3 est « en cours » : jamais sélectionnable

  it('coche et décoche une ligne', () => {
    const { result } = renderHook(() => useHistorySelection(selectable));
    act(() => result.current.toggle(2, false));
    expect([...result.current.selected]).toEqual([2]);
    act(() => result.current.toggle(2, false));
    expect(result.current.selected.size).toBe(0);
  });

  it('ignore une ligne non sélectionnable', () => {
    const { result } = renderHook(() => useHistorySelection(selectable));
    act(() => result.current.toggle(3, false));
    expect(result.current.selected.size).toBe(0);
  });

  it('Maj+clic coche la plage depuis la dernière ligne cliquée, en sautant les lignes non sélectionnables', () => {
    const { result } = renderHook(() => useHistorySelection(selectable));
    act(() => result.current.toggle(1, false));
    act(() => result.current.toggle(5, true));
    expect([...result.current.selected].sort()).toEqual([1, 2, 4, 5]);
  });

  it('Maj+clic sur une ligne déjà cochée décoche la plage', () => {
    const { result } = renderHook(() => useHistorySelection(selectable));
    act(() => result.current.selectAll());
    act(() => result.current.toggle(1, false)); // ancre = 1 (décochée)
    act(() => result.current.toggle(4, true)); // 4 est cochée : la plage 1..4 est décochée
    expect([...result.current.selected]).toEqual([5]);
  });

  it('« Tout sélectionner » prend les lignes sélectionnables, « annuler » vide tout', () => {
    const { result } = renderHook(() => useHistorySelection(selectable));
    act(() => result.current.selectAll());
    expect(result.current.selected.size).toBe(4);
    act(() => result.current.clear());
    expect(result.current.selected.size).toBe(0);
  });

  it('retire de la sélection une ligne qui disparaît de la liste ou devient non sélectionnable', () => {
    const { result, rerender } = renderHook(({ ids }) => useHistorySelection(ids), { initialProps: { ids: selectable } });
    act(() => result.current.selectAll());
    rerender({ ids: [1, 5] });
    expect([...result.current.selected].sort()).toEqual([1, 5]);
  });
});
