import { PERIOD_PRESETS } from '../../constants/metricsPeriods';
import { WORKFLOW_TYPE_OPTIONS } from '../../constants/workflowTypes';
import type { MetricsWorkflowFilter } from '../../types';

interface MetricsFiltersProps {
  days: number;
  onDaysChange: (days: number) => void;
  workflow: MetricsWorkflowFilter;
  onWorkflowChange: (workflow: MetricsWorkflowFilter) => void;
}

// Une seule rangée, au-dessus de tout ce qu'elle filtre : période (préréglages) puis workflow.
export default function MetricsFilters({ days, onDaysChange, workflow, onWorkflowChange }: MetricsFiltersProps) {
  const workflows = WORKFLOW_TYPE_OPTIONS.filter((option) => option.value !== 'AUTO');
  return (
    <div className="viz-filters">
      <div className="viz-seg" role="group" aria-label="Période">
        {PERIOD_PRESETS.map((preset) => (
          <button key={preset} type="button" aria-pressed={days === preset} onClick={() => onDaysChange(preset)}>
            {preset} jours
          </button>
        ))}
      </div>
      <select
        className="viz-select"
        aria-label="Workflow"
        value={workflow}
        onChange={(event) => onWorkflowChange(event.target.value as MetricsWorkflowFilter)}
      >
        <option value="ALL">Tous les workflows</option>
        {workflows.map((option) => (
          <option key={option.value} value={option.value}>{option.icon} {option.label}</option>
        ))}
      </select>
    </div>
  );
}
