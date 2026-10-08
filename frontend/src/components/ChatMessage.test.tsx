import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ChatTurn, LaunchControls } from '../types';
import ChatMessage from './ChatMessage';

const launch: LaunchControls = { onLaunch: vi.fn(), onCancel: vi.fn(), alwaysConfirm: true, onAlwaysConfirmChange: vi.fn(), disabled: false };
const base: ChatTurn = { id: 'temp-1', userMessage: 'Ajoute un bouton', status: 'clarifying', workflow: 'FEATURE', createdAt: new Date().toISOString() };

describe('ChatMessage', () => {
  it('annonce une attente de confirmation, pas des « précisions nécessaires », pour l’aperçu avant lancement', () => {
    render(<ChatMessage turn={{ ...base, launchPreview: { workflow: 'FEATURE', scope: 'PETIT', repoLabel: null } }} retryDisabled={false} launch={launch} />);
    expect(screen.getByRole('status')).toHaveTextContent('En attente de confirmation');
    expect(screen.queryByText(/Précisions nécessaires/)).toBeNull();
    expect(screen.getByRole('button', { name: /Lancer/ })).toBeInTheDocument();
  });

  it('garde « Précisions nécessaires » pour une vraie clarification', () => {
    render(<ChatMessage turn={{ ...base, questions: ['Quel écran ?'] }} retryDisabled={false} launch={launch} />);
    expect(screen.getByRole('status')).toHaveTextContent('Précisions nécessaires');
    expect(screen.queryByRole('button', { name: /Lancer/ })).toBeNull();
  });
});
