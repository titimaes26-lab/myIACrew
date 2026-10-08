import { useCallback, useRef } from 'react';
import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import type { apiClient } from '../../api';
import type { ChatTurn, PendingClarification, QualificationReport, RepoTarget } from '../../types';
import type { WorkflowType } from '../../constants/workflowTypes';
import { isAbortError } from './turnMapping';
import type { SendOutcomes } from './sendOutcomes';
import type { useLaunchPreview } from './useLaunchPreview';

interface SendMessageParams {
  api: ReturnType<typeof apiClient>;
  conversationId: number | null;
  workflowType: WorkflowType;
  pendingClarification: PendingClarification | null;
  setPendingClarification: Dispatch<SetStateAction<PendingClarification | null>>;
  setTurns: Dispatch<SetStateAction<ChatTurn[]>>;
  setSending: Dispatch<SetStateAction<boolean>>;
  setError: Dispatch<SetStateAction<string | null>>;
  abortControllerRef: MutableRefObject<AbortController | null>;
  conversationGenerationRef: MutableRefObject<number>;
  outcomes: SendOutcomes;
  launch: ReturnType<typeof useLaunchPreview>;
}

// Contexte d'UN envoi, partagé par les trois chemins ci-dessous (réponse à une clarification, type choisi à la main,
// qualification automatique) pour qu'ils ne puissent pas diverger sur sa forme.
interface SendContext {
  text: string;
  tempId: string;
  controller: AbortController;
  myGeneration: number;
  executePayload: Record<string, unknown> & { resume_from_execution_id?: number };
  hasRepoTarget: boolean;
  repoOwner: string;
  repoName: string;
  repoTarget: RepoTarget;
  resume: { id: number; userMessage: string; scope?: string | null } | null;
  pushRunningTurn: (workflow?: string, userMessage?: string) => void;
}

// Envoi d'un message : qualification (mode AUTO), clarification, aperçu avant lancement ou exécution directe, et
// « Relancer » (reprise de l'exécution en échec).
export function useSendMessage({
  api, conversationId, workflowType, pendingClarification, setPendingClarification, setTurns, setSending, setError,
  abortControllerRef, conversationGenerationRef, outcomes, launch,
}: SendMessageParams) {
  const { applyExecuteAccepted, reportSendError } = outcomes;
  const { pendingLaunch, setPendingLaunch, confirmBeforeLaunch, discardPendingLaunch } = launch;
  const tempIdCounterRef = useRef(0);
  // Exécution en échec que le prochain envoi reprend (voir prepareRetry) : consommée par sendMessage.
  const resumeFromRef = useRef<{ id: number; userMessage: string; scope?: string | null } | null>(null);

  // « Relancer » : le prochain envoi, s'il reprend EXACTEMENT la demande de ce tour en échec, demande au
  // serveur de réutiliser ses étapes déjà réussies. Une demande modifiée repart de zéro (le serveur
  // refuse de toute façon une reprise incohérente avec le workflow ou le repository).
  const prepareRetry = useCallback((turn: ChatTurn) => {
    resumeFromRef.current = typeof turn.id === 'number' && turn.status === 'failed'
      ? { id: turn.id, userMessage: turn.userMessage, scope: turn.scope }
      : null;
  }, []);

  // Réponse à une clarification en attente.
  const answerClarification = async (send: SendContext, pendingClarification: PendingClarification) => {
    const { text, tempId, controller, myGeneration, executePayload, hasRepoTarget, pushRunningTurn } = send;
    // Si un type a été choisi manuellement pendant qu'une clarification était en
    // attente, il REMPLACE celui détecté par /api/qualify (pendingClarification.workflow)
    // mais la demande d'origine (pendingClarification.originalRequest) reste la base de
    // la demande : ce texte est traité comme la réponse à la clarification, jamais comme
    // une toute nouvelle demande qui ferait perdre le contexte déjà donné par
    // l'utilisateur. Le tour "❓ Précisions nécessaires" n'a donc pas besoin d'être
    // annulé : il est répondu, comme dans le flux AUTO normal (reste en historique).
    // Combine la demande d'origine et la réponse plutôt que d'afficher seulement `text` (la
    // réponse) : ce tour n'a alors plus l'air de "Relancer" (Studio.tsx, via handleRetry, qui
    // préremplit la zone de saisie avec turn.userMessage) une demande tronquée réduite à sa
    // seule réponse de clarification — un agent avec accès en écriture GitHub recevrait sinon
    // un fragment hors contexte comme s'il s'agissait de la demande complète.
    const clarifiedRequest = `${pendingClarification.originalRequest}\n\nPrécisions apportées : ${text}`;
    let effectiveWorkflow: QualificationReport['request_type'] = pendingClarification.workflow;
    if (workflowType !== 'AUTO') {
      effectiveWorkflow = workflowType;
      pushRunningTurn(effectiveWorkflow, clarifiedRequest);
    } else {
      // En AUTO, la réponse sert souvent justement à trancher la catégorie (la question
      // posée est du type "X ou Y ?" quand la confiance était trop basse) : on requalifie
      // la demande précisée au lieu de réutiliser le type provisoire. Pas de nouvelle
      // clarification ici (is_clear ignoré), pour ne jamais boucler sur des questions.
      pushRunningTurn(undefined, clarifiedRequest);
      // Un échec de cette requalification (quota Gemini, erreur serveur) ne doit pas faire
      // perdre la réponse : on retombe sur le type provisoire, comme avant cette étape.
      let report: QualificationReport | null = null;
      try {
        report = await api.qualify(clarifiedRequest, conversationId, hasRepoTarget, controller.signal);
      } catch (qualifyErr) {
        if (isAbortError(qualifyErr)) throw qualifyErr;
      }
      if (myGeneration !== conversationGenerationRef.current) return;
      // fallback : repli "qualification impossible" du backend (DESIGN_AND_DEV par défaut, le
      // workflow le plus coûteux), jamais un vrai choix.
      if (report && !report.fallback) {
        effectiveWorkflow = report.request_type;
      } else if (pendingClarification.fallback) {
        // Aucune des deux qualifications n'a abouti : plutôt que de lancer au hasard le
        // workflow le plus coûteux, on redemande le type à l'utilisateur. La demande précisée
        // (réponse comprise) devient la nouvelle demande en attente : rien de ce qui a été
        // tapé n'est perdu, il suffit de choisir un type et de confirmer.
        setPendingClarification({ originalRequest: clarifiedRequest, workflow: effectiveWorkflow, fallback: true });
        setTurns((t) => t.map((turn) => (turn.id === tempId
          ? {
            ...turn,
            status: 'clarifying',
            agentSummary: "Le type de demande n'a pas pu être déterminé automatiquement.",
            questions: ['Choisis le type de workflow ci-dessous, puis envoie un message (ex : « ok ») pour lancer la demande précisée.'],
            updatedAt: new Date().toISOString(),
          }
          : turn)));
        return;
      }
      setTurns((t) => t.map((turn) => (turn.id === tempId ? { ...turn, workflow: effectiveWorkflow } : turn)));
    }
    const data = await api.execute({
      ...executePayload,
      user_request: pendingClarification.originalRequest,
      target_workflow: effectiveWorkflow,
      clarifications: text,
    }, controller.signal);
    if (myGeneration !== conversationGenerationRef.current) return;
    setPendingClarification(null);
    applyExecuteAccepted(tempId, data);
    return;
  };

  // Type de demande choisi à la main, sans clarification en attente.
  const launchChosenWorkflow = async (send: SendContext) => {
    const { text, tempId, controller, myGeneration, executePayload, pushRunningTurn } = send;
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
    applyExecuteAccepted(tempId, data);
    return;
  };

  // Mode AUTO : qualification, puis clarification, aperçu avant lancement ou exécution.
  const qualifyAndLaunch = async (send: SendContext) => {
    const { text, tempId, controller, myGeneration, executePayload, hasRepoTarget, repoOwner, repoName, repoTarget, resume, pushRunningTurn } = send;
    pushRunningTurn();
    const report = await api.qualify(text, conversationId, hasRepoTarget, controller.signal);
    // Abandonné si une navigation vers une autre conversation a eu lieu pendant cet await :
    // sans ce garde-fou, la suite (setPendingClarification notamment, état global non
    // propre à une conversation) modifierait à tort l'état de la conversation désormais
    // affichée plutôt que celle, abandonnée, à l'origine de cette demande.
    if (myGeneration !== conversationGenerationRef.current) return;

    if (!report.is_clear) {
      setPendingClarification({ originalRequest: text, workflow: report.request_type, fallback: report.fallback });
      setTurns((t) => t.map((turn) => (turn.id === tempId
        ? { ...turn, status: 'clarifying', workflow: report.request_type, agentSummary: report.summary, questions: report.questions, updatedAt: new Date().toISOString() }
        : turn)));
      return;
    }

    // Une reprise garde la taille du tour en échec : la qualification n'est pas déterministe, et un autre scope
    // changerait les étapes (donc le serveur refuserait de réutiliser celles déjà réussies).
    const resumed = executePayload.resume_from_execution_id !== undefined && resume?.scope;
    const scope = report.request_type === 'FEATURE' ? (resumed ? resume.scope : report.scope) : undefined;
    // Aperçu avant lancement : la qualification peut se tromper (type, taille), et un lancement coûte plusieurs
    // minutes de quota. Pas pour une reprise (« Relancer » : déjà confirmée une fois) ni si l'utilisateur a
    // décoché la confirmation.
    if (confirmBeforeLaunch && executePayload.resume_from_execution_id === undefined) {
      const repoLabel = hasRepoTarget ? `${repoOwner}/${repoName}${repoTarget.branch?.trim() ? ` · ${repoTarget.branch.trim()}` : ''}` : null;
      setPendingLaunch({ tempId, request: text, executePayload });
      setTurns((t) => t.map((turn) => (turn.id === tempId
        ? { ...turn, status: 'clarifying', workflow: report.request_type, agentSummary: report.summary, scope, launchPreview: { workflow: report.request_type, scope: scope === 'PETIT' ? 'PETIT' : 'GRAND', repoLabel } }
        : turn)));
      return;
    }

    setTurns((t) => t.map((turn) => (turn.id === tempId ? { ...turn, workflow: report.request_type, agentSummary: report.summary, scope } : turn)));

    const data = await api.execute({
      ...executePayload,
      user_request: text,
      target_workflow: report.request_type,
      scope,
    }, controller.signal);
    if (myGeneration !== conversationGenerationRef.current) return;
    applyExecuteAccepted(tempId, data);
  };

  const sendMessage = async (text: string, repoTarget: RepoTarget) => {
    // Compteur en plus de l'horodatage : deux envois dans la même milliseconde auraient le même id et le second
    // retoucherait le tour du premier.
    const tempId = `temp-${Date.now()}-${++tempIdCounterRef.current}`;
    // Même normalisation que le backend (champ repository vide ou d'espaces = absent) : qualification et
    // exécution doivent s'accorder sur l'existence d'un repository cible.
    const repoOwner = repoTarget.owner?.trim() ?? '';
    const repoName = repoTarget.name?.trim() ?? '';
    const resume = resumeFromRef.current;
    resumeFromRef.current = null;
    // Un aperçu non confirmé est remplacé par cette nouvelle demande (le taper vaut abandonner l'ancienne).
    if (pendingLaunch) discardPendingLaunch("Remplacée par une nouvelle demande avant son lancement.");
    const executePayload = {
      conversation_id: conversationId ?? undefined,
      resume_from_execution_id: resume && resume.userMessage.trim() === text.trim() ? resume.id : undefined,
      repo_owner: repoOwner || undefined,
      repo_name: repoName || undefined,
      base_branch: repoTarget.branch?.trim() || undefined,
    };

    const hasRepoTarget = Boolean(repoOwner && repoName);
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
    // userMessage surchageable (par défaut `text`, le texte tapé) : nécessaire pour le repli
    // manuel avec clarification ci-dessous, où `text` seul (la réponse à la clarification) ne
    // représente pas la demande complète — voir son appel.
    const pushRunningTurn = (workflow?: string, userMessage: string = text) => {
      setTurns((t) => [...t, { id: tempId, userMessage, status: 'running', workflow, createdAt: new Date().toISOString() }]);
    };

    const send: SendContext = {
      text, tempId, controller, myGeneration, executePayload, hasRepoTarget, repoOwner, repoName, repoTarget, resume, pushRunningTurn,
    };
    try {
      if (pendingClarification) await answerClarification(send, pendingClarification);
      else if (workflowType !== 'AUTO') await launchChosenWorkflow(send);
      else await qualifyAndLaunch(send);
    } catch (err: unknown) {
      reportSendError(err, tempId, myGeneration);
    } finally {
      abortControllerRef.current = null;
      setSending(false);
    }
  };

  return { sendMessage, prepareRetry, resumeFromRef };
}
