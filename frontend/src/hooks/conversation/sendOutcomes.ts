import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import type { ExecuteAcceptedResponse } from '../../api';
import type { ChatTurn, QualificationReport } from '../../types';
import { toDisplayedError } from '../../utils/errors';
import { isAbortError } from './turnMapping';

export type PendingClarification = { originalRequest: string; workflow: QualificationReport['request_type']; fallback?: boolean };

export interface OutcomeDeps {
  setTurns: Dispatch<SetStateAction<ChatTurn[]>>;
  setError: Dispatch<SetStateAction<string | null>>;
  setConversationId: Dispatch<SetStateAction<number | null>>;
  setPendingClarification: Dispatch<SetStateAction<PendingClarification | null>>;
  conversationGenerationRef: MutableRefObject<number>;
}

// Issues d'un /api/execute (accepté, échoué ou annulé), partagées par sendMessage et confirmLaunch pour qu'elles ne
// puissent pas diverger silencieusement.
export function useSendOutcomes({ setTurns, setError, setConversationId, setPendingClarification, conversationGenerationRef }: OutcomeDeps) {
  // Partagé entre les 3 chemins qui aboutissent à un /api/execute accepté (repli manuel,
  // réponse à une clarification, exécution automatique) pour qu'ils ne puissent pas
  // diverger silencieusement en ne mettant à jour ce mapping que d'un seul côté.
  // /api/execute répond désormais dès que l'exécution est LANCÉE côté serveur (tâche de fond),
  // pas quand elle est TERMINÉE (voir ExecuteAcceptedResponse dans api.ts) : ce tour reste donc
  // "running" ici (déjà posé par pushRunningTurn) — seul son id passe du tempId local à l'id réel
  // en base, pour que le sondage de progression (l'effet plus haut) puisse le suivre et, à terme,
  // le resynchroniser avec son résultat final une fois l'exécution terminée côté serveur, même si
  // cette requête d'origine a depuis été interrompue (écran verrouillé, onglet fermé).
  const applyExecuteAccepted = (tempId: string, data: ExecuteAcceptedResponse) => {
    setConversationId(data.conversation_id);
    setTurns((t) => t.map((turn) => (turn.id === tempId
      ? { ...turn, id: data.id, resumedSteps: data.resumed_steps?.length ? data.resumed_steps : undefined }
      : turn)));
  };

  // Issue d'un envoi qui a échoué ou été annulé, partagée par sendMessage et confirmLaunch : marque le tour
  // concerné « annulé » ou « en échec », sauf si la conversation affichée a changé entretemps.
  const reportSendError = (err: unknown, tempId: string, myGeneration: number) => {
    // Idem : une erreur (y compris une annulation) rattachée à une conversation abandonnée
    // ne doit affecter ni son historique (de toute façon remplacé entretemps) ni, surtout,
    // pendingClarification/error/conversationId de la conversation désormais affichée.
    if (myGeneration !== conversationGenerationRef.current) return;
    if (isAbortError(err)) {
      // Sans ce reset, un message suivant sans rapport serait à tort envoyé comme
      // réponse de clarification à la demande d'origine (désormais abandonnée).
      setPendingClarification(null);
      // Toujours marqué 'cancelled' sans réserve ici (jamais dismissedLocally, voir
      // cancelSending) : ce catch n'est atteint que si l'appel await api.execute() (ou
      // api.qualify()) ci-dessus a lui-même été rejeté par cet abort, ce qui signifie par
      // construction qu'applyExecuteAccepted n'a PAS pu tourner — ce tour est donc encore sur
      // son tempId (une string), jamais l'id réel renvoyé par /api/execute. dismissedLocally
      // suppose justement un id réel déjà connu (voir cancelSending) pour que le sondage de
      // progression puisse un jour reconstituer ce tour par cet id ; sans lui, `status` resterait
      // "running" à jamais, sans AUCUN mécanisme capable de l'en faire sortir — bien pire que le
      // risque, ici accepté, d'un 409 "exécution déjà en cours" si l'utilisateur renvoie un
      // message avant que l'exécution éventuellement déjà lancée côté serveur ne soit terminée.
      setTurns((t) => t.map((turn) => (turn.id === tempId
        ? { ...turn, status: 'cancelled', result: "Annulé côté interface avant confirmation du serveur.", updatedAt: new Date().toISOString() }
        : turn)));
      return;
    }
    const shown = toDisplayedError(err, 'Une erreur est survenue.');
    const message = shown.message;
    setTurns((t) => t.map((turn) => (turn.id === tempId
      ? { ...turn, status: 'failed', result: message, errorCode: shown.code, errorRetryable: shown.retryable, updatedAt: new Date().toISOString() }
      : turn)));
    setError(message);
  };

  return { applyExecuteAccepted, reportSendError };
}

export type SendOutcomes = ReturnType<typeof useSendOutcomes>;
