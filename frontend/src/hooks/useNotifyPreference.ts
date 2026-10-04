import { useCallback, useState } from 'react';

const NOTIFY_KEY = 'studio.notify';

export type NotifyPermission = 'unsupported' | 'default' | 'granted' | 'denied';

function currentPermission(): NotifyPermission {
  return typeof Notification === 'undefined' ? 'unsupported' : Notification.permission;
}

function readEnabled(): boolean {
  try {
    return window.localStorage.getItem(NOTIFY_KEY) === 'on';
  } catch {
    return false;
  }
}

// Préférence « me prévenir à la fin d'une exécution » (notification du navigateur). Désactivée par défaut : activer
// demande la permission du navigateur ; si elle est refusée, la préférence reste désactivée et `permission` vaut
// 'denied' (le bouton l'explique). Mémorisée dans localStorage (indisponible = valable pour cette session).
export function useNotifyPreference() {
  const [enabled, setEnabled] = useState(readEnabled);
  const [permission, setPermission] = useState<NotifyPermission>(currentPermission);

  const persist = useCallback((value: boolean) => {
    setEnabled(value);
    try {
      window.localStorage.setItem(NOTIFY_KEY, value ? 'on' : 'off');
    } catch {
      // Stockage indisponible : le choix vaut pour cette session seulement.
    }
  }, []);

  const toggle = useCallback(async () => {
    if (enabled) {
      persist(false);
      return;
    }
    if (typeof Notification === 'undefined') return;
    let result: NotificationPermission = Notification.permission;
    if (result === 'default') {
      try {
        result = await Notification.requestPermission();
      } catch {
        result = Notification.permission;
      }
    }
    setPermission(result);
    persist(result === 'granted');
  }, [enabled, persist]);

  // Activée ET encore autorisée : une permission retirée après coup désactive de fait les notifications.
  return { enabled: enabled && permission === 'granted', permission, toggle };
}
