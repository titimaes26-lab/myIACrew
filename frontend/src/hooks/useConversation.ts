import { useEffect, useState } from 'react';
import { apiClient, ApiError } from '../api';
import type { ChatTurn, ExecutionHistoryEntry, RepoTarget } from '../types';

function historyEntryToTurn(entry: ExecutionHistoryEntry): ChatTurn {
  return {
    id: entry.id,
    userMessage: entry.user_request,
    status: entry.status,
    workflow: entry.workflow,
    result: entry.result,
    createdAt: entry.created_at,
  };
}

export function useConversation(accessToken: string, apiUrl: string) {
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingClarification, setPendingClarification] = useState<{ originalRequest: string; workflow: string } | null>(null);

  const api = apiClient(apiUrl, accessToken);

  // Reprend la conversation la plus récemment active au chargement
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const conversations = await api.listConversations(1);
        if (cancelled || conversations.length === 0) return;
        const latest = conversations[0];
        const messages = await api.getConversationMessages(latest.id);
        if (cancelled) return;
        setConversationId(latest.id);
        setTurns(messages.map(historyEntryToTurn));
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Impossible de charger la conversation.');
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startNewConversation = () => {
    setConversationId(null);
    setTurns([]);
    setPendingClarification(null);
    setError(null);
  };

  const sendMessage = async (text: string, repoTarget: RepoTarget) => {
    const tempId = `temp-${Date.now()}`;
    const executePayload = {
      conversation_id: conversationId ?? undefined,
      repo_owner: repoTarget.owner || undefined,
      repo_name: repoTarget.name || undefined,
      base_branch: repoTarget.branch || undefined,
    };

    setSending(true);
    setError(null);

    try {
      if (pendingClarification) {
        setTurns((t) => [...t, { id: tempId, userMessage: text, status: 'running', workflow: pendingClarification.workflow, createdAt: new Date().toISOString() }]);
        const data = await api.execute({
          ...executePayload,
          user_request: pendingClarification.originalRequest,
          target_workflow: pendingClarification.workflow,
          clarifications: text,
        });
        setConversationId(data.conversation_id);
        setPendingClarification(null);
        setTurns((t) => t.map((turn) => (turn.id === tempId ? { ...turn, id: data.id, status: 'success', result: data.result } : turn)));
        return;
      }

      setTurns((t) => [...t, { id: tempId, userMessage: text, status: 'running', createdAt: new Date().toISOString() }]);
      const report = await api.qualify(text);

      if (!report.is_clear) {
        setPendingClarification({ originalRequest: text, workflow: report.request_type });
        setTurns((t) => t.map((turn) => (turn.id === tempId
          ? { ...turn, status: 'clarifying', workflow: report.request_type, agentSummary: report.summary, questions: report.questions }
          : turn)));
        return;
      }

      setTurns((t) => t.map((turn) => (turn.id === tempId ? { ...turn, workflow: report.request_type, agentSummary: report.summary } : turn)));

      const data = await api.execute({
        ...executePayload,
        user_request: text,
        target_workflow: report.request_type,
      });
      setConversationId(data.conversation_id);
      setTurns((t) => t.map((turn) => (turn.id === tempId ? { ...turn, id: data.id, status: 'success', result: data.result } : turn)));
    } catch (err: unknown) {
      if (err instanceof ApiError && err.conversationId) setConversationId(err.conversationId);
      const message = err instanceof Error ? err.message : 'Une erreur est survenue.';
      setTurns((t) => t.map((turn) => (turn.id === tempId ? { ...turn, status: 'failed', result: message } : turn)));
      setError(message);
    } finally {
      setSending(false);
    }
  };

  return { turns, sending, error, conversationId, pendingClarification, sendMessage, startNewConversation };
}
