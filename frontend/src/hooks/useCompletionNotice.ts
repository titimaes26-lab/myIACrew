import { useEffect, useRef } from 'react';

type Outcome = 'success' | 'failed' | 'other';

interface CompletionNoticeOptions {
  // Une exécution est en cours (suivie par cette session ou reprise depuis l'historique).
  running: boolean;
  // Issue du dernier tour : lue au moment où `running` retombe.
  outcome: Outcome;
  // Demande du dernier tour, pour le corps de la notification.
  message: string;
  notify: boolean;
}

const RUNNING_PREFIX = '⏳ ';
const OUTCOME_PREFIX: Record<Exclude<Outcome, 'other'>, string> = { success: '✓ Terminé — ', failed: '✗ Échec — ' };
const NOTIFICATION_BODY_CHARS = 100;

// Prévient de la fin d'une exécution quand l'utilisateur est sur un autre onglet : le titre de l'onglet passe à
// « ⏳ » pendant l'exécution puis à « ✓ Terminé » / « ✗ Échec » jusqu'au retour sur l'onglet, et une notification du
// navigateur est envoyée si l'utilisateur l'a activée. Jamais pour une exécution annulée ni une clarification.
export function useCompletionNotice({ running, outcome, message, notify }: CompletionNoticeOptions): void {
  const baseTitle = useRef<string | null>(null);
  const wasRunning = useRef(false);
  const pendingOutcome = useRef<Exclude<Outcome, 'other'> | null>(null);

  useEffect(() => {
    if (baseTitle.current === null) baseTitle.current = document.title;
    const base = baseTitle.current;
    const restore = () => {
      pendingOutcome.current = null;
      document.title = base;
    };

    if (running) {
      wasRunning.current = true;
      pendingOutcome.current = null;
      document.title = `${RUNNING_PREFIX}${base}`;
    } else if (wasRunning.current) {
      wasRunning.current = false;
      if (outcome !== 'other' && document.hidden) {
        pendingOutcome.current = outcome;
        document.title = `${OUTCOME_PREFIX[outcome]}${base}`;
        if (notify && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
          try {
            new Notification(outcome === 'success' ? 'Exécution terminée' : 'Exécution en échec', {
              body: message.slice(0, NOTIFICATION_BODY_CHARS), tag: 'studio-run',
            });
          } catch {
            // Constructeur indisponible (certains navigateurs mobiles) : le titre de l'onglet suffit.
          }
        }
      } else {
        restore();
      }
    }

    // Retour sur l'onglet : le résultat a été vu, le titre redevient normal (sauf pendant une exécution).
    const onVisible = () => {
      if (!document.hidden && pendingOutcome.current) restore();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);
    return () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [running, outcome, message, notify]);

  // Au démontage (déconnexion), le titre d'origine est remis.
  useEffect(() => () => {
    if (baseTitle.current !== null) document.title = baseTitle.current;
  }, []);
}
