import { useState, type KeyboardEvent } from 'react';
import type { RepoTarget } from '../types';
import RepoTargetFields from './RepoTargetFields';

interface ChatInputProps {
  disabled: boolean;
  onSend: (text: string, repoTarget: RepoTarget) => void;
}

export default function ChatInput({ disabled, onSend }: ChatInputProps) {
  const [text, setText] = useState('');
  const [showRepoFields, setShowRepoFields] = useState(false);
  const [repoOwner, setRepoOwner] = useState('');
  const [repoName, setRepoName] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');

  const submit = () => {
    if (!text.trim() || disabled) return;
    onSend(text, { owner: repoOwner.trim(), name: repoName.trim(), branch: baseBranch.trim() });
    setText('');
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <button
        type="button"
        onClick={() => setShowRepoFields((v) => !v)}
        style={{ alignSelf: 'flex-start', padding: '4px 10px', fontSize: '13px', backgroundColor: 'transparent', border: '1px solid #ccc', borderRadius: '6px', cursor: 'pointer', color: '#666' }}
      >
        🔗 {repoOwner && repoName ? `${repoOwner}/${repoName}` : 'Repository GitHub cible (optionnel)'}
      </button>

      {showRepoFields && (
        <RepoTargetFields
          repoOwner={repoOwner}
          repoName={repoName}
          baseBranch={baseBranch}
          onRepoOwnerChange={setRepoOwner}
          onRepoNameChange={setRepoName}
          onBaseBranchChange={setBaseBranch}
          disabled={disabled}
        />
      )}

      <div style={{ display: 'flex', gap: '10px' }}>
        <textarea
          rows={2}
          disabled={disabled}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Décrivez votre besoin ou répondez à l'agent..."
          style={{ flex: 1, padding: '12px', borderRadius: '6px', border: '1px solid #ccc', fontSize: '15px', boxSizing: 'border-box', resize: 'vertical', fontFamily: 'inherit' }}
        />
        <button
          type="button"
          onClick={submit}
          disabled={disabled || !text.trim()}
          style={{ padding: '0 20px', backgroundColor: disabled ? '#93c5fd' : '#0070f3', color: '#fff', border: 'none', borderRadius: '6px', cursor: disabled ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}
        >
          Envoyer
        </button>
      </div>
    </div>
  );
}
