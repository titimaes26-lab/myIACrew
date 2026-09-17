import { useEffect, useRef } from 'react';
import type { ChatTurn } from '../types';
import ChatMessage from './ChatMessage';

export default function ChatThread({ turns }: { turns: ChatTurn[] }) {
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
        <ChatMessage key={turn.id} turn={turn} />
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
