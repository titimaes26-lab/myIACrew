import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ChatInput from './ChatInput';

const suggestions = [
  { repo_owner: 'titimaes26-lab', repo_name: 'PlatformGame', base_branch: 'main' },
  { repo_owner: 'autre', repo_name: 'projet', base_branch: 'dev' },
];

let repoTargets: () => Promise<Response>;

beforeEach(() => {
  window.localStorage.clear();
  repoTargets = () => Promise.resolve(new Response(JSON.stringify(suggestions), { status: 200 }));
  vi.stubGlobal('fetch', vi.fn((url: string) => (String(url).endsWith('/api/repo-targets')
    ? repoTargets()
    : Promise.resolve(new Response('{}', { status: 404 })))));
});

afterEach(() => { vi.unstubAllGlobals(); });

function renderInput(overrides: Partial<React.ComponentProps<typeof ChatInput>> = {}) {
  const props = {
    disabled: false, cancellable: false, onCancel: vi.fn(), apiUrl: 'http://api', accessToken: 'token', onSend: vi.fn(),
    workflowType: 'AUTO' as const, onWorkflowTypeChange: vi.fn(), retryDraft: null, ...overrides,
  };
  const view = render(<ChatInput {...props} />);
  return { ...view, props, rerenderWith: (next: Partial<typeof props>) => view.rerender(<ChatInput {...props} {...next} />) };
}

const box = () => screen.getByRole('textbox', { name: 'Votre message' });

describe('ChatInput — envoi', () => {
  it('envoie le texte et le repository par le bouton, vide la zone et replie le panneau', async () => {
    const { props } = renderInput();
    await waitFor(() => expect(screen.getByRole('button', { name: '🔗 titimaes26-lab/PlatformGame' })).toBeInTheDocument()); // préremplissage
    await userEvent.type(box(), 'Corrige le bug');
    await userEvent.click(screen.getByRole('button', { name: 'Envoyer' }));
    expect(props.onSend).toHaveBeenCalledWith('Corrige le bug', { owner: 'titimaes26-lab', name: 'PlatformGame', branch: 'main' });
    expect(box()).toHaveValue('');
    expect(screen.getByRole('button', { name: '🔗 titimaes26-lab/PlatformGame' })).toHaveAttribute('aria-expanded', 'false');
  });

  it('Entrée envoie, Maj+Entrée ajoute une ligne, un texte vide ne s’envoie pas', async () => {
    const { props } = renderInput();
    await userEvent.type(box(), '{Enter}');
    expect(props.onSend).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Envoyer' })).toBeDisabled();
    await userEvent.type(box(), 'ligne 1{Shift>}{Enter}{/Shift}ligne 2');
    expect(props.onSend).not.toHaveBeenCalled();
    expect(box()).toHaveValue('ligne 1\nligne 2');
    await userEvent.type(box(), '{Enter}');
    expect(props.onSend).toHaveBeenCalledTimes(1);
  });

  it('retrouve le brouillon enregistré et le garde à jour', async () => {
    window.localStorage.setItem('myiacrew:chat-draft', 'brouillon');
    renderInput();
    expect(box()).toHaveValue('brouillon');
    await userEvent.type(box(), ' suite');
    expect(window.localStorage.getItem('myiacrew:chat-draft')).toBe('brouillon suite');
  });
});

describe('ChatInput — états occupés', () => {
  it('occupé et annulable : masque la zone de texte et propose « Annuler »', async () => {
    const { props } = renderInput({ disabled: true, cancellable: true });
    expect(screen.queryByRole('textbox', { name: 'Votre message' })).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /Annuler/ }));
    expect(props.onCancel).toHaveBeenCalledTimes(1);
  });

  it('occupé sans rien à annuler : bouton « En attente » désactivé', () => {
    renderInput({ disabled: true, cancellable: false });
    expect(screen.getByRole('button', { name: /En attente/ })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /Annuler/ })).toBeNull();
  });
});

describe('ChatInput — type de demande', () => {
  it('signale un choix manuel et remonte le changement', async () => {
    const { props, rerenderWith } = renderInput();
    const select = screen.getByRole('combobox');
    expect(select).not.toHaveClass('field--accent');
    await userEvent.selectOptions(select, 'BUGFIX');
    expect(props.onWorkflowTypeChange).toHaveBeenCalledWith('BUGFIX');
    rerenderWith({ workflowType: 'BUGFIX' });
    expect(screen.getByRole('combobox')).toHaveClass('field--accent');
  });
});

describe('ChatInput — « Relancer »', () => {
  it('préremplit la zone et lui donne le focus, y compris pour deux relances identiques', async () => {
    const { rerenderWith } = renderInput();
    rerenderWith({ retryDraft: { text: 'Ajoute un panier', nonce: 1 } });
    expect(box()).toHaveValue('Ajoute un panier');
    expect(box()).toHaveFocus();
    await userEvent.clear(box());
    await userEvent.type(box(), 'autre chose');
    rerenderWith({ retryDraft: { text: 'Ajoute un panier', nonce: 2 } });   // même texte, nouveau clic
    expect(box()).toHaveValue('Ajoute un panier');
  });

  it('ne réapplique pas un brouillon déjà appliqué lors d’un simple rendu', async () => {
    const { rerenderWith } = renderInput({ retryDraft: { text: 'initial', nonce: 1 } });
    await userEvent.clear(box());
    await userEvent.type(box(), 'modifié');
    rerenderWith({ retryDraft: { text: 'initial', nonce: 1 } });
    expect(box()).toHaveValue('modifié');
  });
});

describe('ChatInput — repository cible', () => {
  it('préremplit avec la cible la plus récente et ouvre le panneau', async () => {
    renderInput();
    await waitFor(() => expect(screen.getByPlaceholderText(/owner/)).toHaveValue('titimaes26-lab'));
    expect(screen.getByRole('button', { name: '🔗 titimaes26-lab/PlatformGame' })).toHaveAttribute('aria-expanded', 'true');
  });

  it('n’applique aucune suggestion si l’utilisateur a déjà touché au panneau avant la réponse', async () => {
    let resolve: (response: Response) => void = () => undefined;
    repoTargets = () => new Promise<Response>((done) => { resolve = done; });
    renderInput();
    await userEvent.click(screen.getByRole('button', { name: /Repository GitHub cible/ }));   // ouvre, avant la réponse
    await act(async () => { resolve(new Response(JSON.stringify(suggestions), { status: 200 })); });
    expect(screen.getByPlaceholderText(/owner/)).toHaveValue('');
  });

  it('ne réapplique pas le préremplissage quand le jeton est rafraîchi', async () => {
    const { rerenderWith } = renderInput();
    await waitFor(() => expect(screen.getByPlaceholderText(/owner/)).toHaveValue('titimaes26-lab'));
    await userEvent.clear(screen.getByPlaceholderText(/owner/));
    await userEvent.type(screen.getByPlaceholderText(/owner/), 'moi');
    rerenderWith({ accessToken: 'nouveau-jeton' });
    await act(async () => { await Promise.resolve(); });
    expect(screen.getByPlaceholderText(/owner/)).toHaveValue('moi');
  });

  it('un clic sur une suggestion remplit les trois champs', async () => {
    renderInput();
    await waitFor(() => expect(screen.getByRole('button', { name: 'autre/projet@dev' })).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: 'autre/projet@dev' }));
    expect(screen.getByPlaceholderText(/owner/)).toHaveValue('autre');
    expect(screen.getByDisplayValue('projet')).toBeInTheDocument();
    expect(screen.getByDisplayValue('dev')).toBeInTheDocument();
  });

  it('un échec de chargement des suggestions ne bloque pas la saisie', async () => {
    repoTargets = () => Promise.reject(new Error('réseau'));
    const { props } = renderInput();
    await userEvent.type(box(), 'Bonjour');
    await userEvent.click(screen.getByRole('button', { name: 'Envoyer' }));
    expect(props.onSend).toHaveBeenCalledWith('Bonjour', { owner: '', name: '', branch: 'main' });
  });
});
