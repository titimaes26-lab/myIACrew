import { useCallback, useEffect, useRef, useState } from 'react';
import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import type { apiClient } from '../../api';
import type { ChatTurn, LaunchChoice } from '../../types';
import { readConfirmLaunch, CONFIRM_LAUNCH_KEY, type PendingLaunch } from './turnMapping';
import type { SendOutcomes } from './sendOutcomes';

interface LaunchPreviewParams {
  api: ReturnType<typeof apiClient>;
  setTurns: Dispatch<SetStateAction<ChatTurn[]>>;
  setSending: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  abortControllerRef: MutableRefObject<AbortController | null>;
  conversationGenerationRef: MutableRefObject<number>;
  outcomes: SendOutcomes;
}

// Aperçu avant lancement : état de la demande qualifiée en attente de « Lancer », préférence « confirmer avant de
// lancer », et les actions confirmer / annuler / abandonner.
export function useLaunchPreview({ api, setTurns, setSending, setError, abortControllerRef, conversationGenerationRef, outcomes }: LaunchPreviewParams) {
  const { applyExecuteAccepted, reportSendError } = outcomes;
  // Aperçu avant lancement : la demande qualifiée attend « Lancer » (voir confirmLaunch). Un état (et non un ref) :
  // Studio désactive « Relancer » tant qu'il existe.
  const [pendingLaunch, setPendingLaunch] = useState<PendingLaunch | null>(null);
  const launchingRef = useRef(false);
  const [confirmBeforeLaunch, setConfirmBeforeLaunchState] = useState(readConfirmLaunch);
  const setConfirmBeforeLaunch = useCallback((value: boolean) => {
    setConfirmBeforeLaunchState(value);
    try {
      window.localStorage.setItem(CONFIRM_LAUNCH_KEY, value ? 'on' : 'off');
    } catch {
      // Stockage indisponible : le choix vaut pour cette session seulement.
    }
  }, []);
  // Choix du type de demande, modifiable avant chaque envoi (voir sendMessage) mais tenu

  // Abandonne l'aperçu en attente : son tour est marqué annulé (rien n'a été lancé côté serveur).
  const discardPendingLaunch = (reason: string) => {
    const pending = pendingLaunch;
    if (!pending) return;
    setPendingLaunch(null);
    setTurns((t) => t.map((turn) => (turn.id === pending.tempId
      ? { ...turn, status: 'cancelled', launchPreview: undefined, result: reason, updatedAt: new Date().toISOString() }
      : turn)));
  };

  const cancelLaunch = () => discardPendingLaunch('Lancement annulé : rien n\'a été exécuté.');

  // « Lancer » depuis l'aperçu, avec le type et la taille éventuellement corrigés par l'utilisateur.
  const confirmLaunch = async (choice: LaunchChoice) => {
    const pending = pendingLaunch;
    // launchingRef : un second clic avant le rendu suivant enverrait un deuxième /api/execute (409, tour en échec).
    if (!pending || launchingRef.current) return;
    launchingRef.current = true;
    setPendingLaunch(null);
    const controller = new AbortController();
    abortControllerRef.current = controller;
    const myGeneration = conversationGenerationRef.current;
    const scope = choice.workflow === 'FEATURE' ? choice.scope : undefined;
    setSending(true);
    setError(null);
    // createdAt repart de maintenant : la frise et le chrono comptent depuis le lancement, pas depuis l'attente.
    setTurns((t) => t.map((turn) => (turn.id === pending.tempId
      ? { ...turn, status: 'running', workflow: choice.workflow, scope, launchPreview: undefined, questions: undefined, createdAt: new Date().toISOString() }
      : turn)));
    try {
      const data = await api.execute({
        ...pending.executePayload,
        user_request: pending.request,
        target_workflow: choice.workflow,
        scope,
      }, controller.signal);
      if (myGeneration !== conversationGenerationRef.current) return;
      applyExecuteAccepted(pending.tempId, data);
    } catch (err: unknown) {
      reportSendError(err, pending.tempId, myGeneration);
    } finally {
      launchingRef.current = false;
      abortControllerRef.current = null;
      setSending(false);
    }
  };

  // Références STABLES vers confirmLaunch/cancelLaunch (recréées à chaque rendu) : transmises à chaque ChatMessage
  // (React.memo), elles ne doivent pas changer à chaque sondage de progression.
  const confirmLaunchRef = useRef(confirmLaunch);
  const cancelLaunchRef = useRef(cancelLaunch);
  useEffect(() => {
    confirmLaunchRef.current = confirmLaunch;
    cancelLaunchRef.current = cancelLaunch;
  });
  const stableConfirmLaunch = useCallback((choice: LaunchChoice) => confirmLaunchRef.current(choice), []);
  const stableCancelLaunch = useCallback(() => cancelLaunchRef.current(), []);

  return {
    pendingLaunch, setPendingLaunch, confirmBeforeLaunch, setConfirmBeforeLaunch, discardPendingLaunch,
    confirmLaunch: stableConfirmLaunch, cancelLaunch: stableCancelLaunch,
  };
}
