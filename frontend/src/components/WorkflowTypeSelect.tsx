import { WORKFLOW_TYPE_OPTIONS, type WorkflowType } from '../constants/workflowTypes';

interface WorkflowTypeSelectProps {
  workflowType: WorkflowType;
  onWorkflowTypeChange: (workflowType: WorkflowType) => void;
  disabled: boolean;
}

export default function WorkflowTypeSelect({ workflowType, onWorkflowTypeChange, disabled }: WorkflowTypeSelectProps) {
  return (
    <label className="field-label">
      Type de demande :
      <select
        value={workflowType}
        onChange={(e) => onWorkflowTypeChange(e.target.value as WorkflowType)}
        disabled={disabled}
        // Ce choix persiste d'un message à l'autre (jamais réinitialisé à 'AUTO' après
        // l'envoi) pour permettre plusieurs messages de suite dans un même mode manuel
        // sans avoir à le re-sélectionner à chaque fois. Un style distinct quand il
        // n'est pas 'AUTO' est donc nécessaire : sans lui, un choix manuel oublié depuis
        // un message précédent resterait actif en silence pour une demande sans rapport,
        // qui contournerait alors /api/qualify sans que rien ne le signale à l'écran.
        className={`field field--sm${workflowType !== 'AUTO' ? ' field--accent' : ''}`}
      >
        {WORKFLOW_TYPE_OPTIONS.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.icon} {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}
