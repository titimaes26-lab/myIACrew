import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AgentRunView, ExecutionRow, ExecutionsPage, MetricsSummary } from '../../types';
import MetricsPanel from './MetricsPanel';

const executions = {
  total: 20, success: 18, failed: 2, median_duration_seconds: 82, avg_llm_calls: 9, avg_tokens: 12000,
  token_executions: 18, rate_limit_hits: 3, wait_seconds: 40, auto_retried: 0, resumed: 0,
  qa_verdicts: { GO: 0, GO_AVEC_RESERVES: 0, NO_GO: 0 }, total_cost: null, avg_cost: null,
};

function summary(overrides: Partial<MetricsSummary> = {}): MetricsSummary {
  return {
    period_days: 30, workflow: null, executions, agents: [], failures: [],
    daily: [
      { date: '2026-10-01', executions: 4, failed: 1, llm_calls: 12, tokens: 3400, median_duration_seconds: 95, qa_total: 0, qa_go: 0 },
      { date: '2026-10-02', executions: 2, failed: 0, llm_calls: 6, tokens: 1500, median_duration_seconds: 60, qa_total: 0, qa_go: 0 },
    ],
    truncated: false,
    previous: { ...executions, total: 10, success: 8, failed: 2, median_duration_seconds: 100, avg_llm_calls: 6, rate_limit_hits: 0 },
    ...overrides,
  };
}

beforeAll(() => {
  // jsdom n'a pas ResizeObserver (utilisé pour mesurer la largeur des graphiques).
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
});

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
});

const emptyPage: ExecutionsPage = { total: 0, items: [] };

// Route la réponse selon l'adresse : synthèse, liste des exécutions, détail par agent.
function respondWith(data: MetricsSummary, page: ExecutionsPage = emptyPage, runs: AgentRunView[] = []) {
  (fetch as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string) => {
    const body = url.includes('/api/metrics/executions') ? page : url.includes('/agent-runs') ? runs : data;
    return Promise.resolve(new Response(JSON.stringify(body), { status: 200 }));
  });
}

// Le libellé d'une tuile peut aussi apparaître ailleurs (sélecteur de métrique, tableau) : on cible la tuile.
function tile(label: string) {
  const element = screen.getAllByText(label).find((candidate) => candidate.classList.contains('viz-tile-label'));
  return element?.closest('.viz-tile') as HTMLElement;
}

describe('MetricsPanel — comparaison à la période précédente', () => {
  it('affiche l’écart de chaque tuile, lu comme mieux ou moins bien selon la métrique', async () => {
    respondWith(summary());
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Exécutions terminées');
    // durée médiane 100 s -> 82 s : baisse de 18 %, c'est une amélioration
    expect(within(tile('Durée médiane')).getByText('▼ −18 %').parentElement).toHaveClass('viz-delta--good');
    // appels LLM 6 -> 9 : hausse de 50 %, c'est une dégradation
    expect(within(tile('Appels LLM')).getByText('▲ +50 %').parentElement).toHaveClass('viz-delta--bad');
    // taux de succès 80 % -> 90 % : +10 points, amélioration
    expect(within(tile('Taux de succès')).getByText('▲ +10 pts').parentElement).toHaveClass('viz-delta--good');
    // nombre d'exécutions : neutre
    expect(within(tile('Exécutions terminées')).getByText('▲ +100 %').parentElement).toHaveClass('viz-delta--neutral');
    // pauses de quota 0 -> 3 : pas de variation relative depuis zéro, donc aucun écart
    expect(tile('Pauses quota').querySelector('.viz-delta')).toBeNull();
    expect(screen.getByText(/par rapport aux 30 jours précédents/)).toBeInTheDocument();
  });

  it('donne le sens complet de chaque écart aux lecteurs d’écran', async () => {
    respondWith(summary());
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Exécutions terminées');
    expect(within(tile('Durée médiane')).getByText('Durée médiane : en baisse de 18 % par rapport à la période précédente')).toBeInTheDocument();
  });

  it('n’affiche aucun écart sans période précédente comparable', async () => {
    respondWith(summary({ previous: null }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Exécutions terminées');
    expect(document.querySelector('.viz-delta')).toBeNull();
    expect(screen.queryByText(/jours précédents/)).not.toBeInTheDocument();
  });

  it('signale la troncature quand la limite d’exécutions est atteinte', async () => {
    respondWith(summary({ truncated: true, previous: null }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    expect(await screen.findByText(/Trop d'exécutions sur cette période/)).toBeInTheDocument();
  });
});

describe('MetricsPanel — limites', () => {
  it('explique l’absence de comparaison quand la limite coupe la période précédente', async () => {
    respondWith(summary({ previous: null, comparison_limited: true, truncated: false }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    expect(await screen.findByText(/Comparaison indisponible : trop d'exécutions sur la période précédente/)).toBeInTheDocument();
    expect(screen.queryByText(/Trop d'exécutions sur cette période/)).not.toBeInTheDocument();
  });
});

describe('MetricsPanel — tendances quotidiennes', () => {
  it('change de métrique : le titre, les valeurs et le tableau suivent', async () => {
    const user = userEvent.setup();
    respondWith(summary());
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    expect(await screen.findByText('Appels LLM par jour')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Durée médiane' }));
    expect(screen.getByText('Durée médiane par jour')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Durée médiane' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('group', { name: /1 oct\. : Durée médiane 1m 35s/ })).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Taux de succès' }));
    expect(screen.getByText('Taux de succès par jour')).toBeInTheDocument();
    expect(screen.getByRole('group', { name: /1 oct\. : Taux de succès 75 %/ })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: /2 oct\. : Taux de succès 100 %/ })).toBeInTheDocument();
  });

  it('trace un liseré pour une vraie valeur à zéro, et rien pour un jour sans donnée', async () => {
    const user = userEvent.setup();
    respondWith(summary({
      daily: [
        { date: '2026-10-01', executions: 2, failed: 2, llm_calls: 4, tokens: 0, median_duration_seconds: 50, qa_total: 0, qa_go: 0 },
        { date: '2026-10-02', executions: 0, failed: 0, llm_calls: 0, tokens: 0, median_duration_seconds: null, qa_total: 0, qa_go: 0 },
      ],
    }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Appels LLM par jour');
    await user.click(screen.getByRole('button', { name: 'Taux de succès' }));
    expect(document.querySelectorAll('.viz-colbar--zero')).toHaveLength(1); // 0 % de succès le 1er : vraie valeur
    expect(document.querySelectorAll('.viz-colbar')).toHaveLength(1);       // le 2 : aucune donnée, aucune colonne
  });

  it('ne trace pas de colonne un jour sans durée mesurée', async () => {
    const user = userEvent.setup();
    respondWith(summary({
      daily: [
        { date: '2026-10-01', executions: 4, failed: 0, llm_calls: 12, tokens: 0, median_duration_seconds: 95, qa_total: 0, qa_go: 0 },
        { date: '2026-10-02', executions: 1, failed: 0, llm_calls: 0, tokens: 0, median_duration_seconds: null, qa_total: 0, qa_go: 0 },
      ],
    }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Appels LLM par jour');
    await user.click(screen.getByRole('button', { name: 'Durée médiane' }));
    expect(screen.getByRole('group', { name: /2 oct\. : Durée médiane aucune donnée/ })).toBeInTheDocument();
    expect(document.querySelectorAll('.viz-colbar')).toHaveLength(1);
  });
});


// --- Point 8 : coût, raisonnement, qualité ------------------------------------------------------

describe('MetricsPanel — coût, raisonnement et qualité', () => {
  it('affiche le coût estimé seulement quand un tarif est configuré', async () => {
    respondWith(summary({ currency: '€', executions: { ...executions, avg_cost: 0.0123, total_cost: 0.246 }, previous: null }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Exécutions terminées');
    expect(within(tile('Coût estimé')).getByText(/0,0123 €/)).toBeInTheDocument();
    expect(within(tile('Coût estimé')).getByText(/0,2460 € au total/)).toBeInTheDocument();
  });

  it('masque la tuile de coût sans tarif', async () => {
    respondWith(summary());
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Exécutions terminées');
    expect(screen.queryByText('Coût estimé')).not.toBeInTheDocument();
  });

  it('montre les relances automatiques et les reprises avec leur part des exécutions', async () => {
    respondWith(summary({ executions: { ...executions, auto_retried: 5, resumed: 2 }, previous: null }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Exécutions terminées');
    expect(within(tile('Relances automatiques')).getByText('25 % des exécutions')).toBeInTheDocument();
    expect(within(tile("Reprises à l'étape")).getByText('10 % des exécutions')).toBeInTheDocument();
  });

  it('lit une hausse du taux de relance comme une dégradation (en points)', async () => {
    respondWith(summary({
      executions: { ...executions, auto_retried: 5 },
      previous: { ...executions, total: 10, auto_retried: 0 },
    }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Exécutions terminées');
    expect(within(tile('Relances automatiques')).getByText('▲ +25 pts').parentElement).toHaveClass('viz-delta--bad');
  });

  it('affiche la répartition des verdicts QA, ou explique leur absence', async () => {
    respondWith(summary({ executions: { ...executions, qa_verdicts: { GO: 12, GO_AVEC_RESERVES: 3, NO_GO: 1 } }, previous: null }));
    const { unmount } = render(<MetricsPanel apiUrl="" accessToken="t" />);
    const card = (await screen.findByText('Verdict QA')).closest('section') as HTMLElement;
    expect(within(card).getByText('12 · 75 %')).toBeInTheDocument();
    expect(within(card).getByText('1 · 6 %')).toBeInTheDocument();
    unmount();

    respondWith(summary());
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    expect(await screen.findByText(/Aucun verdict QA sur cette période/)).toBeInTheDocument();
  });

  it('suit la part de verdicts GO dans le temps', async () => {
    const user = userEvent.setup();
    respondWith(summary({
      daily: [
        { date: '2026-10-01', executions: 4, failed: 0, llm_calls: 12, tokens: 0, median_duration_seconds: 95, qa_total: 4, qa_go: 3 },
        { date: '2026-10-02', executions: 2, failed: 0, llm_calls: 6, tokens: 0, median_duration_seconds: 60, qa_total: 0, qa_go: 0 },
      ],
    }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Appels LLM par jour');
    await user.click(screen.getByRole('button', { name: /Verdict QA « GO »/ }));
    expect(screen.getByRole('group', { name: /1 oct\. : Verdict QA « GO » 75 %/ })).toBeInTheDocument();
    expect(screen.getByRole('group', { name: /2 oct\. : Verdict QA « GO » aucune donnée/ })).toBeInTheDocument();
  });
});

// --- Point 4 et 5 : tableau des exécutions et chronologie -----------------------------------------

const row = (overrides: Partial<ExecutionRow> = {}): ExecutionRow => ({
  id: 7, conversation_id: 3, user_request: 'Corrige le panier', workflow: 'BUGFIX', status: 'success',
  created_at: '2026-10-01T10:00:00', duration_seconds: 120, llm_calls: 9, tokens: 12000, error_code: null,
  qa_verdict: 'GO', attempts: 1, reused_steps: 0, repo: null, ...overrides,
});

const agentRun = (agent: string, label: string, duration: number): AgentRunView => ({
  agent, label, status: 'completed', duration_seconds: duration, llm_calls: 2, llm_errors: 0, tokens_known: true,
  prompt_tokens: 10, completion_tokens: 5, total_tokens: 15, tool_calls: 1, tool_errors: 0,
});

function executionCalls(): string[] {
  return (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls.map(([url]) => String(url)).filter((url) => url.includes('/api/metrics/executions'));
}

describe('MetricsPanel — tableau des exécutions', () => {
  it('liste les exécutions avec leurs mesures, leur verdict et leurs badges de raisonnement', async () => {
    respondWith(summary(), { total: 2, items: [row(), row({ id: 8, status: 'failed', qa_verdict: null, attempts: 2, reused_steps: 2, llm_calls: null, tokens: null, duration_seconds: null, user_request: 'Ajoute un thème' })] });
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    const card = (await screen.findByRole('region', { name: 'Exécutions récentes' }));
    expect(await within(card).findByText('Corrige le panier')).toBeInTheDocument();
    expect(within(card).getByText('GO')).toBeInTheDocument();
    expect(within(card).getByText('↻ relance')).toBeInTheDocument();
    expect(within(card).getByText('⏩ reprise')).toBeInTheDocument();
    expect(within(card).getByText('2m 0s')).toBeInTheDocument();
  });

  it('trie côté serveur au clic sur une colonne, en inversant le sens au second clic', async () => {
    const user = userEvent.setup();
    respondWith(summary(), { total: 1, items: [row()] });
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    const card = await screen.findByRole('region', { name: 'Exécutions récentes' });
    await within(card).findByText('Corrige le panier');
    await user.click(within(card).getByRole('button', { name: 'Durée' }));
    await waitFor(() => expect(executionCalls().at(-1)).toContain('sort=duration&order=desc'));
    expect(within(card).getByRole('columnheader', { name: /Durée/ })).toHaveAttribute('aria-sort', 'descending');
    await user.click(within(card).getByRole('button', { name: /Durée/ }));
    await waitFor(() => expect(executionCalls().at(-1)).toContain('sort=duration&order=asc'));
    expect(within(card).getByRole('columnheader', { name: /Durée/ })).toHaveAttribute('aria-sort', 'ascending');
  });

  it('filtre par statut côté serveur', async () => {
    const user = userEvent.setup();
    respondWith(summary(), { total: 1, items: [row({ status: 'failed' })] });
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    const card = await screen.findByRole('region', { name: 'Exécutions récentes' });
    await within(card).findByText('Corrige le panier');
    await user.click(within(card).getByRole('button', { name: 'Échecs' }));
    await waitFor(() => expect(executionCalls().at(-1)).toContain('status=failed'));
    expect(within(card).getByRole('button', { name: 'Échecs' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('pagine, et revient à la première page quand le filtre change', async () => {
    const user = userEvent.setup();
    const items = Array.from({ length: 10 }, (_, i) => row({ id: i + 1, user_request: `Demande ${i + 1}` }));
    respondWith(summary(), { total: 25, items });
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    const card = await screen.findByRole('region', { name: 'Exécutions récentes' });
    expect(await within(card).findByText('1–10 sur 25')).toBeInTheDocument();
    expect(within(card).getByRole('button', { name: /Précédentes/ })).toBeDisabled();
    await user.click(within(card).getByRole('button', { name: /Suivantes/ }));
    await waitFor(() => expect(executionCalls().at(-1)).toContain('offset=10'));
    expect(await within(card).findByText('11–20 sur 25')).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: 'Échecs' }));
    await waitFor(() => expect(executionCalls().at(-1)).toContain('offset=0'));
  });

  it('dit quand aucune exécution ne correspond aux filtres', async () => {
    respondWith(summary(), { total: 0, items: [] });
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    expect(await screen.findByText('Aucune exécution ne correspond à ces filtres.')).toBeInTheDocument();
  });

  it('déplie une ligne : chronologie (étapes puis attente) et détail par agent', async () => {
    const user = userEvent.setup();
    respondWith(summary(), { total: 1, items: [row({ duration_seconds: 200 })] }, [agentRun('design', 'Conception', 30), agentRun('qa', 'QA', 20)]);
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    const card = await screen.findByRole('region', { name: 'Exécutions récentes' });
    await user.click(await within(card).findByRole('button', { name: /Voir le détail de l'exécution/ }));
    const timeline = await within(card).findByRole('group', { name: "Chronologie de l'exécution" });
    expect(within(timeline).getByText('Conception')).toBeInTheDocument();
    expect(within(timeline).getByText('Attente et finalisation')).toBeInTheDocument();
    expect(within(timeline).getByText('Durée totale : 3m 20s')).toBeInTheDocument();
    expect(within(card).getByRole('table', { name: 'Performance par agent de cette exécution' })).toBeInTheDocument();
    await user.click(within(card).getByRole('button', { name: /Masquer le détail/ }));
    expect(within(card).queryByRole('group', { name: "Chronologie de l'exécution" })).not.toBeInTheDocument();
  });
});
