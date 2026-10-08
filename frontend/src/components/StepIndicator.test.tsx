import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { MetricsApiContext } from '../hooks/metricsApi';
import { clearStepStatsCache } from '../hooks/useStepStats';
import StepIndicator from './StepIndicator';
import { queueAheadText } from '../utils/queueText';

const since = new Date().toISOString();

function items() {
  return screen.getAllByRole('listitem');
}

describe('StepIndicator', () => {
  it('marque terminées les étapes avant l’étape réelle, courante celle du serveur, à venir les suivantes', () => {
    render(<StepIndicator workflow="DESIGN_AND_DEV" since={since} currentStepKey="diagnostic" />);
    const list = items();
    expect(list).toHaveLength(5);
    expect(list[0]).toHaveClass('stepper__item--done');
    expect(list[1]).toHaveClass('stepper__item--done');
    expect(list[2]).toHaveClass('stepper__item--current');
    expect(list[2]).toHaveAttribute('aria-current', 'step');
    expect(list[3]).toHaveClass('stepper__item--todo');
  });

  it('montre les étapes reprises à part, et l’étape courante saute les étapes reprises', () => {
    render(<StepIndicator workflow="DESIGN_AND_DEV" since={since} reusedSteps={['design', 'architecture']} />);
    const list = items();
    expect(list[0]).toHaveClass('stepper__item--reused');
    expect(list[1]).toHaveClass('stepper__item--reused');
    // estimation par temps : démarre à 0, mais la première étape NON reprise est la courante
    expect(list[2]).toHaveClass('stepper__item--current');
  });

  it('saute l’architecture pour une petite FEATURE et le dit', () => {
    render(<StepIndicator workflow="FEATURE" scope="PETIT" since={since} currentStepKey="diagnostic" />);
    const list = items();
    expect(list).toHaveLength(3);
    expect(list[0]).toHaveClass('stepper__item--current');   // le Diagnostic est la première étape
    expect(screen.queryByText('Architecture')).toBeNull();
    expect(screen.getByText(/étape d'architecture est sautée/)).toBeInTheDocument();
  });

  it('garde l’architecture pour une FEATURE sans scope, GRAND, ou pour un autre workflow', () => {
    const { rerender } = render(<StepIndicator workflow="FEATURE" since={since} />);
    expect(items()).toHaveLength(4);
    rerender(<StepIndicator workflow="FEATURE" scope="GRAND" since={since} />);
    expect(items()).toHaveLength(4);
    rerender(<StepIndicator workflow="BUGFIX" scope="PETIT" since={since} />);
    expect(items()).toHaveLength(3);
    expect(screen.queryByText(/sautée/)).toBeNull();
  });

  it('en file d’attente, aucune étape n’est courante', () => {
    render(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="queued" />);
    for (const item of items()) expect(item).toHaveClass('stepper__item--todo');
    expect(screen.getByText(/En file d'attente/)).toBeInTheDocument();
  });

  it('en file d’attente, dit combien d’exécutions passent avant', () => {
    const { rerender } = render(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="queued" queueAhead={2} />);
    expect(screen.getByText(/2 exécutions passent avant elle/)).toBeInTheDocument();
    rerender(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="queued" queueAhead={1} />);
    expect(screen.getByText(/1 exécution passe avant elle/)).toBeInTheDocument();
    rerender(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="queued" queueAhead={0} />);
    expect(screen.getByText(/elle est la prochaine/)).toBeInTheDocument();
    rerender(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="queued" />);
    expect(screen.getByText(/d'autres exécutions occupent déjà ce service/)).toBeInTheDocument();
    expect(queueAheadText(null)).toContain('occupent');
  });

  it('le « en cours depuis » ne compte pas le temps passé dans la file', () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date('2026-10-04T12:00:00Z'));
      const start = new Date().toISOString();
      const { rerender } = render(<StepIndicator workflow="BUGFIX" since={start} currentStepKey="queued" queueAhead={1} />);
      act(() => { vi.advanceTimersByTime(125_000); });                   // 2 min 5 s dans la file
      expect(screen.getByText(/En file d'attente depuis 2m 5s/)).toBeInTheDocument();
      rerender(<StepIndicator workflow="BUGFIX" since={start} currentStepKey="diagnostic" />);
      expect(screen.getByText(/En cours depuis 0s/)).toBeInTheDocument();   // l'exécution vient de commencer
      act(() => { vi.advanceTimersByTime(30_000); });
      expect(screen.getByText(/En cours depuis 30s/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('annonce l’état de chaque étape aux lecteurs d’écran', () => {
    render(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="development" />);
    expect(screen.getByText('(terminée)')).toBeInTheDocument();
    expect(screen.getByText('(en cours)')).toBeInTheDocument();
    expect(screen.getByText('(à venir)')).toBeInTheDocument();
  });
});

describe('StepIndicator — temps restant estimé', () => {
  const agents = [
    ['architecture', 60], ['diagnostic', 100], ['development', 40], ['qa', 20],
  ].map(([agent, p50]) => ({ agent, runs: 10, duration_p50: p50 }));

  function renderWithStats(since: string, currentStepKey: string) {
    clearStepStatsCache();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ agents }), { status: 200 }))));
    return render(
      <MetricsApiContext.Provider value={{ apiUrl: '', accessToken: 't' }}>
        <StepIndicator workflow="FEATURE" since={since} currentStepKey={currentStepKey} />
      </MetricsApiContext.Provider>,
    );
  }

  afterEach(() => { vi.unstubAllGlobals(); });

  it('affiche la durée de l’étape en cours et le temps restant estimé d’après les médianes', async () => {
    renderWithStats(new Date(Date.now() - 20_000).toISOString(), 'architecture');
    // 20 s écoulées sur 60 s de médiane : 40 + 100 + 40 + 20 s restantes, arrondies à la dizaine.
    expect(await screen.findByText(/temps restant estimé : environ 3m [12]0s/)).toBeInTheDocument();
    expect(screen.getByText(/Étape « Architecture technique » depuis 2\ds/)).toBeInTheDocument();
  });

  it('dit qu’une étape est plus longue que d’habitude et n’estime que la suite', async () => {
    renderWithStats(new Date(Date.now() - 90_000).toISOString(), 'architecture');
    expect(await screen.findByText(/plus long que d'habitude \(médiane 1m 0s\)/)).toBeInTheDocument();
  });

  it('n’estime rien quand la frise est montée en cours d’étape suivante (début inconnu)', async () => {
    renderWithStats(new Date(Date.now() - 20_000).toISOString(), 'diagnostic');
    await new Promise((resolve) => setTimeout(resolve, 30));
    expect(screen.queryByText(/temps restant estimé/)).toBeNull();
    expect(screen.queryByText(/Étape « /)).toBeNull();
  });

  it('montre seulement la durée de l’étape quand les médianes sont indisponibles', async () => {
    clearStepStatsCache();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response('{}', { status: 500 }))));
    render(
      <MetricsApiContext.Provider value={{ apiUrl: '', accessToken: 't' }}>
        <StepIndicator workflow="FEATURE" since={new Date(Date.now() - 5_000).toISOString()} currentStepKey="architecture" />
      </MetricsApiContext.Provider>,
    );
    expect(await screen.findByText(/Étape « Architecture technique » depuis/)).toBeInTheDocument();
    expect(screen.queryByText(/temps restant estimé/)).toBeNull();
  });

  it('remesure l’étape quand le suivi réel disparaît (pause, relance) puis revient sur la même étape', async () => {
    clearStepStatsCache();
    vi.stubGlobal('fetch', vi.fn(() => Promise.resolve(new Response(JSON.stringify({ agents }), { status: 200 }))));
    const since = new Date(Date.now() - 240_000).toISOString();   // 4 min depuis le début du tour
    const ui = (key: string | null) => (
      <MetricsApiContext.Provider value={{ apiUrl: '', accessToken: 't' }}>
        <StepIndicator workflow="FEATURE" since={since} currentStepKey={key} />
      </MetricsApiContext.Provider>
    );
    const { rerender } = render(ui('architecture'));
    expect(await screen.findByText(/Étape « Architecture technique » depuis 4m/)).toBeInTheDocument();
    // Pause avant une nouvelle tentative : plus d'étape réelle.
    rerender(ui(null));
    expect(screen.queryByText(/Étape « /)).toBeNull();
    // La même étape repart : son chrono repart de zéro au lieu de continuer à 4 min.
    rerender(ui('architecture'));
    expect(await screen.findByText(/Étape « Architecture technique » depuis 0s/)).toBeInTheDocument();
    expect(screen.queryByText(/plus long que d'habitude/)).toBeNull();
  });
});
