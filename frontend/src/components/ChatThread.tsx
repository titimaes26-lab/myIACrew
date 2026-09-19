import { useEffect, useRef } from 'react';
import type { ChatTurn } from '../types';
import ChatMessage from './ChatMessage';

interface ChatThreadProps {
  turns: ChatTurn[];
  onRetry?: (turn: ChatTurn) => void;
  // Nom volontairement pas "sending" : recouvre aussi le cas où une clarification est en
  // attente de réponse (voir Studio.tsx), pas seulement un envoi réseau en cours.
  retryDisabled: boolean;
}

export default function ChatThread({ turns, onRetry, retryDisabled }: ChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns.length]);

  if (turns.length === 0) {
    return (
      <p style={{ color: '#666', textAlign: 'center', marginTop: '40px' }}>
        Décrivez votre besoin ci-dessous pour démarrer la conversation.
      </p>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {turns.map((turn) => (
        <ChatMessage key={turn.id} turn={turn} onRetry={onRetry} retryDisabled={retryDisabled} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
