import { useEffect, useRef } from 'react';
import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { apiClient } from '../../api';
import type { ChatTurn, ExecutionHistoryEntry } from '../../types';
import { isConnectionFailure } from '../../utils/errors';
import { useConnectionStatus } from '../useConnectionStatus';
import { applyProgress, mergeResyncedTurns } from './progressMerge';

// Fréquence de sondage de la progression réelle (voir l'effet plus bas) : assez rapide pour
// paraître réactif face à des étapes qui durent typiquement plusieurs dizaines de secondes,
// sans multiplier inutilement les requêtes.
const PROGRESS_POLL_MS = 3000;

interface ProgressPollingParams {
  turns: ChatTurn[];
  setTurns: Dispatch<SetStateAction<ChatTurn[]>>;
  setError: Dispatch<SetStateAction<string | null>>;
  conversationId: number | null;
  apiUrl: string;
  accessToken: string;
  conversationGenerationRef: MutableRefObject<number>;
}

// Suivi de la progression réelle des tours « running » (voir le commentaire de l'effet) : renvoie aussi l'état de
// connexion (perdue après plusieurs pannes consécutives du sondage).
export function useProgressPolling({ turns, setTurns, setError, conversationId, apiUrl, accessToken, conversationGenerationRef }: ProgressPollingParams) {
  const { lost: connectionLost, reportFailure, reportSuccess, reset: resetConnection } = useConnectionStatus();
  // Distinct de conversationGenerationRef : celui-ci ne bouge que sur un changement RÉEL de
  // conversation, alors qu'un cycle de sondage de progression (l'effet plus bas) peut se
  // terminer et en redémarrer un NOUVEAU sans jamais changer de conversation (ex: un tour
  // "running" repris depuis l'historique se termine, puis un nouveau message est envoyé dans
  // cette même conversation avant qu'une réponse tardive du cycle précédent n'ait fini
  // d'arriver). Sans un identifiant PROPRE à chaque cycle, une telle réponse tardive passerait
  // à tort la vérification par conversationGenerationRef (la conversation, elle, n'a pas
  // changé) et s'appliquerait au tour du nouveau cycle.
  const pollCycleRef = useRef(0);

  // Progression réelle : tant qu'un tour est "running" (juste envoyé DANS cette session, ou
  // encore en cours côté serveur après un rechargement de page qui l'a restauré via
  // loadConversation), sonde périodiquement l'étape en cours persistée en base
  // (ExecutionHistory.current_step, mise à jour par on_step_change côté backend) plutôt que de
  // se fier uniquement à l'estimation par temps écoulé de StepIndicator.
  //
  // Dérivé de `turns` (pas de `sending`) : `sending` est un état local à CETTE session
  // navigateur, remis à false à chaque rechargement de page, alors qu'un tour rechargé depuis
  // l'historique peut être encore "running" côté serveur — c'est précisément le cas que la
  // persistance en base (par opposition à un simple état en mémoire côté backend) est censée
  // couvrir. Une simple valeur booléenne dérivée (pas `turns` lui-même) comme dépendance
  // d'effet : sinon CHAQUE mutation de `turns` (résultat qui arrive, statut qui change)
  // redémarrerait l'effet et donc l'intervalle, sans raison une fois qu'un tour est déjà
  // "running" et le reste.
  const hasRunningTurn = turns.some((t) => t.status === 'running');
  // Un tour chargé depuis l'historique (loadConversation) a un id NUMÉRIQUE réel dès le départ ;
  // un tour envoyé DANS cette session en a un TEMPORAIRE (`temp-...`, une string) tant que
  // sendMessage n'a pas lui-même reçu sa réponse (voir applyExecuteAccepted). Sert plus bas à
  // éviter un fetch de resynchronisation qui ne pourrait de toute façon jamais rien trouver pour
  // ce second cas : messages.find(m => m.id === turn.id) ne peut matcher qu'un id numérique.
  const runningTurnHasNumericId = turns.some((t) => t.status === 'running' && typeof t.id === 'number');
  // Identifiants serveur des tours « running », relus par le sondage (qui ne voit pas `turns` : son effet ne dépend
  // que de booléens) pour ne resynchroniser QUE ces exécutions, pas toute la conversation.
  const runningIdsRef = useRef<number[]>([]);
  useEffect(() => {
    runningIdsRef.current = turns.flatMap((t) => (t.status === 'running' && typeof t.id === 'number' ? [t.id] : []));
  });

  useEffect(() => {
    // Sans conversationId, aucun moyen d'interroger /api/conversations/{id}/messages : c'est
    // le cas du tout premier message d'une conversation encore inexistante côté serveur au
    // moment de l'envoi (son id n'est connu qu'une fois /api/execute résolu) — StepIndicator
    // retombe alors sur son estimation par temps écoulé pour ce seul tour.
    if (!hasRunningTurn || conversationId == null) return;

    // Nouveau cycle de sondage : voir pollCycleRef plus haut. Incrémenté ici (à la mise en
    // place de CET effet), pas dans son nettoyage — un effet suivant qui démarre un nouveau
    // cycle incrémente de toute façon à son tour, ce qui suffit à invalider les closures de
    // celui-ci (myCycle capturé juste après ne correspondra plus à pollCycleRef.current une
    // fois qu'un effet plus récent aura tourné).
    const myCycle = ++pollCycleRef.current;

    // Un seul client pour tous les sondages de CET effet (pas reconstruit à chaque tick) :
    // apiUrl/accessToken sont déjà en dépendances de l'effet, donc le recréer à chaque
    // changement de l'un des deux (via le redémarrage de l'effet) suffit.
    const client = apiClient(apiUrl, accessToken);
    // Numéro de séquence local à CET effet (pas un ref partagé entre effets, un nouvel effet -
    // donc une nouvelle conversation ou un nouveau cycle "running" - repart proprement de zéro) :
    // sur une requête HTTP lente, deux sondages peuvent se doubler et répondre dans le désordre.
    // Sans ce compteur, une réponse partie tôt mais arrivée tard (mySeq plus petit) pourrait
    // écraser une réponse plus récente déjà appliquée, faisant reculer l'affichage de la
    // progression pendant jusqu'à un intervalle de sondage.
    let nextSeq = 0;
    let lastAppliedSeq = 0;
    // Sans ce garde-fou, deux tours de sondage consécutifs (toutes les PROGRESS_POLL_MS) qui
    // constatent chacun "plus rien de running" avant que le premier fetch complet de
    // resynchronisation ci-dessous n'ait eu le temps de répondre déclencheraient chacun leur
    // propre resynchronisation (une requête par exécution suivie) en parallèle pour rien : le
    // second ne fait qu'attendre le même résultat que le premier finira de toute façon par
    // apporter.
    let resyncInFlight = false;
    // Posé au nettoyage : un sondage encore en vol d'un ancien cycle ne doit plus rien signaler.
    let stopped = false;

    const poll = () => {
      // Capturé À CHAQUE appel (pas une fois pour tout l'effet) : un changement de
      // conversation en plein vol annule bien l'intervalle (nettoyage de cet effet, déclenché
      // par le changement de `conversationId` en dépendance), mais n'annule PAS un sondage déjà
      // parti — sans cette vérification après coup, sa réponse pourrait arriver après le
      // changement et appliquer par erreur l'étape d'UNE AUTRE conversation au tour "running"
      // de celle affichée désormais.
      const myGeneration = conversationGenerationRef.current;
      const mySeq = ++nextSeq;

      client.getConversationProgress(conversationId)
        .then((progress) => {
          // Toute réponse (même périmée ci-dessous) prouve que la connexion fonctionne.
          if (stopped) return;
          reportSuccess();
          if (myGeneration !== conversationGenerationRef.current) return;
          if (myCycle !== pollCycleRef.current) return;
          if (mySeq <= lastAppliedSeq) return;
          lastAppliedSeq = mySeq;

          if (progress.status === 'running') {
            setTurns((t) => applyProgress(t, progress));
            return;
          }

          // Plus aucune exécution "running" dans cette conversation : soit elle vient de se
          // terminer, soit ce sondage arrive en double après que sendMessage a déjà lui-même
          // traité sa propre réponse. Un tour chargé depuis l'historique (id numérique réel, via
          // loadConversation) n'est suivi par AUCUN autre mécanisme que ce sondage : sans cette
          // resynchronisation complète, un tel tour resterait bloqué sur "🔄 En cours..."
          // indéfiniment une fois l'exécution terminée côté serveur, et hasRunningTurn ne
          // repasserait jamais à false (le sondage tournerait alors indéfiniment, cette branche
          // répondant toujours "plus rien de 'running'"). À l'inverse, un tour encore sur son id
          // temporaire (envoyé DANS cette session, pas encore remplacé par son id réel — voir
          // applyExecuteAccepted) ne peut par construction jamais être retrouvé par
          // messages.find(m => m.id === turn.id) plus bas (id numérique serveur vs "temp-...") :
          // inutile de payer le coût d'un fetch complet de l'historique à chaque tick pour lui,
          // sendMessage résoudra de toute façon très bientôt sa propre requête /api/execute.
          const idsAtFetch = new Set(runningIdsRef.current);
          if (!runningTurnHasNumericId || idsAtFetch.size === 0 || resyncInFlight) return;
          resyncInFlight = true;
          // Seules les exécutions suivies sont relues (une requête chacune, résultat complet compris), au lieu de
          // toute la conversation avec le résultat de chacun de ses tours ; une exécution disparue (404) est
          // simplement absente de `messages`, ce que le repli plus bas traite comme avant.
          Promise.all([...idsAtFetch].map((id) => client.getExecution(id)))
            .then((found) => found.filter((entry): entry is ExecutionHistoryEntry => entry !== null))
            .then((messages) => {
              if (myGeneration !== conversationGenerationRef.current) return;
              // Un nouveau cycle (nouveau tour "running" démarré pendant que CE fetch, lent,
              // était en vol) a déjà commencé : cette réponse, bâtie sur un instantané de
              // l'historique antérieur à ce nouveau tour, ne peut par construction pas le
              // contenir — sans cette vérification, matching resterait introuvable pour LUI
              // aussi et le repli plus bas le marquerait à tort "supprimé".
              if (myCycle !== pollCycleRef.current) return;
              // Renseigné DANS l'updater ci-dessous (seul endroit qui voit encore le statut
              // "running" ancien ET le statut final tout juste reçu), mais appliqué à l'état
              // global APRÈS cet appel à setTurns, pas depuis l'intérieur de l'updater lui-même
              // (un simple effet de bord y serait à la fois un anti-pattern React — l'updater
              // peut être invoqué plusieurs fois en StrictMode — et retarderait ce setError
              // derrière le prochain rendu déclenché par setTurns). Depuis que /api/execute
              // répond avant la fin réelle de l'exécution, c'est le SEUL endroit qui détecte
              // encore un échec découvert après coup par ce sondage plutôt que directement par
              // la réponse de /api/execute (voir le catch de sendMessage, qui ne voit plus jamais
              // ce genre d'échec) — sans ça, ce même échec n'afficherait plus la bannière
              // d'erreur globale, seulement le badge "❌ Échec" sur la bulle de ce tour.
              let failedMessage: string | null = null;
              setTurns((t) => {
                const result = mergeResyncedTurns(t, idsAtFetch, messages);
                failedMessage = result.failedMessage;
                return result.turns;
              });
              if (failedMessage) setError(failedMessage);
            })
            .catch(() => {
              // Resynchronisation best-effort : un prochain tick de ce même intervalle (tant que
              // hasRunningTurn reste vrai) retentera.
            })
            .finally(() => {
              resyncInFlight = false;
            });
        })
        .catch((err: unknown) => {
          // Sondage périodique best-effort : une erreur réseau ponctuelle ne doit pas
          // interrompre l'exécution en cours, seulement priver cette itération de mise à jour.
          // Plusieurs échecs d'affilée sont signalés à l'utilisateur (connectionLost) — mais
          // seulement les vraies pannes de connexion ou de serveur : un 401/404 est permanent,
          // « nouvelle tentative automatique » serait faux.
          if (!stopped && isConnectionFailure(err)) reportFailure();
        });
    };
    poll();
    const interval = setInterval(poll, PROGRESS_POLL_MS);
    return () => {
      clearInterval(interval);
      stopped = true;
      // Plus de sondage (fin d'exécution, changement de conversation) : plus rien à signaler.
      resetConnection();
    };
  // setTurns, setError et conversationGenerationRef sont STABLES (setters d'état et ref du hook parent) : les lister n'ajoute
  // aucun redémarrage de l'effet, seulement la déclaration honnête de ce qu'il lit.
  }, [hasRunningTurn, runningTurnHasNumericId, conversationId, apiUrl, accessToken, reportSuccess, reportFailure, resetConnection, setTurns, setError, conversationGenerationRef]);

  return { hasRunningTurn, connectionLost };
}
