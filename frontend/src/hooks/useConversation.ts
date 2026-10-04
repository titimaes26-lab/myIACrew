import { useRef, useState } from 'react';
import { apiClient } from '../api';
import type { ChatTurn } from '../types';
import type { WorkflowType } from '../constants/workflowTypes';
import { toDisplayedError } from '../utils/errors';
import { historyEntryToTurn } from './conversation/turnMapping';
import { useSendOutcomes, type PendingClarification } from './conversation/sendOutcomes';
import { useProgressPolling } from './conversation/useProgressPolling';
import { useLaunchPreview } from './conversation/useLaunchPreview';
import { useSendMessage } from './conversation/useSendMessage';

// État d'une conversation du Studio, assemblé à partir de hooks dédiés (dossier ./conversation) : sondage de la
// progression, aperçu avant lancement, envoi d'un message ; ce fichier garde l'état partagé et la navigation
// (nouvelle conversation, reprise, annulation).
export function useConversation(accessToken: string, apiUrl: string) {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // workflow typé QualificationReport['request_type'] (pas un simple string) : sinon
  // effectiveWorkflow, plus bas, se retrouverait lui-même élargi à string dès qu'il combine
  // cette valeur avec workflowType (typé WorkflowType), perdant la garantie à la
  // compilation que seule une des 4 catégories reconnues par le backend est envoyée.
  const [pendingClarification, setPendingClarification] = useState<PendingClarification | null>(null);
  // Incrémenté à ces mêmes deux limites que workflowType ci-dessous (startNewConversation, et
  // loadConversation seulement quand il ne s'agit pas d'un no-op sur la conversation déjà
  // affichée) — PAS à chaque changement de conversationId : conversationId lui-même passe de
  // null à un id réel dès le premier envoi réussi d'une nouvelle conversation (voir
  // applyExecuteAccepted), une transition de LA MÊME conversation, pas un changement de
  // conversation. Destiné à servir de `key` React sur <ChatInput> (voir Studio.tsx) : un simple
  // remontage via key réinitialise tout l'état local de ce composant (repo cible préremplis
  // compris) bien plus simplement qu'un prop à comparer et resynchroniser à la main, mais
  // seulement si ce qu'on lui donne comme key change UNIQUEMENT à ces limites précises — d'où
  // ce compteur dédié plutôt que conversationId directement.
  const [conversationResetSignal, setConversationResetSignal] = useState(0);
  // ici plutôt que localement dans ChatInput : startNewConversation/loadConversation ont
  // besoin de pouvoir le remettre à 'AUTO' à ces limites naturelles (nouvelle conversation,
  // reprise d'une conversation différente), où faire persister un choix manuel resterait
  // silencieusement actif pour une demande sans rapport avec celle où il avait été choisi.
  const [workflowType, setWorkflowType] = useState<WorkflowType>('AUTO');
  const abortControllerRef = useRef<AbortController | null>(null);
  // Incrémenté à chaque changement de conversation (nouvelle ou reprise d'une différente),
  // jamais pour un simple envoi de message. Sert à repérer, après un await, si l'opération en
  // cours est toujours la plus récente avant d'appliquer son résultat sur l'état : contrairement
  // à une comparaison ponctuelle du type `id !== conversationId` (lue via une closure qui peut
  // être périmée une fois l'await résolu), un ref se lit toujours à sa valeur courante, donc ce
  // test reste valable quel que soit ce qui s'est produit pendant l'attente.
  const conversationGenerationRef = useRef(0);

  const api = apiClient(apiUrl, accessToken);
  const core = { setTurns, setError, setSending, setConversationId, setPendingClarification, abortControllerRef, conversationGenerationRef };

  const { hasRunningTurn, connectionLost } = useProgressPolling({
    turns, setTurns, setError, conversationId, apiUrl, accessToken, conversationGenerationRef,
  });
  const outcomes = useSendOutcomes(core);
  const launch = useLaunchPreview({ api, ...core, outcomes });
  const { sendMessage, prepareRetry, resumeFromRef } = useSendMessage({
    api, conversationId, workflowType, pendingClarification, ...core, outcomes, launch,
  });
  const { pendingLaunch, setPendingLaunch, confirmBeforeLaunch, setConfirmBeforeLaunch, confirmLaunch, cancelLaunch } = launch;

  // Usage interne uniquement (startNewConversation/loadConversation ci-dessous) : abandonne
  // juste la requête HTTP en vol, sans toucher à `turns`. Distinct de cancelSending (le bouton
  // "Annuler" exposé plus bas) car ici l'appelant remplace de toute façon `turns` dans la foulée
  // (fil vide ou historique rechargé) — y marquer un tour "cancelled" au passage n'aurait aucun
  // sens : l'exécution abandonnée par cette navigation continue très bien côté serveur, ce n'est
  // pas un choix explicite de l'utilisateur de l'annuler.
  const abortInFlightRequest = () => {
    abortControllerRef.current?.abort();
  };

  // Exposé comme action du bouton "Annuler". Depuis que /api/execute répond immédiatement (voir
  // applyExecuteAccepted), l'abandon de la requête HTTP elle-même ne suffit plus à grand-chose une
  // fois cette réponse reçue : le tour est alors suivi par le sondage de progression (l'effet plus
  // haut), pas par cette requête — l'annuler ne l'interromprait pas. D'où le marquage local direct
  // de tout tour encore "running" via `dismissedLocally` (voir sa définition dans types.ts) plutôt
  // que via `status` dans CE cas : l'exécution continue réellement côté serveur (son résultat sera
  // de toute façon persisté en base), et /api/execute refuserait de toute façon un nouveau message
  // tant qu'elle n'est pas terminée (garde de concurrence par conversation, backend/main.py) —
  // repasser `status` à 'cancelled' libérerait donc à tort la zone de saisie pour un envoi voué à
  // échouer avec un 409 "exécution déjà en cours".
  //
  // typeof turn.id === 'number' est la condition-clé de ce choix : ce n'est vrai QUE si
  // applyExecuteAccepted a déjà tourné, c'est-à-dire que /api/execute a déjà répondu et que ce
  // tour est donc bien suivi par un id réel que le sondage de progression peut retrouver plus tard
  // via getExecution. Si /api/execute est encore en vol (id encore le tempId local,
  // une string) au moment de ce clic, dismissedLocally serait un piège bien pire que le 409
  // ci-dessus : `status` resterait "running" à jamais SANS qu'aucun mécanisme ne puisse jamais le
  // faire sortir de cet état (le sondage ne resynchronise que par id réel — voir
  // runningTurnHasNumericId — et cet id-là n'arrivera justement jamais, puisque la promesse de
  // api.execute() vient d'être abandonnée). Dans ce cas plus rare (fenêtre de quelques centaines
  // de ms à quelques secondes), on accepte donc le risque, bien moindre, d'un 409 sur un envoi
  // immédiatement suivant plutôt qu'une conversation bloquée pour de bon.
  const cancelSending = () => {
    abortInFlightRequest();
    setTurns((t) => t.map((turn) => {
      if (turn.status !== 'running') return turn;
      if (typeof turn.id === 'number') return { ...turn, dismissedLocally: true };
      return {
        ...turn,
        status: 'cancelled',
        result: "Annulé avant la confirmation du lancement de l'exécution.",
        updatedAt: new Date().toISOString(),
      };
    }));
  };

  // Chaque ouverture de l'interface démarre sur un fil vide ; une conversation passée peut
  // être reprise explicitement depuis le panneau Historique via loadConversation.
  const startNewConversation = () => {
    resumeFromRef.current = null;
    setPendingLaunch(null);
    // Sans ça, une requête encore en vol pourrait se résoudre après coup et rattacher
    // ce nouveau fil (vide) au conversationId de l'ancienne requête via son propre
    // setConversationId(data.conversation_id) dans sendMessage.
    abortInFlightRequest();
    conversationGenerationRef.current += 1;
    setConversationId(null);
    setTurns([]);
    setPendingClarification(null);
    setWorkflowType('AUTO');
    setConversationResetSignal((n) => n + 1);
    setError(null);
    // L'abandon de la requête ci-dessus ne libère `sending` que de façon asynchrone, via le
    // `finally` de sendMessage : sans ce reset immédiat, ce nouveau fil pourtant vide afficherait
    // encore brièvement la zone de saisie désactivée et le bouton Annuler de l'ancien envoi.
    setSending(false);
  };

  const loadConversation = async (id: number) => {
    // Aucun effet si on "reprend" la conversation déjà affichée : ses turns locaux (y compris un
    // tour "clarifying" en attente de réponse, jamais persisté côté serveur tant qu'il n'est pas
    // répondu, ou un tour "running" encore identifié par son tempId le temps que /api/execute
    // réponde) sont déjà à jour, alors que les recharger depuis le serveur les remplacerait par
    // un instantané qui leur correspond moins bien et casserait le rattachement par tempId
    // qu'attend applyExecuteAccepted.
    if (id === conversationId) return;
    setError(null);
    // Annulé seulement en changeant RÉELLEMENT de conversation : annuler même en reprenant la
    // conversation déjà affichée romprait à tort son propre envoi en cours (déjà exclu ci-dessus).
    abortInFlightRequest();
    setPendingLaunch(null);
    conversationGenerationRef.current += 1;
    // Idem `startNewConversation` : sans ce reset immédiat, la conversation qu'on quitte
    // afficherait encore brièvement la zone de saisie désactivée après son abandon.
    setSending(false);
    const myGeneration = conversationGenerationRef.current;
    try {
      const messages = await api.getConversationMessages(id);
      // Abandonné si une navigation plus récente (nouvel appel à loadConversation ou
      // startNewConversation) a eu lieu pendant cet await : sans ce garde-fou, cette réponse
      // périmée écraserait silencieusement l'état de la conversation réellement affichée.
      if (myGeneration !== conversationGenerationRef.current) return;
      setConversationId(id);
      setTurns(messages.map(historyEntryToTurn));
      setPendingClarification(null);
      setWorkflowType('AUTO');
      setConversationResetSignal((n) => n + 1);
    } catch (err: unknown) {
      if (myGeneration !== conversationGenerationRef.current) return;
      setError(toDisplayedError(err, 'Impossible de charger cette conversation.').message);
    }
  };

  return { turns, sending, hasRunningTurn, error, connectionLost, prepareRetry, conversationId, pendingClarification, pendingLaunch, confirmBeforeLaunch, setConfirmBeforeLaunch, confirmLaunch, cancelLaunch, workflowType, setWorkflowType, conversationResetSignal, sendMessage, cancelSending, startNewConversation, loadConversation };
}
