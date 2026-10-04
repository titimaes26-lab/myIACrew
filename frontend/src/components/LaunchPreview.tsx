import { useEffect, useRef, useState } from 'react';
import type { LaunchChoice, LaunchControls, LaunchPreview as LaunchPreviewData } from '../types';
import { WORKFLOW_TYPE_OPTIONS } from '../constants/workflowTypes';
import { stepShortLabel, workflowSteps } from '../constants/workflowSteps';
import Button from './ui/Button';

const TYPE_OPTIONS = WORKFLOW_TYPE_OPTIONS.filter((option) => option.value !== 'AUTO');

type Scope = 'PETIT' | 'GRAND';

// Aperçu de ce que la qualification propose de lancer : type, taille (FEATURE), étapes qui vont tourner et
// repository cible. L'utilisateur corrige ou confirme avant l'exécution (plusieurs minutes de quota).
export default function LaunchPreview({ preview, controls }: { preview: LaunchPreviewData; controls: LaunchControls }) {
  const [workflow, setWorkflow] = useState<LaunchChoice['workflow']>(preview.workflow);
  const [scope, setScope] = useState<Scope>(preview.scope ?? 'GRAND');
  const effectiveScope = workflow === 'FEATURE' ? scope : undefined;
  const steps = workflowSteps(workflow, effectiveScope);
  const root = useRef<HTMLElement>(null);

  // L'aperçu apparaît sans action de l'utilisateur : le focus passe sur « Lancer » (Entrée confirme), sauf si l'utilisateur
  // est déjà en train d'écrire autre chose.
  useEffect(() => {
    const active = document.activeElement;
    const idle = !active || active === document.body || (active instanceof HTMLTextAreaElement && active.value === '');
    if (idle) root.current?.querySelector<HTMLButtonElement>('.launch-preview__actions button')?.focus();
  }, []);

  return (
    <section ref={root} className="launch-preview" aria-label="Aperçu avant lancement">
      <p className="launch-preview__title">🚀 Prêt à lancer ?</p>
      <div className="launch-preview__grid">
        <label className="launch-preview__field">
          <span>Type de demande</span>
          <select value={workflow} onChange={(event) => setWorkflow(event.target.value as LaunchChoice['workflow'])} disabled={controls.disabled}>
            {TYPE_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>{option.icon} {option.label}</option>
            ))}
          </select>
        </label>
        {workflow === 'FEATURE' && (
          <label className="launch-preview__field">
            <span>Taille</span>
            <select value={scope} onChange={(event) => setScope(event.target.value as Scope)} disabled={controls.disabled}>
              <option value="PETIT">Petite modification (sans architecture)</option>
              <option value="GRAND">Fonctionnalité complète (avec architecture)</option>
            </select>
          </label>
        )}
      </div>
      <p className="launch-preview__line">
        <strong>Étapes :</strong> {steps.map((step) => stepShortLabel(step.label)).join(' → ')}
      </p>
      <p className="launch-preview__line">
        <strong>Repository :</strong> {preview.repoLabel ?? 'aucun (espace de travail local)'}
      </p>
      <label className="launch-preview__check">
        <input type="checkbox" checked={controls.alwaysConfirm} onChange={(event) => controls.onAlwaysConfirmChange(event.target.checked)} />
        Toujours demander confirmation avant de lancer
      </label>
      <div className="launch-preview__actions">
        <Button variant="primary" size="sm" disabled={controls.disabled} onClick={() => controls.onLaunch({ workflow, scope: effectiveScope })}>
          🚀 Lancer
        </Button>
        <Button size="sm" disabled={controls.disabled} onClick={controls.onCancel}>Annuler</Button>
      </div>
    </section>
  );
}
