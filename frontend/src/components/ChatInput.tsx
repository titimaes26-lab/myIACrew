import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { apiClient } from '../api';
import type { RepoTarget, RepoTargetSuggestion } from '../types';
import RepoTargetFields from './RepoTargetFields';
import { readChatDraft, writeChatDraft } from '../utils/chatDraft';

const MIN_TEXTAREA_HEIGHT = 52;
const MAX_TEXTAREA_HEIGHT = 200;

interface ChatInputProps {
  disabled: boolean;
  onCancel: () => void;
  apiUrl: string;
  accessToken: string;
  onSend: (text: string, repoTarget: RepoTarget) => void;
}

export default function ChatInput({ disabled, onCancel, apiUrl, accessToken, onSend }: ChatInputProps) {
  const [text, setText] = useState(readChatDraft);
  const [showRepoFields, setShowRepoFields] = useState(false);
  const [repoOwner, setRepoOwner] = useState('');
  const [repoName, setRepoName] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [repoSuggestions, setRepoSuggestions] = useState<RepoTargetSuggestion[]>([]);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    writeChatDraft(text);
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`;
  }, [text]);

  useEffect(() => {
    let cancelled = false;
    apiClient(apiUrl, accessToken)
      .listRepoTargets()
      .then((data) => {
        if (!cancelled) setRepoSuggestions(data);
      })
      .catch(() => {
        // Suggestions optionnelles : un échec de chargement ne doit pas bloquer la saisie.
      });
    return () => {
      cancelled = true;
    };
  }, [apiUrl, accessToken]);

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

  const applySuggestion = (suggestion: RepoTargetSuggestion) => {
    setRepoOwner(suggestion.repo_owner);
    setRepoName(suggestion.repo_name);
    setBaseBranch(suggestion.base_branch || 'main');
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
        <>
          {repoSuggestions.length > 0 && (
            <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
              {repoSuggestions.map((s) => (
                <button
                  key={`${s.repo_owner}/${s.repo_name}@${s.base_branch}`}
                  type="button"
                  onClick={() => applySuggestion(s)}
                  disabled={disabled}
                  style={{ fontSize: '12px', padding: '4px 10px', borderRadius: '999px', border: '1px solid #ccc', backgroundColor: '#f3f4f6', color: '#333', cursor: disabled ? 'not-allowed' : 'pointer' }}
                >
                  {s.repo_owner}/{s.repo_name}{s.base_branch ? `@${s.base_branch}` : ''}
                </button>
              ))}
            </div>
          )}
          <RepoTargetFields
            repoOwner={repoOwner}
            repoName={repoName}
            baseBranch={baseBranch}
            onRepoOwnerChange={setRepoOwner}
            onRepoNameChange={setRepoName}
            onBaseBranchChange={setBaseBranch}
            disabled={disabled}
          />
        </>
      )}

      <div style={{ display: 'flex', gap: '10px' }}>
        <textarea
          ref={textareaRef}
          rows={2}
          disabled={disabled}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Décrivez votre besoin ou répondez à l'agent..."
          style={{
            flex: 1,
            padding: '12px',
            borderRadius: '6px',
            border: '1px solid #ccc',
            fontSize: '15px',
            boxSizing: 'border-box',
            resize: 'none',
            overflowY: 'auto',
            minHeight: `${MIN_TEXTAREA_HEIGHT}px`,
            maxHeight: `${MAX_TEXTAREA_HEIGHT}px`,
            fontFamily: 'inherit',
          }}
        />
        {disabled ? (
          <button
            type="button"
            onClick={onCancel}
            style={{ padding: '0 20px', backgroundColor: '#fff', color: '#991b1b', border: '1px solid #fca5a5', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }}
          >
            🚫 Annuler
          </button>
        ) : (
          <button
            type="button"
            onClick={submit}
            disabled={!text.trim()}
            style={{ padding: '0 20px', backgroundColor: '#0070f3', color: '#fff', border: 'none', borderRadius: '6px', cursor: 'pointer', fontWeight: 'bold' }}
          >
            Envoyer
          </button>
        )}
      </div>
    </div>
  );
}
