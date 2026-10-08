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

// L'utilisateur ne regarde pas l'application : onglet masqué, OU fenêtre sans focus (côte à côte avec une autre).
const isAway = () => document.hidden || (typeof document.hasFocus === 'function' && !document.hasFocus());

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
      if (outcome !== 'other' && isAway()) {
        pendingOutcome.current = outcome;
        document.title = `${OUTCOME_PREFIX[outcome]}${base}`;
        if (notify && typeof Notification !== 'undefined' && Notification.permission === 'granted') {
          try {
            const notification = new Notification(outcome === 'success' ? 'Exécution terminée' : 'Exécution en échec', {
              body: message.slice(0, NOTIFICATION_BODY_CHARS), tag: 'studio-run',
            });
            // Un clic ramène l'onglet : sans cela, la notification ne ferait que signaler une fin sans y mener.
            notification.onclick = () => {
              window.focus();
              notification.close();
            };
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
      if (!isAway() && pendingOutcome.current) restore();
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
