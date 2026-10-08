import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import StudioHeader from './StudioHeader';

vi.mock('../supabaseClient', () => ({ supabase: { auth: { signOut: vi.fn() } } }));

function setup(overrides: Partial<Parameters<typeof StudioHeader>[0]> = {}) {
  const props = {
    userEmail: 'a@b.c', historyOpen: false, onToggleHistory: vi.fn(), metricsOpen: false, onToggleMetrics: vi.fn(),
    notifyEnabled: false, notifyPermission: 'default' as const, onToggleNotify: vi.fn(), ...overrides,
  };
  render(<StudioHeader {...props} />);
  return props;
}

describe('StudioHeader — notification de fin', () => {
  it('propose le bouton et reflète l’état activé', async () => {
    const props = setup({ notifyEnabled: true, notifyPermission: 'granted' });
    const button = screen.getByRole('button', { name: /Me prévenir/ });
    expect(button).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(button);
    expect(props.onToggleNotify).toHaveBeenCalled();
  });

  it('explique un blocage du navigateur', () => {
    setup({ notifyPermission: 'denied' });
    const button = screen.getByRole('button', { name: /Me prévenir \(bloqué\)/ });
    expect(button).toHaveAttribute('title', expect.stringContaining('réglages du site'));
  });

  it('n’affiche aucun bouton si le navigateur ne gère pas les notifications', () => {
    setup({ notifyPermission: 'unsupported' });
    expect(screen.queryByRole('button', { name: /Me prévenir/ })).toBeNull();
  });
});
