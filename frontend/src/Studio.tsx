import { useState } from 'react';
import { useConversation } from './hooks/useConversation';
import StudioHeader from './components/StudioHeader';
import ChatThread from './components/ChatThread';
import ChatInput from './components/ChatInput';
import HistoryPanel from './components/HistoryPanel';

export default function Studio({ accessToken, userEmail }: { accessToken: string; userEmail: string }) {
  const [showHistory, setShowHistory] = useState(false);
  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
  const { turns, sending, error, sendMessage, cancelSending, startNewConversation, loadConversation } = useConversation(accessToken, API_URL);

  const handleResumeConversation = (conversationId: number) => {
    loadConversation(conversationId);
    setShowHistory(false);
  };

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
        <div style={{ padding: '12px 16px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#991b1b', marginBottom: '20px', fontWeight: '500' }}>
          ❌ {error}
        </div>
      )}

      <ChatThread turns={turns} />

      <div style={{ marginTop: '20px' }}>
        <ChatInput disabled={sending} onCancel={cancelSending} apiUrl={API_URL} accessToken={accessToken} onSend={sendMessage} />
      </div>
    </div>
  );
}
