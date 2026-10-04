import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import InfoTip from './InfoTip';

describe('InfoTip', () => {
  it('porte un nom accessible et ouvre / ferme la bulle au clic', () => {
    render(<InfoTip term="p95" text="95 % des exécutions sont plus rapides." />);
    const button = screen.getByRole('button', { name: 'Explication : p95' });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.click(button);
    const tip = screen.getByRole('tooltip');
    expect(tip).toHaveTextContent('95 % des exécutions');
    expect(button).toHaveAttribute('aria-describedby', tip.id);
    expect(button).toHaveAttribute('aria-expanded', 'true');

    fireEvent.click(button);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('s’ouvre au survol et se ferme en le quittant', () => {
    render(<InfoTip term="p50" text="Médiane." />);
    const button = screen.getByRole('button', { name: 'Explication : p50' });
    fireEvent.mouseEnter(button);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Médiane.');
    fireEvent.mouseLeave(button);
    expect(screen.queryByRole('tooltip')).toBeNull();
  });

  it('se ferme avec Échap et au clic à l’extérieur', () => {
    render(<div><InfoTip term="p50" text="Médiane." /><p>ailleurs</p></div>);
    const button = screen.getByRole('button', { name: 'Explication : p50' });
    fireEvent.click(button);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('tooltip')).toBeNull();

    fireEvent.click(button);
    fireEvent.pointerDown(screen.getByText('ailleurs'));
    expect(screen.queryByRole('tooltip')).toBeNull();
  });
});
