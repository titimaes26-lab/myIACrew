import type { FormEvent } from 'react';

interface QualificationFormProps {
  prompt: string;
  onPromptChange: (value: string) => void;
  onSubmit: (e: FormEvent) => void;
  disabled: boolean;
  loading: boolean;
}

export default function QualificationForm({ prompt, onPromptChange, onSubmit, disabled, loading }: QualificationFormProps) {
  return (
    <form onSubmit={onSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
      <label htmlFor="prompt"><b>Décrivez votre besoin ou bug :</b></label>
      <textarea
        id="prompt"
        rows={4}
        disabled={disabled}
        style={{ width: '100%', padding: '12px', borderRadius: '6px', border: '1px solid #ccc', fontSize: '15px', boxSizing: 'border-box' }}
        value={prompt}
        onChange={(e) => onPromptChange(e.target.value)}
        placeholder="Ex: Mon application affiche un écran blanc lors de l'ouverture du composant Dashboard..."
      />
      <button
        type="submit"
        disabled={disabled || !prompt.trim()}
        style={{
          padding: '12px',
          backgroundColor: loading ? '#93c5fd' : '#0070f3',
          color: '#fff',
          border: 'none',
          borderRadius: '6px',
          cursor: loading ? 'not-allowed' : 'pointer',
          fontWeight: 'bold',
        }}
      >
        {loading ? '🔍 Analyse et qualification en cours...' : '1. Examiner la demande'}
      </button>
    </form>
  );
}
