import { useCallback, useState } from 'react';
import { useConversation } from './hooks/useConversation';
import StudioHeader from './components/StudioHeader';
import ChatThread from './components/ChatThread';
import ChatInput from './components/ChatInput';
import HistoryPanel from './components/HistoryPanel';
import { isWorkflowType } from './constants/workflowTypes';
import type { ChatTurn } from './types';

export default function Studio({ accessToken, userEmail }: { accessToken: string; userEmail: string }) {
  const [showHistory, setShowHistory] = useState(false);
  const [retryDraft, setRetryDraft] = useState<{ text: string; nonce: number } | null>(null);
  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
  const { turns, sending, hasRunningTurn, error, pendingClarification, workflowType, setWorkflowType, sendMessage, cancelSending, startNewConversation, loadConversation } = useConversation(accessToken, API_URL);
  // `sending` seul (envoi en vol DANS cette session) ne suffit pas : un tour repris depuis
  // l'Historique peut encore être "running" côté serveur sans que CETTE session l'ait
  // elle-même envoyé (sending resterait alors false). Sans bloquer la saisie dans ce cas
  // aussi, un second message pourrait être envoyé pendant que /api/qualify tourne encore
  // pour lui — avant que le contrôle de concurrence d'une-exécution-à-la-fois de
  // /api/execute (backend/main.py, HTTP 409) ne puisse s'appliquer — créant temporairement
  // DEUX tours locaux "running" à la fois. Le sondage de progression de useConversation.ts
  // ne sait alors pas auquel des deux rattacher l'étape reçue (il n'y en a normalement
  // jamais qu'un), et lui appliquerait à tort l'étape de l'AUTRE exécution.
  const busy = sending || hasRunningTurn;
  // "Relancer" préremplit la zone de saisie avec le texte d'UN AUTRE tour (voir handleRetry) :
  // envoyé pendant qu'une clarification est en attente, ce texte serait alors traité comme LA
  // RÉPONSE à cette clarification plutôt que comme la nouvelle demande affichée (sendMessage
  // route tout texte tapé pendant pendingClarification vers cette clarification, quel que soit
  // son contenu réel). Désactiver "Relancer" dans ce cas empêche la seule action qui peut
  // amener ce texte incohérent dans la zone de saisie.
  const retryDisabled = busy || pendingClarification !== null;

  const handleResumeConversation = (conversationId: number) => {
    loadConversation(conversationId);
    setShowHistory(false);
  };

  // Préremplit la zone de saisie avec la demande d'origine plutôt que de la renvoyer
  // directement : un échec vient souvent d'un problème qui mérite d'être corrigé avant de
  // relancer (repo cible, formulation ambiguë...), donc laisser l'utilisateur relire/modifier
  // avant l'envoi plutôt que de relancer à l'identique en un clic.
  //
  // useCallback (pas une simple fonction inline) : cette fonction est transmise jusqu'à CHAQUE
  // ChatMessage (via ChatThread), qui est enveloppé dans React.memo précisément pour ne
  // re-rendre que le tour dont l'objet a changé. Une nouvelle référence de fonction à chaque
  // rendu de Studio (déclenché ici environ toutes les 3s pendant une exécution, par le sondage
  // de progression de useConversation.ts) casserait cette comparaison superficielle et
  // re-rendrait TOUS les tours à chaque sondage, pas seulement celui en cours.
  const handleRetry = useCallback((turn: ChatTurn) => {
    setRetryDraft({ text: turn.userMessage, nonce: Date.now() });
    // Reprend le même type de workflow que le tour en échec (déjà connu, pas besoin de
    // repasser par /api/qualify) uniquement s'il s'agit d'une des 4 catégories reconnues :
    // turn.workflow est un simple `string` reçu du backend, jamais garanti correspondre à
    // WorkflowType (ex: 'AUTO' n'apparaît jamais sur un turn puisque c'est justement la
    // détection automatique qui produit ces catégories).
    if (turn.workflow && isWorkflowType(turn.workflow)) {
      setWorkflowType(turn.workflow);
    }
  }, [setWorkflowType]);

  return (
    // #root (index.css) est display:flex ; ce div en est le seul enfant, donc un flex
    // item. Sans width:100%/minWidth:0, son min-width par défaut ("auto") vaut le
    // max-content de son contenu (en-tête, bulle de message longue…), l'empêchant de
    // rétrécir sous cette largeur et forçant la page entière à déborder sur mobile.
    <div style={{ maxWidth: '850px', width: '100%', minWidth: 0, margin: '40px auto', fontFamily: 'system-ui, sans-serif', padding: '20px', boxSizing: 'border-box', color: '#333' }}>
      <StudioHeader
        userEmail={userEmail}
        historyOpen={showHistory}
        onToggleHistory={() => setShowHistory((open) => !open)}
      />

      <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: '10px' }}>
        <button
          type="button"
          onClick={startNewConversation}
          style={{ padding: '6px 12px', backgroundColor: '#f3f4f6', color: '#333', border: '1px solid #ccc', borderRadius: '6px', cursor: 'pointer', fontSize: '13px' }}
        >
          ✨ Nouvelle conversation
        </button>
      </div>

      {showHistory && (
        <HistoryPanel apiUrl={API_URL} accessToken={accessToken} onResumeConversation={handleResumeConversation} />
      )}

      {error && (
        <div role="alert" style={{ padding: '12px 16px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#991b1b', marginBottom: '20px', fontWeight: '500' }}>
          ❌ {error}
        </div>
      )}

      <ChatThread turns={turns} onRetry={handleRetry} retryDisabled={retryDisabled} />

      <div style={{ marginTop: '20px' }}>
        <ChatInput
          disabled={busy}
          cancellable={sending}
          onCancel={cancelSending}
          apiUrl={API_URL}
          accessToken={accessToken}
          onSend={sendMessage}
          workflowType={workflowType}
          onWorkflowTypeChange={setWorkflowType}
          retryDraft={retryDraft}
        />
      </div>
    </div>
  );
}
