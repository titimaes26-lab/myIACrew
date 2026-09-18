import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { apiClient } from '../api';
import type { RepoTarget, RepoTargetSuggestion } from '../types';
import RepoTargetFields from './RepoTargetFields';
import { readChatDraft, writeChatDraft } from '../utils/chatDraft';
import { WORKFLOW_TYPE_OPTIONS, type WorkflowType } from '../constants/workflowTypes';

const MIN_TEXTAREA_HEIGHT = 52;
const MAX_TEXTAREA_HEIGHT = 200;

interface ChatInputProps {
  disabled: boolean;
  onCancel: () => void;
  apiUrl: string;
  accessToken: string;
  onSend: (text: string, repoTarget: RepoTarget) => void;
  // Contrôlé par useConversation (pas un état local à ce composant) : ce hook a besoin de
  // pouvoir remettre ce choix à 'AUTO' à des limites que ce composant ne connaît pas
  // (nouvelle conversation, reprise d'une conversation différente) sans quoi un choix
  // manuel resterait actif en silence pour une demande sans rapport avec celle où il avait
  // été fait.
  workflowType: WorkflowType;
  onWorkflowTypeChange: (workflowType: WorkflowType) => void;
}

export default function ChatInput({ disabled, onCancel, apiUrl, accessToken, onSend, workflowType, onWorkflowTypeChange }: ChatInputProps) {
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
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ fontSize: '13px', color: '#666', display: 'flex', alignItems: 'center', gap: '6px' }}>
          Type de demande :
          <select
            value={workflowType}
            onChange={(e) => onWorkflowTypeChange(e.target.value as WorkflowType)}
            disabled={disabled}
            // Ce choix persiste d'un message à l'autre (jamais réinitialisé à 'AUTO' après
            // l'envoi) pour permettre plusieurs messages de suite dans un même mode manuel
            // sans avoir à le re-sélectionner à chaque fois. Un style distinct quand il
            // n'est pas 'AUTO' est donc nécessaire : sans lui, un choix manuel oublié depuis
            // un message précédent resterait actif en silence pour une demande sans rapport,
            // qui contournerait alors /api/qualify sans que rien ne le signale à l'écran.
            style={{
              padding: '4px 8px',
              fontSize: '13px',
              borderRadius: '6px',
              border: workflowType === 'AUTO' ? '1px solid #ccc' : '1px solid #0070f3',
              backgroundColor: workflowType === 'AUTO' ? '#fff' : '#eff6ff',
              color: workflowType === 'AUTO' ? '#333' : '#0070f3',
              fontWeight: workflowType === 'AUTO' ? 'normal' : 600,
              cursor: disabled ? 'not-allowed' : 'pointer',
            }}
          >
            {WORKFLOW_TYPE_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.icon} {opt.label}
              </option>
            ))}
          </select>
        </label>

        <button
          type="button"
          onClick={() => setShowRepoFields((v) => !v)}
          style={{ padding: '4px 10px', fontSize: '13px', backgroundColor: 'transparent', border: '1px solid #ccc', borderRadius: '6px', cursor: 'pointer', color: '#666' }}
        >
          🔗 {repoOwner && repoName ? `${repoOwner}/${repoName}` : 'Repository GitHub cible (optionnel)'}
        </button>
      </div>

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
