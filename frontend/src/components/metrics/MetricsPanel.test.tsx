import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MetricsSummary } from '../../types';
import MetricsPanel from './MetricsPanel';

const executions = {
  total: 20, success: 18, failed: 2, median_duration_seconds: 82, avg_llm_calls: 9, avg_tokens: 12000,
  token_executions: 18, rate_limit_hits: 3, wait_seconds: 40,
};

function summary(overrides: Partial<MetricsSummary> = {}): MetricsSummary {
  return {
    period_days: 30, workflow: null, executions, agents: [], failures: [],
    daily: [
      { date: '2026-10-01', executions: 4, failed: 1, llm_calls: 12, tokens: 3400, median_duration_seconds: 95 },
      { date: '2026-10-02', executions: 2, failed: 0, llm_calls: 6, tokens: 1500, median_duration_seconds: 60 },
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

function respondWith(data: MetricsSummary) {
  (fetch as unknown as ReturnType<typeof vi.fn>).mockResolvedValue(new Response(JSON.stringify(data), { status: 200 }));
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
        { date: '2026-10-01', executions: 2, failed: 2, llm_calls: 4, tokens: 0, median_duration_seconds: 50 },
        { date: '2026-10-02', executions: 0, failed: 0, llm_calls: 0, tokens: 0, median_duration_seconds: null },
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
        { date: '2026-10-01', executions: 4, failed: 0, llm_calls: 12, tokens: 0, median_duration_seconds: 95 },
        { date: '2026-10-02', executions: 1, failed: 0, llm_calls: 0, tokens: 0, median_duration_seconds: null },
      ],
    }));
    render(<MetricsPanel apiUrl="" accessToken="t" />);
    await screen.findByText('Appels LLM par jour');
    await user.click(screen.getByRole('button', { name: 'Durée médiane' }));
    expect(screen.getByRole('group', { name: /2 oct\. : Durée médiane aucune donnée/ })).toBeInTheDocument();
    expect(document.querySelectorAll('.viz-colbar')).toHaveLength(1);
  });
});
