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

  describe('échec de livraison GitHub', () => {
    const report = 'RAPPORT-DE-L-AGENT ' + 'détail '.repeat(150) + 'FIN-DU-RAPPORT';
    const delivery = (extra: string) => (
      "Un repository GitHub cible était configuré mais la vérification après coup a échoué : la branche 'crewai/x' existe mais ne contient aucun nouveau commit. "
      + "Cela peut venir d'un manque de permissions d'écriture.\n\n--- Rapport de l'agent (non vérifié sur GitHub) ---\n" + report + extra
    );
    const zeroAhead = "\n\n--- Travail déjà présent sur GitHub ---\n- Branche `crewai/x` : 0 commit(s) d'avance sur `test`, aucun nouveau commit de cette tentative — https://github.com/o/r/tree/crewai/x\n- Pull Request : aucune ouverte pour cette branche";

    it('explique en une phrase ce qui s’est passé, au lieu de renvoyer à « Erreur interne »', () => {
      renderFailure(delivery(zeroAhead), 'DELIVERY_FAILED', false);
      expect(screen.getByText('Livraison GitHub non confirmée')).toBeInTheDocument();
      expect(screen.getByText(/n'a aucun commit d'avance sur la branche de base/)).toBeInTheDocument();
      expect(screen.getByText(/le Développeur n'a rien écrit pendant cette exécution/)).toBeInTheDocument();
      expect(screen.queryByText('Erreur interne')).toBeNull();
    });

    it('replie le rapport de l’agent (copiable), affiche le guide et le lien de la branche', () => {
      renderFailure(delivery(zeroAhead), 'DELIVERY_FAILED', false);
      const summary = screen.getByText(/Rapport de l'agent \(non vérifié sur GitHub\)/);
      const details = summary.closest('details') as HTMLDetailsElement;
      expect(details.open).toBe(false);
      expect(details).toHaveTextContent('FIN-DU-RAPPORT');   // le rapport complet est là, seulement replié
      expect(screen.getByRole('button', { name: /Copier : Rapport de l'agent/ })).toBeInTheDocument();
      expect(screen.getByText('Que faire ?')).toBeInTheDocument();
      const open = screen.getByRole('link', { name: 'Ouvrir la branche crewai/x sur GitHub' });
      expect(open).toHaveAttribute('href', 'https://github.com/o/r/tree/crewai/x');
      expect(screen.queryByText(/Panne temporaire/)).toBeNull();
    });

    it('dit qu’aucune branche n’existe quand rien n’a été poussé', () => {
      const none = "\n\n--- Travail déjà présent sur GitHub ---\n- Rien n'a été poussé : la branche `crewai/x` n'existe pas sur o/r.";
      renderFailure(delivery(none).replace("existe mais ne contient aucun nouveau commit", "aucune branche 'crewai/x' n'existe"), 'DELIVERY_FAILED', false);
      expect(screen.getByText(/Aucune branche n'a été créée sur GitHub/)).toBeInTheDocument();
    });
  });

  it('replie un détail technique long et laisse un court visible', () => {
    const long = "Échec à l'étape 1/3 (X) : " + 'z'.repeat(900);
    const { unmount } = renderFailure(long, 'INTERNAL_ERROR', false);
    expect(screen.getByText(/Détail technique \(900 caractères\)/).closest('details')).not.toBeNull();
    unmount();
    renderFailure("Échec à l'étape 1/3 (X) : court", 'INTERNAL_ERROR', false);
    expect(document.querySelector('details')).toBeNull();
  });

  it('affiche la carte « Déjà réalisé avant l’échec » sans la mêler au détail technique', () => {
    const partial = "\n\n--- Déjà réalisé avant l'échec ---\n2 étapes terminées avant l'échec\n- Architecte : Plan\n- Analyste : Cause";
    renderFailure(`Échec à l'étape 3/4 (Développeur) : boum${partial}`, 'LLM_UNAVAILABLE', true);
    expect(screen.getByText("✅ Déjà réalisé avant l'échec")).toBeInTheDocument();
    expect(screen.getByText('2 étapes terminées avant l\'échec')).toBeInTheDocument();
    expect(screen.getByText(/Architecte : Plan/)).toBeInTheDocument();
    expect(screen.queryByText(/--- Déjà réalisé/)).not.toBeInTheDocument();
  });
});
