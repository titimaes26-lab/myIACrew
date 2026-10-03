import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import StepIndicator from './StepIndicator';

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

  it('en file d’attente, aucune étape n’est courante', () => {
    render(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="queued" />);
    for (const item of items()) expect(item).toHaveClass('stepper__item--todo');
    expect(screen.getByText(/En file d'attente/)).toBeInTheDocument();
  });

  it('annonce l’état de chaque étape aux lecteurs d’écran', () => {
    render(<StepIndicator workflow="BUGFIX" since={since} currentStepKey="development" />);
    expect(screen.getByText('(terminée)')).toBeInTheDocument();
    expect(screen.getByText('(en cours)')).toBeInTheDocument();
    expect(screen.getByText('(à venir)')).toBeInTheDocument();
  });
});
