import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useNotifyPreference } from './useNotifyPreference';

function stubNotification(permission: NotificationPermission, request: NotificationPermission = 'granted') {
  const requestPermission = vi.fn(async () => {
    FakeNotification.permission = request;
    return request;
  });
  class FakeNotification {
    static permission: NotificationPermission = permission;
    static requestPermission = requestPermission;
  }
  vi.stubGlobal('Notification', FakeNotification);
  return requestPermission;
}

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('useNotifyPreference', () => {
  it('est désactivée par défaut', () => {
    stubNotification('default');
    const { result } = renderHook(() => useNotifyPreference());
    expect(result.current).toMatchObject({ enabled: false, permission: 'default' });
  });

  it('demande la permission à l’activation et mémorise le choix', async () => {
    const request = stubNotification('default', 'granted');
    const { result } = renderHook(() => useNotifyPreference());
    await act(async () => { await result.current.toggle(); });
    expect(request).toHaveBeenCalledTimes(1);
    expect(result.current).toMatchObject({ enabled: true, permission: 'granted' });
    expect(window.localStorage.getItem('studio.notify')).toBe('on');
    expect(renderHook(() => useNotifyPreference()).result.current.enabled).toBe(true);
  });

  it('relit la permission quand requestPermission ne renvoie rien (anciens Safari)', async () => {
    class OldSafariNotification {
      static permission: NotificationPermission = 'default';
      static requestPermission = vi.fn(async () => {
        OldSafariNotification.permission = 'granted';
        return undefined as unknown as NotificationPermission;
      });
    }
    vi.stubGlobal('Notification', OldSafariNotification);
    const { result } = renderHook(() => useNotifyPreference());
    await act(async () => { await result.current.toggle(); });
    expect(result.current).toMatchObject({ enabled: true, permission: 'granted' });
  });

  it('reste désactivée si la permission est refusée, et le dit', async () => {
    stubNotification('default', 'denied');
    const { result } = renderHook(() => useNotifyPreference());
    await act(async () => { await result.current.toggle(); });
    expect(result.current).toMatchObject({ enabled: false, permission: 'denied' });
    expect(window.localStorage.getItem('studio.notify')).toBe('off');
  });

  it('se désactive d’un clic sans redemander la permission', async () => {
    const request = stubNotification('granted');
    const { result } = renderHook(() => useNotifyPreference());
    await act(async () => { await result.current.toggle(); });
    expect(result.current.enabled).toBe(true);
    expect(request).not.toHaveBeenCalled();
    await act(async () => { await result.current.toggle(); });
    expect(result.current.enabled).toBe(false);
  });

  it('une permission retirée après coup désactive de fait les notifications', () => {
    stubNotification('denied');
    window.localStorage.setItem('studio.notify', 'on');
    expect(renderHook(() => useNotifyPreference()).result.current).toMatchObject({ enabled: false, permission: 'denied' });
  });

  it('signale un navigateur sans notifications, sans rien faire à l’activation', async () => {
    vi.stubGlobal('Notification', undefined);
    const { result } = renderHook(() => useNotifyPreference());
    expect(result.current.permission).toBe('unsupported');
    await act(async () => { await result.current.toggle(); });
    expect(result.current.enabled).toBe(false);
  });

  it('fonctionne avec un stockage indisponible', async () => {
    stubNotification('granted');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('bloqué'); });
    const { result } = renderHook(() => useNotifyPreference());
    await act(async () => { await result.current.toggle(); });
    expect(result.current.enabled).toBe(true);
  });
});
