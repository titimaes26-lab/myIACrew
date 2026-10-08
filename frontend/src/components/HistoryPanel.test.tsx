import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import HistoryPanel from './HistoryPanel';

const base = {
  clarifications: null, result: null, conversation_id: 5, repo_owner: null, repo_name: null,
  created_at: '2026-10-01T10:00:00', updated_at: '2026-10-01T10:05:00', api_calls_count: null,
  rate_limit_hits: null, total_wait_time_seconds: null, current_step: null, workflow: 'BUGFIX',
  error_code: null, error_retryable: null,
};
const entries = [
  { ...base, id: 1, user_request: 'Première demande', status: 'success' },
  { ...base, id: 2, user_request: 'Deuxième demande', status: 'failed' },
  { ...base, id: 3, user_request: 'Troisième demande (en cours)', status: 'running' },
  { ...base, id: 4, user_request: 'Quatrième demande', status: 'success' },
];

type Handler = (url: string, init?: RequestInit) => Response | Promise<Response>;
let handler: Handler;
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status });

function checkbox(name: RegExp) {
  return screen.getByRole('checkbox', { name });
}

beforeEach(() => {
  handler = (url) => (url.endsWith('/api/history') ? json(entries) : json({}));
  vi.stubGlobal('fetch', vi.fn((url: string, init?: RequestInit) => Promise.resolve(handler(url, init))));
  vi.stubGlobal('confirm', vi.fn(() => true));
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function renderPanel() {
  render(<HistoryPanel apiUrl="" accessToken="t" onResumeConversation={() => undefined} />);
  await screen.findByText('Première demande');
}

describe('HistoryPanel — sélection multiple', () => {
  it('désactive la case d’une exécution en cours', async () => {
    await renderPanel();
    expect(checkbox(/Troisième demande/)).toBeDisabled();
    expect(checkbox(/Première demande/)).toBeEnabled();
  });

  it('coche des lignes, affiche le compteur et l’état indéterminé de « Tout sélectionner »', async () => {
    const user = userEvent.setup();
    await renderPanel();
    await user.click(checkbox(/Première demande/));
    expect(screen.getByText('1 sélectionnée')).toBeInTheDocument();
    const all = screen.getByRole('checkbox', { name: /Tout sélectionner/ }) as HTMLInputElement;
    expect(all.indeterminate).toBe(true);
    await user.click(all);
    expect(screen.getByText('3 sélectionnées')).toBeInTheDocument(); // 3 lignes sélectionnables sur 4
    expect(all.indeterminate).toBe(false);
    expect(all.checked).toBe(true);
  });

  it('Maj+clic coche la plage en sautant la ligne en cours', async () => {
    const user = userEvent.setup();
    await renderPanel();
    await user.click(checkbox(/Première demande/));
    await user.keyboard('{Shift>}');
    await user.click(checkbox(/Quatrième demande/));
    await user.keyboard('{/Shift}');
    expect(screen.getByText('3 sélectionnées')).toBeInTheDocument();
    expect(checkbox(/Troisième demande/)).not.toBeChecked();
  });

  it('« Annuler la sélection » vide tout', async () => {
    const user = userEvent.setup();
    await renderPanel();
    await user.click(checkbox(/Première demande/));
    await user.click(screen.getByRole('button', { name: 'Annuler la sélection' }));
    expect(screen.queryByText(/sélectionnée/)).not.toBeInTheDocument();
  });

  it('ne supprime rien quand la confirmation est refusée', async () => {
    const user = userEvent.setup();
    vi.stubGlobal('confirm', vi.fn(() => false));
    await renderPanel();
    await user.click(checkbox(/Première demande/));
    await user.click(screen.getByRole('button', { name: /Supprimer la sélection/ }));
    expect(screen.getByText('Première demande')).toBeInTheDocument();
    expect((fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.some(([url]) => String(url).includes('bulk-delete'))).toBe(false);
  });

  it('supprime le lot, signale la ligne ignorée et recharge la liste', async () => {
    const user = userEvent.setup();
    let listCalls = 0;
    handler = (url, init) => {
      if (url.endsWith('/api/history')) {
        listCalls += 1;
        return json(listCalls === 1 ? entries : entries.filter((e) => e.id !== 1));
      }
      if (url.endsWith('/bulk-delete')) {
        const ids: number[] = JSON.parse(String(init?.body)).ids;
        expect(ids.sort()).toEqual([1, 2]);
        return json({ deleted: [1], skipped: [{ id: 2, reason: 'running' }] });
      }
      return json({});
    };
    await renderPanel();
    await user.click(checkbox(/Première demande/));
    await user.click(checkbox(/Deuxième demande/));
    await user.click(screen.getByRole('button', { name: /Supprimer la sélection \(2\)/ }));

    await waitFor(() => expect(screen.queryByText('Première demande')).not.toBeInTheDocument());
    expect(screen.getByText(/1 supprimée, 1 ignorée : en cours/)).toBeInTheDocument();
    expect(screen.getByText('Deuxième demande')).toBeInTheDocument(); // la ligne ignorée reste
    expect(window.confirm).toHaveBeenCalledWith('Supprimer ces 2 exécutions de l\'historique ?');
  });
});

describe('HistoryPanel — erreurs', () => {
  it('propose « Réessayer » après un échec de chargement transitoire, et recharge la liste', async () => {
    const user = userEvent.setup();
    let calls = 0;
    handler = (url) => {
      if (!url.endsWith('/api/history')) return json({});
      calls += 1;
      return calls === 1
        ? json({ detail: 'Serveur occupé', code: 'SERVICE_UNAVAILABLE', retryable: true }, 503)
        : json(entries);
    };
    render(<HistoryPanel apiUrl="" accessToken="t" onResumeConversation={() => undefined} />);
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByText(/Serveur occupé/)).toBeInTheDocument();
    await user.click(within(alert).getByRole('button', { name: 'Réessayer' }));
    expect(await screen.findByText('Première demande')).toBeInTheDocument();
  });

  it('n’offre pas « Réessayer » pour une erreur permanente', async () => {
    handler = (url) => (url.endsWith('/api/history') ? json({ detail: 'Session invalide', code: 'UNAUTHORIZED', retryable: false }, 401) : json({}));
    render(<HistoryPanel apiUrl="" accessToken="t" onResumeConversation={() => undefined} />);
    const alert = await screen.findByRole('alert');
    expect(within(alert).queryByRole('button', { name: 'Réessayer' })).not.toBeInTheDocument();
  });
});
