import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ChatTurn, LaunchControls } from '../types';
import ChatMessage from './ChatMessage';

vi.mock('./MarkdownRenderer', () => ({ default: ({ content }: { content: string }) => <pre>{content}</pre> }));

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

  it('affiche le modèle des agents déjà terminés pendant l’exécution, sans laisser le marqueur dans le texte', async () => {
    const completedAgents = {
      'Lead Product / Game Designer': '## Lead Product / Game Designer\n\n<!--agent-duration:4.00-->\n<!--agent-model:gemini-3.8-flash-->\n\nspec du panier',
    };
    render(<ChatMessage turn={{ ...base, status: 'running', completedAgents }} retryDisabled={false} launch={launch} />);
    expect(await screen.findByText('🤖 gemini-3.8-flash', {}, { timeout: 5000 })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('agent-model');
  });
});
