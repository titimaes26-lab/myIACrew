import { useEffect, useRef, useState } from 'react';
import { apiClient, ApiError, type ExecuteResponse } from '../api';
import type { ChatTurn, ExecutionHistoryEntry, QualificationReport, RepoTarget } from '../types';
import type { WorkflowType } from '../constants/workflowTypes';

// Fréquence de sondage de la progression réelle (voir l'effet plus bas) : assez rapide pour
// paraître réactif face à des étapes qui durent typiquement plusieurs dizaines de secondes,
// sans multiplier inutilement les requêtes.
const PROGRESS_POLL_MS = 3000;

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
    currentStep: entry.current_step,
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
  // Distinct de conversationGenerationRef : celui-ci ne bouge que sur un changement RÉEL de
  // conversation, alors qu'un cycle de sondage de progression (l'effet plus bas) peut se
  // terminer et en redémarrer un NOUVEAU sans jamais changer de conversation (ex: un tour
  // "running" repris depuis l'historique se termine, puis un nouveau message est envoyé dans
  // cette même conversation avant qu'une réponse tardive du cycle précédent n'ait fini
  // d'arriver). Sans un identifiant PROPRE à chaque cycle, une telle réponse tardive passerait
  // à tort la vérification par conversationGenerationRef (la conversation, elle, n'a pas
  // changé) et s'appliquerait au tour du nouveau cycle.
  const pollCycleRef = useRef(0);

  const api = apiClient(apiUrl, accessToken);

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
  // sendMessage n'a pas lui-même reçu sa réponse (voir applyExecuteSuccess). Sert plus bas à
  // éviter un fetch de resynchronisation qui ne pourrait de toute façon jamais rien trouver pour
  // ce second cas : messages.find(m => m.id === turn.id) ne peut matcher qu'un id numérique.
  const runningTurnHasNumericId = turns.some((t) => t.status === 'running' && typeof t.id === 'number');

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
    // propre getConversationMessages (potentiellement volumineux) en parallèle pour rien : le
    // second ne fait qu'attendre le même résultat que le premier finira de toute façon par
    // apporter.
    let resyncInFlight = false;

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
          if (myGeneration !== conversationGenerationRef.current) return;
          if (myCycle !== pollCycleRef.current) return;
          if (mySeq <= lastAppliedSeq) return;
          lastAppliedSeq = mySeq;

          if (progress.status === 'running') {
            setTurns((t) => t.map((turn) => (
              // Évite une nouvelle référence d'objet (et donc un re-rendu du ChatMessage
              // mémoïsé correspondant) quand l'étape sondée est identique à celle déjà connue
              // localement, ce qui est le cas courant : une étape CrewAI dure typiquement bien
              // plus longtemps qu'un intervalle de sondage.
              turn.status === 'running' && turn.currentStep !== progress.current_step
                ? { ...turn, currentStep: progress.current_step }
                : turn
            )));
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
          // applyExecuteSuccess) ne peut par construction jamais être retrouvé par
          // messages.find(m => m.id === turn.id) plus bas (id numérique serveur vs "temp-...") :
          // inutile de payer le coût d'un fetch complet de l'historique à chaque tick pour lui,
          // sendMessage résoudra de toute façon très bientôt sa propre requête /api/execute.
          if (!runningTurnHasNumericId || resyncInFlight) return;
          resyncInFlight = true;
          client.getConversationMessages(conversationId)
            .then((messages) => {
              if (myGeneration !== conversationGenerationRef.current) return;
              // Un nouveau cycle (nouveau tour "running" démarré pendant que CE fetch, lent,
              // était en vol) a déjà commencé : cette réponse, bâtie sur un instantané de
              // l'historique antérieur à ce nouveau tour, ne peut par construction pas le
              // contenir — sans cette vérification, matching resterait introuvable pour LUI
              // aussi et le repli plus bas le marquerait à tort "supprimé".
              if (myCycle !== pollCycleRef.current) return;
              setTurns((t) => t.map((turn) => {
                if (turn.status !== 'running') return turn;
                const matching = messages.find((m) => m.id === turn.id);
                if (matching) return historyEntryToTurn(matching);
                // Toujours introuvable dans l'historique de cette conversation : la ligne a
                // disparu de la base (ex: nettoyage manuel direct en base d'une exécution restée
                // bloquée à "running" pour toujours après un crash serveur — /api/history ne
                // permet plus, lui, de supprimer une ligne "running" justement pour éviter ce
                // cas). Sans ce repli, ce sondage tournerait indéfiniment : plus aucune ligne
                // "running" à trouver, mais rien non plus à faire correspondre à ce tour pour le
                // faire sortir de cet état.
                return {
                  ...turn,
                  status: 'cancelled' as const,
                  result: "Cette exécution n'existe plus en base (nettoyage manuel probable).",
                  updatedAt: new Date().toISOString(),
                };
              }));
            })
            .catch(() => {
              // Resynchronisation best-effort : un prochain tick de ce même intervalle (tant que
              // hasRunningTurn reste vrai) retentera.
            })
            .finally(() => {
              resyncInFlight = false;
            });
        })
        .catch(() => {
          // Sondage périodique best-effort : une erreur réseau ponctuelle ne doit pas
          // interrompre l'exécution en cours, seulement priver cette itération de mise à jour.
        });
    };
    poll();
    const interval = setInterval(poll, PROGRESS_POLL_MS);
    return () => clearInterval(interval);
  }, [hasRunningTurn, runningTurnHasNumericId, conversationId, apiUrl, accessToken]);

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
    // userMessage surchageable (par défaut `text`, le texte tapé) : nécessaire pour le repli
    // manuel avec clarification ci-dessous, où `text` seul (la réponse à la clarification) ne
    // représente pas la demande complète — voir son appel.
    const pushRunningTurn = (workflow?: string, userMessage: string = text) => {
      setTurns((t) => [...t, { id: tempId, userMessage, status: 'running', workflow, createdAt: new Date().toISOString() }]);
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
        // Combine la demande d'origine et la réponse plutôt que d'afficher seulement `text` (la
        // réponse) : ce tour n'a alors plus l'air de "Relancer" (Studio.tsx, via handleRetry, qui
        // préremplit la zone de saisie avec turn.userMessage) une demande tronquée réduite à sa
        // seule réponse de clarification — un agent avec accès en écriture GitHub recevrait sinon
        // un fragment hors contexte comme s'il s'agissait de la demande complète.
        pushRunningTurn(effectiveWorkflow, `${pendingClarification.originalRequest}\n\nPrécisions apportées : ${text}`);
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

  return { turns, sending, hasRunningTurn, error, conversationId, pendingClarification, workflowType, setWorkflowType, sendMessage, cancelSending, startNewConversation, loadConversation };
}
