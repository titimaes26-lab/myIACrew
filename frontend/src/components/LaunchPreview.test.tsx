import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { LaunchControls, LaunchPreview as LaunchPreviewData } from '../types';
import LaunchPreview from './LaunchPreview';

const DEFAULT_PREVIEW: LaunchPreviewData = { workflow: 'FEATURE', scope: 'PETIT', repoLabel: 'o/r · main' };

function setup(preview: LaunchPreviewData = DEFAULT_PREVIEW, overrides: Partial<LaunchControls> = {}) {
  const controls: LaunchControls = {
    onLaunch: vi.fn(), onCancel: vi.fn(), alwaysConfirm: true, onAlwaysConfirmChange: vi.fn(), disabled: false, ...overrides,
  };
  render(<LaunchPreview preview={preview} controls={controls} />);
  return controls;
}

describe('LaunchPreview', () => {
  it('montre type, taille, étapes qui vont tourner et repository', () => {
    setup();
    expect(screen.getByLabelText('Type de demande')).toHaveValue('FEATURE');
    expect(screen.getByLabelText('Taille')).toHaveValue('PETIT');
    expect(screen.getByText(/Diagnostic & rédaction du code → Écriture du code → Revue QA/)).toBeInTheDocument();
    expect(screen.queryByText(/Architecture technique/)).toBeNull();
    expect(screen.getByText(/o\/r · main/)).toBeInTheDocument();
  });

  it('ajoute l’architecture aux étapes quand la taille passe à « complète »', async () => {
    setup();
    await userEvent.selectOptions(screen.getByLabelText('Taille'), 'GRAND');
    expect(screen.getByText(/Architecture technique → Diagnostic/)).toBeInTheDocument();
  });

  it('masque la taille hors FEATURE et ne l’envoie pas', async () => {
    const controls = setup();
    await userEvent.selectOptions(screen.getByLabelText('Type de demande'), 'BUGFIX');
    expect(screen.queryByLabelText('Taille')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: /Lancer/ }));
    expect(controls.onLaunch).toHaveBeenCalledWith({ workflow: 'BUGFIX', scope: undefined });
  });

  it('lance avec le choix courant, annule, et mémorise « toujours confirmer »', async () => {
    const controls = setup({ workflow: 'FEATURE', scope: 'PETIT', repoLabel: null });
    expect(screen.getByText(/aucun \(espace de travail local\)/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /Lancer/ }));
    expect(controls.onLaunch).toHaveBeenCalledWith({ workflow: 'FEATURE', scope: 'PETIT' });
    await userEvent.click(screen.getByRole('button', { name: 'Annuler' }));
    expect(controls.onCancel).toHaveBeenCalled();
    await userEvent.click(screen.getByRole('checkbox', { name: /Toujours demander confirmation/ }));
    expect(controls.onAlwaysConfirmChange).toHaveBeenCalledWith(false);
  });

  it('met le focus sur « Lancer » à l’apparition, sauf si l’utilisateur écrit déjà ailleurs', () => {
    setup();
    expect(screen.getByRole('button', { name: /Lancer/ })).toHaveFocus();
  });

  it('ne vole pas le focus d’une zone de saisie en cours de frappe', () => {
    const area = document.createElement('textarea');
    document.body.appendChild(area);
    area.value = 'prochaine demande';
    area.focus();
    setup();
    expect(area).toHaveFocus();
    area.remove();
  });

  it('désactive les actions pendant un lancement', () => {
    setup(undefined, { disabled: true });
    expect(screen.getByRole('button', { name: /Lancer/ })).toBeDisabled();
    expect(screen.getByLabelText('Type de demande')).toBeDisabled();
  });
});
