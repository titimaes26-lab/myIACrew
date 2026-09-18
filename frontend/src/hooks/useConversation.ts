import { useRef, useState } from 'react';
import { apiClient, ApiError, type ExecuteResponse } from '../api';
import type { ChatTurn, ExecutionHistoryEntry, QualificationReport, RepoTarget } from '../types';
import type { WorkflowType } from '../constants/workflowTypes';

function isAbortError(err: unknown): boolean {
  return err instanceof DOMException && err.name === 'AbortError';
}

function historyEntryToTurn(entry: ExecutionHistoryEntry): ChatTurn {
  return {
    id: entry.id,
    userMessage: entry.user_request,
    status: entry.status,
    workflow: entry.workflow,
    result: entry.result,
    createdAt: entry.created_at,
    updatedAt: entry.status !== 'running' ? entry.updated_at : undefined,
    apiCallsCount: entry.api_calls_count,
    rateLimitHits: entry.rate_limit_hits,
    totalWaitTimeSeconds: entry.total_wait_time_seconds,
  };
}

export function useConversation(accessToken: string, apiUrl: string) {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // workflow typé QualificationReport['request_type'] (pas un simple string) : sinon
  // effectiveWorkflow, plus bas, se retrouverait lui-même élargi à string dès qu'il combine
  // cette valeur avec workflowType (typé WorkflowType), perdant la garantie à la
  // compilation que seule une des 4 catégories reconnues par le backend est envoyée.
  const [pendingClarification, setPendingClarification] = useState<{ originalRequest: string; workflow: QualificationReport['request_type'] } | null>(null);
  // Choix du type de demande, modifiable avant chaque envoi (voir sendMessage) mais tenu
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

  const cancelSending = () => {
    abortControllerRef.current?.abort();
  };

  // Chaque ouverture de l'interface démarre sur un fil vide ; une conversation passée peut
  // être reprise explicitement depuis le panneau Historique via loadConversation.
  const startNewConversation = () => {
    // Sans ça, une requête encore en vol pourrait se résoudre après coup et rattacher
    // ce nouveau fil (vide) au conversationId de l'ancienne requête via son propre
    // setConversationId(data.conversation_id) dans sendMessage.
    cancelSending();
    conversationGenerationRef.current += 1;
    setConversationId(null);
    setTurns([]);
    setPendingClarification(null);
    setWorkflowType('AUTO');
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
    // qu'attend applyExecuteSuccess.
    if (id === conversationId) return;
    setError(null);
    // Annulé seulement en changeant RÉELLEMENT de conversation : annuler même en reprenant la
    // conversation déjà affichée romprait à tort son propre envoi en cours (déjà exclu ci-dessus).
    cancelSending();
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
    } catch (err: unknown) {
      if (myGeneration !== conversationGenerationRef.current) return;
      setError(err instanceof Error ? err.message : 'Impossible de charger cette conversation.');
    }
  };

  const cancelTurn = (turnId: ChatTurn['id'], message: string) => {
    setTurns((t) => t.map((turn) => (turn.id === turnId
      ? { ...turn, status: 'cancelled', result: message, updatedAt: new Date().toISOString() }
      : turn)));
  };

  // Partagé entre les 3 chemins qui aboutissent à un /api/execute réussi (repli manuel,
  // réponse à une clarification, exécution automatique) pour qu'ils ne puissent pas
  // diverger silencieusement en ne mettant à jour ce mapping que d'un seul côté.
  const applyExecuteSuccess = (tempId: string, data: ExecuteResponse) => {
    setConversationId(data.conversation_id);
    setTurns((t) => t.map((turn) => (turn.id === tempId
      ? {
          ...turn,
          id: data.id,
          status: 'success',
          result: data.result,
          updatedAt: new Date().toISOString(),
          apiCallsCount: data.api_calls_count,
          rateLimitHits: data.rate_limit_hits,
          totalWaitTimeSeconds: data.total_wait_time_seconds,
        }
      : turn)));
  };

  const sendMessage = async (text: string, repoTarget: RepoTarget) => {
    const tempId = `temp-${Date.now()}`;
    const executePayload = {
      conversation_id: conversationId ?? undefined,
      repo_owner: repoTarget.owner || undefined,
      repo_name: repoTarget.name || undefined,
      base_branch: repoTarget.branch || undefined,
    };

    const controller = new AbortController();
    abortControllerRef.current = controller;
    // Capturé maintenant (jamais modifié par sendMessage lui-même, seulement lu) : si une
    // navigation vers une autre conversation survient pendant un des await ci-dessous, ce
    // nombre ne correspondra plus à conversationGenerationRef.current au retour de l'await,
    // ce qui permet d'abandonner silencieusement un résultat devenu obsolète (pendingClarification
    // notamment est un état global, pas propre à une conversation, qui serait sinon modifié à
    // tort pour la conversation désormais affichée).
    const myGeneration = conversationGenerationRef.current;

    setSending(true);
    setError(null);

    // Partagé entre les 3 chemins qui poussent le tour de CET envoi avant son exécution
    // (repli manuel avec clarification, repli manuel sans clarification, AUTO avant
    // /api/qualify) pour qu'ils ne puissent pas diverger silencieusement sur sa forme.
    // workflow omis (undefined) tant que la catégorie n'est pas encore connue (avant
    // /api/qualify) : ChatTurn.workflow est optionnel précisément pour ce cas.
    const pushRunningTurn = (workflow?: string) => {
      setTurns((t) => [...t, { id: tempId, userMessage: text, status: 'running', workflow, createdAt: new Date().toISOString() }]);
    };

    try {
      if (pendingClarification) {
        // Si un type a été choisi manuellement pendant qu'une clarification était en
        // attente, il REMPLACE celui détecté par /api/qualify (pendingClarification.workflow)
        // mais la demande d'origine (pendingClarification.originalRequest) reste la base de
        // la demande : ce texte est traité comme la réponse à la clarification, jamais comme
        // une toute nouvelle demande qui ferait perdre le contexte déjà donné par
        // l'utilisateur. Le tour "❓ Précisions nécessaires" n'a donc pas besoin d'être
        // annulé : il est répondu, comme dans le flux AUTO normal (reste en historique).
        const effectiveWorkflow = workflowType === 'AUTO' ? pendingClarification.workflow : workflowType;
        pushRunningTurn(effectiveWorkflow);
        const data = await api.execute({
          ...executePayload,
          user_request: pendingClarification.originalRequest,
          target_workflow: effectiveWorkflow,
          clarifications: text,
        }, controller.signal);
        if (myGeneration !== conversationGenerationRef.current) return;
        setPendingClarification(null);
        applyExecuteSuccess(tempId, data);
        return;
      }

      if (workflowType !== 'AUTO') {
        // Sélection manuelle du type de demande, sans clarification en attente : contourne
        // /api/qualify pour exécuter directement le workflow choisi, le texte tapé étant
        // ici la demande complète (pas la réponse à une question précédente).
        pushRunningTurn(workflowType);
        const data = await api.execute({
          ...executePayload,
          user_request: text,
          target_workflow: workflowType,
        }, controller.signal);
        if (myGeneration !== conversationGenerationRef.current) return;
        applyExecuteSuccess(tempId, data);
        return;
      }

      pushRunningTurn();
      const report = await api.qualify(text, controller.signal);
      // Abandonné si une navigation vers une autre conversation a eu lieu pendant cet await :
      // sans ce garde-fou, la suite (setPendingClarification notamment, état global non
      // propre à une conversation) modifierait à tort l'état de la conversation désormais
      // affichée plutôt que celle, abandonnée, à l'origine de cette demande.
      if (myGeneration !== conversationGenerationRef.current) return;

      if (!report.is_clear) {
        setPendingClarification({ originalRequest: text, workflow: report.request_type });
        setTurns((t) => t.map((turn) => (turn.id === tempId
          ? { ...turn, status: 'clarifying', workflow: report.request_type, agentSummary: report.summary, questions: report.questions, updatedAt: new Date().toISOString() }
          : turn)));
        return;
      }

      setTurns((t) => t.map((turn) => (turn.id === tempId ? { ...turn, workflow: report.request_type, agentSummary: report.summary } : turn)));

      const data = await api.execute({
        ...executePayload,
        user_request: text,
        target_workflow: report.request_type,
      }, controller.signal);
      if (myGeneration !== conversationGenerationRef.current) return;
      applyExecuteSuccess(tempId, data);
    } catch (err: unknown) {
      // Idem : une erreur (y compris une annulation) rattachée à une conversation abandonnée
      // ne doit affecter ni son historique (de toute façon remplacé entretemps) ni, surtout,
      // pendingClarification/error/conversationId de la conversation désormais affichée.
      if (myGeneration !== conversationGenerationRef.current) return;
      if (isAbortError(err)) {
        // Sans ce reset, un message suivant sans rapport serait à tort envoyé comme
        // réponse de clarification à la demande d'origine (désormais abandonnée).
        setPendingClarification(null);
        cancelTurn(tempId, "Annulé côté interface. L'exécution peut continuer côté serveur si elle était déjà lancée : le résultat, s'il arrive, apparaîtra dans l'historique.");
        return;
      }
      if (err instanceof ApiError && err.conversationId) setConversationId(err.conversationId);
      const message = err instanceof Error ? err.message : 'Une erreur est survenue.';
      setTurns((t) => t.map((turn) => (turn.id === tempId
        ? { ...turn, status: 'failed', result: message, updatedAt: new Date().toISOString() }
        : turn)));
      setError(message);
    } finally {
      abortControllerRef.current = null;
      setSending(false);
    }
  };

  return { turns, sending, error, conversationId, pendingClarification, workflowType, setWorkflowType, sendMessage, cancelSending, startNewConversation, loadConversation };
}
