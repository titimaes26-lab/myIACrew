import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCompletionNotice } from './useCompletionNotice';

type Props = { running: boolean; outcome: 'success' | 'failed' | 'other'; message: string; notify: boolean };
const idle: Props = { running: false, outcome: 'other', message: '', notify: false };
let hidden = false;
let focused = true;
let notifications: { title: string; options?: NotificationOptions; instance?: { onclick: (() => void) | null; close: () => void } }[];

function setHidden(value: boolean) {
  hidden = value;
  document.dispatchEvent(new Event('visibilitychange'));
}

beforeEach(() => {
  document.title = 'Studio';
  hidden = false;
  focused = true;
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  document.hasFocus = () => focused;
  notifications = [];
  class FakeNotification {
    static permission: NotificationPermission = 'granted';
    onclick: (() => void) | null = null;
    close = vi.fn();
    constructor(title: string, options?: NotificationOptions) { notifications.push({ title, options, instance: this }); }
  }
  vi.stubGlobal('Notification', FakeNotification);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function setup(initial: Props = idle) {
  return renderHook((props: Props) => useCompletionNotice(props), { initialProps: initial });
}

describe('useCompletionNotice', () => {
  it('marque le titre pendant l’exécution puis le restaure si l’onglet est visible à la fin', () => {
    const { rerender } = setup();
    expect(document.title).toBe('Studio');
    rerender({ ...idle, running: true });
    expect(document.title).toBe('⏳ Studio');
    rerender({ ...idle, outcome: 'success' });
    expect(document.title).toBe('Studio');
    expect(notifications).toHaveLength(0);   // l'utilisateur regarde déjà l'onglet
  });

  it('annonce la fin dans le titre d’un onglet masqué, jusqu’au retour', () => {
    const { rerender } = setup();
    rerender({ ...idle, running: true });
    hidden = true;
    rerender({ ...idle, outcome: 'failed' });
    expect(document.title).toBe('✗ Échec — Studio');
    setHidden(false);
    expect(document.title).toBe('Studio');
  });

  it('prévient aussi quand la fenêtre est visible mais n’a plus le focus, et se tait au retour', () => {
    const { rerender } = setup();
    rerender({ ...idle, running: true });
    focused = false;
    rerender({ ...idle, outcome: 'success' });
    expect(document.title).toBe('✓ Terminé — Studio');
    focused = true;
    window.dispatchEvent(new Event('focus'));
    expect(document.title).toBe('Studio');
  });

  it('un clic sur la notification ramène la fenêtre', () => {
    const focus = vi.spyOn(window, 'focus').mockImplementation(() => {});
    const { rerender } = setup();
    rerender({ ...idle, running: true, notify: true });
    hidden = true;
    rerender({ ...idle, outcome: 'success', notify: true });
    const instance = notifications[0].instance;
    instance?.onclick?.();
    expect(focus).toHaveBeenCalled();
    expect(instance?.close).toHaveBeenCalled();
  });

  it('envoie une notification du navigateur si elle est activée et autorisée', () => {
    const { rerender } = setup();
    rerender({ running: true, outcome: 'other', message: 'Ajoute un panier', notify: true });
    hidden = true;
    rerender({ running: false, outcome: 'success', message: 'Ajoute un panier ' + 'x'.repeat(200), notify: true });
    expect(document.title).toBe('✓ Terminé — Studio');
    expect(notifications).toHaveLength(1);
    expect(notifications[0].title).toBe('Exécution terminée');
    expect(notifications[0].options?.body).toHaveLength(100);
  });

  it('ne notifie pas si la préférence est désactivée ou la permission absente', () => {
    const { rerender } = setup();
    rerender({ ...idle, running: true });
    hidden = true;
    rerender({ ...idle, outcome: 'success', notify: false });
    expect(notifications).toHaveLength(0);

    vi.stubGlobal('Notification', class { static permission = 'denied'; constructor() { notifications.push({ title: 'x' }); } });
    rerender({ ...idle, running: true, notify: true });
    rerender({ ...idle, outcome: 'success', notify: true });
    expect(notifications).toHaveLength(0);
  });

  it('ne dit rien d’une exécution annulée ou d’une clarification', () => {
    const { rerender } = setup();
    rerender({ ...idle, running: true, notify: true });
    hidden = true;
    rerender({ ...idle, outcome: 'other', notify: true });
    expect(document.title).toBe('Studio');
    expect(notifications).toHaveLength(0);
  });

  it('une notification impossible à construire ne casse rien', () => {
    vi.stubGlobal('Notification', class { static permission = 'granted'; constructor() { throw new TypeError('Illegal constructor'); } });
    const { rerender } = setup();
    rerender({ ...idle, running: true, notify: true });
    hidden = true;
    expect(() => rerender({ ...idle, outcome: 'success', notify: true })).not.toThrow();
    expect(document.title).toBe('✓ Terminé — Studio');
  });

  it('remet le titre d’origine au démontage', () => {
    const { rerender, unmount } = setup();
    rerender({ ...idle, running: true });
    unmount();
    expect(document.title).toBe('Studio');
  });

  it('sans API Notification, seul le titre est utilisé', () => {
    vi.stubGlobal('Notification', undefined);
    const { rerender } = setup();
    rerender({ ...idle, running: true, notify: true });
    hidden = true;
    expect(() => rerender({ ...idle, outcome: 'success', notify: true })).not.toThrow();
    expect(document.title).toBe('✓ Terminé — Studio');
  });
});
