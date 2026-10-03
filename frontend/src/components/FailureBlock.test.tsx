import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import FailureBlock from './FailureBlock';
import { parseFailureDetail } from '../utils/parseFailureDetail';

const github = "\n\n--- Travail déjà présent sur GitHub ---\n- Branche `crewai/x` : 2 commit(s) — https://github.com/o/r/tree/crewai/x\n- Pull Request ouverte : https://github.com/o/r/pull/7.";

function renderFailure(result: string | null, errorCode: string | null, errorRetryable: boolean | null) {
  const detail = result ? parseFailureDetail(result) : null;
  return render(<FailureBlock turn={{ result, errorCode, errorRetryable }} detail={detail} />);
}

describe('FailureBlock', () => {
  it('présente une panne temporaire en ambre, avec la cause, l’étape et le conseil', () => {
    renderFailure("Échec à l'étape 3/4 (Analyste Diagnostic Technique) : Le modèle IA est indisponible.", 'LLM_UNAVAILABLE', true);
    const alert = screen.getByRole('alert');
    expect(alert).toHaveClass('failure--transient');
    expect(screen.getByText('Modèle indisponible ou surchargé')).toBeInTheDocument();
    expect(screen.getByText(/Étape 3\/4/)).toBeInTheDocument();
    expect(screen.getByText(/Panne temporaire/)).toBeInTheDocument();
  });

  it('présente un vrai échec en rouge', () => {
    renderFailure("Échec à l'étape 4/4 (QA) : contrôle non respecté.", 'GUARDRAIL_FAILED', false);
    expect(screen.getByRole('alert')).not.toHaveClass('failure--transient');
  });

  it('retombe sur le code quand retryable est absent : couleur et conseil restent cohérents', () => {
    renderFailure("Échec à l'étape 1/3 (X) : quota.", 'QUOTA_EXHAUSTED', null);
    expect(screen.getByRole('alert')).toHaveClass('failure--transient');
    expect(screen.getByText(/Panne temporaire/)).toBeInTheDocument();
  });

  it('affiche la carte GitHub avec des liens sûrs, la ponctuation hors du lien', () => {
    renderFailure(`Échec à l'étape 2/5 (Architecte) : boum${github}`, 'INTERNAL_ERROR', false);
    expect(screen.getByText(/Travail déjà présent sur GitHub/)).toBeInTheDocument();
    const pull = screen.getByRole('link', { name: 'o/r/pull/7' });
    expect(pull).toHaveAttribute('href', 'https://github.com/o/r/pull/7');
    expect(pull).toHaveAttribute('rel', 'noopener noreferrer');
    expect(pull).toHaveAttribute('target', '_blank');
    expect(screen.getByText('crewai/x', { selector: 'code' })).toBeInTheDocument();
  });

  it('s’affiche même sans texte de résultat', () => {
    renderFailure(null, 'QUOTA_EXHAUSTED', true);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Quota du modèle épuisé')).toBeInTheDocument();
  });

  it('ne rend jamais de HTML contenu dans le message', () => {
    renderFailure("Échec à l'étape 1/3 (X) : <img src=x onerror=alert(1)>", null, false);
    expect(document.querySelector('img')).toBeNull();
  });
});
