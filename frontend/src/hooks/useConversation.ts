import { useEffect, useRef, useState } from 'react';
import { apiClient, type ExecuteAcceptedResponse } from '../api';
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
  // sendMessage n'a pas lui-même reçu sa réponse (voir applyExecuteAccepted). Sert plus bas à
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
          // applyExecuteAccepted) ne peut par construction jamais être retrouvé par
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
              setTurns((t) => t.map((turn) => {
                if (turn.status !== 'running') return turn;
                const matching = messages.find((m) => m.id === turn.id);
                if (matching) {
                  if (matching.status === 'failed') failedMessage = matching.result ?? 'Une erreur est survenue.';
                  // createdAt du turn LOCAL conservé (pas celui, forcément différent, de
                  // ExecutionHistory.created_at côté serveur — voir database.py, fixé au moment
                  // du INSERT, donc toujours postérieur de quelques centaines de ms à l'appel
                  // client à pushRunningTurn) : ChatThread.tsx s'appuie sur le fait que createdAt
                  // ne change JAMAIS après la création d'un tour, aussi bien pour sa clé React
                  // (key={turn.createdAt}, voir son commentaire) que pour identityKey (qui décide
                  // s'il faut recoller au bas du fil). Écraser createdAt ici démonterait/
                  // remonterait ce <ChatMessage> ET forcerait un recollage en bas au moment même
                  // où cette exécution se termine — y compris si l'utilisateur était remonté lire
                  // l'historique entretemps.
                  return { ...historyEntryToTurn(matching), createdAt: turn.createdAt };
                }
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
        .catch(() => {
          // Sondage périodique best-effort : une erreur réseau ponctuelle ne doit pas
          // interrompre l'exécution en cours, seulement priver cette itération de mise à jour.
        });
    };
    poll();
    const interval = setInterval(poll, PROGRESS_POLL_MS);
    return () => clearInterval(interval);
  }, [hasRunningTurn, runningTurnHasNumericId, conversationId, apiUrl, accessToken]);

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
  // via getConversationMessages. Si /api/execute est encore en vol (id encore le tempId local,
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
      setError(err instanceof Error ? err.message : 'Impossible de charger cette conversation.');
    }
  };

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
      ? { ...turn, id: data.id }
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
          const report = await api.qualify(clarifiedRequest, conversationId, controller.signal);
          if (myGeneration !== conversationGenerationRef.current) return;
          // confidence === 0 : repli "qualification impossible" du backend (DESIGN_AND_DEV par
          // défaut, le workflow le plus coûteux) — le type provisoire reste alors le meilleur choix.
          if (report.confidence !== 0) effectiveWorkflow = report.request_type;
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
        applyExecuteAccepted(tempId, data);
        return;
      }

      pushRunningTurn();
      const report = await api.qualify(text, conversationId, controller.signal);
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
      applyExecuteAccepted(tempId, data);
    } catch (err: unknown) {
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

  return { turns, sending, hasRunningTurn, error, conversationId, pendingClarification, workflowType, setWorkflowType, conversationResetSignal, sendMessage, cancelSending, startNewConversation, loadConversation };
}
