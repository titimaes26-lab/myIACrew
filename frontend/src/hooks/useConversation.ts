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
    setConversationId(null);
    setTurns([]);
    setPendingClarification(null);
    setWorkflowType('AUTO');
    setError(null);
  };

  const loadConversation = async (id: number) => {
    setError(null);
    try {
      const messages = await api.getConversationMessages(id);
      setConversationId(id);
      setTurns(messages.map(historyEntryToTurn));
      setPendingClarification(null);
      setWorkflowType('AUTO');
    } catch (err: unknown) {
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

    setSending(true);
    setError(null);

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
        setTurns((t) => [...t, { id: tempId, userMessage: text, status: 'running', workflow: effectiveWorkflow, createdAt: new Date().toISOString() }]);
        const data = await api.execute({
          ...executePayload,
          user_request: pendingClarification.originalRequest,
          target_workflow: effectiveWorkflow,
          clarifications: text,
        }, controller.signal);
        setPendingClarification(null);
        applyExecuteSuccess(tempId, data);
        return;
      }

      if (workflowType !== 'AUTO') {
        // Sélection manuelle du type de demande, sans clarification en attente : contourne
        // /api/qualify pour exécuter directement le workflow choisi, le texte tapé étant
        // ici la demande complète (pas la réponse à une question précédente).
        setTurns((t) => [...t, { id: tempId, userMessage: text, status: 'running', workflow: workflowType, createdAt: new Date().toISOString() }]);
        const data = await api.execute({
          ...executePayload,
          user_request: text,
          target_workflow: workflowType,
        }, controller.signal);
        applyExecuteSuccess(tempId, data);
        return;
      }

      setTurns((t) => [...t, { id: tempId, userMessage: text, status: 'running', createdAt: new Date().toISOString() }]);
      const report = await api.qualify(text, controller.signal);

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
      applyExecuteSuccess(tempId, data);
    } catch (err: unknown) {
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
