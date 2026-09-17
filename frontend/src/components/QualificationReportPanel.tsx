import type { QualificationReport } from '../types';
import RepoTargetFields from './RepoTargetFields';
import AgentStepIndicator from './AgentStepIndicator';

interface QualificationReportPanelProps {
  report: QualificationReport;
  clarifications: string;
  onClarificationsChange: (value: string) => void;
  repoOwner: string;
  repoName: string;
  baseBranch: string;
  onRepoOwnerChange: (value: string) => void;
  onRepoNameChange: (value: string) => void;
  onBaseBranchChange: (value: string) => void;
  selectedWorkflow: string;
  onWorkflowChange: (value: string) => void;
  onExecute: () => void;
  loadingExec: boolean;
  loadingQualif: boolean;
  executionSteps: string[];
  currentStep: number;
}

export default function QualificationReportPanel({
  report,
  clarifications,
  onClarificationsChange,
  repoOwner,
  repoName,
  baseBranch,
  onRepoOwnerChange,
  onRepoNameChange,
  onBaseBranchChange,
  selectedWorkflow,
  onWorkflowChange,
  onExecute,
  loadingExec,
  loadingQualif,
  executionSteps,
  currentStep,
}: QualificationReportPanelProps) {
  return (
    <div style={{ marginTop: '30px', padding: '20px', backgroundColor: '#f8f9fa', border: '1px solid #e1e4e8', borderRadius: '8px' }}>
      <h3>📋 Rapport de Qualification</h3>
      <p><strong>Synthèse :</strong> {report.summary}</p>
      <p><strong>Clarté de la demande :</strong> {report.is_clear ? '✅ Claire' : '⚠️ Ambiguïtés détectées'}</p>

      {report.questions && report.questions.length > 0 && (
        <div style={{ marginTop: '15px', backgroundColor: '#fff', padding: '15px', borderRadius: '6px', border: '1px solid #e1e4e8' }}>
          <p style={{ margin: '0 0 10px 0', fontWeight: 'bold' }}>❓ Précisions recommandées par l'agent :</p>
          <ul style={{ paddingLeft: '20px' }}>
            {report.questions.map((q, i) => <li key={i} style={{ marginBottom: '4px' }}>{q}</li>)}
          </ul>
          <textarea
            rows={3}
            disabled={loadingExec}
            style={{ width: '100%', padding: '10px', borderRadius: '4px', border: '1px solid #ccc', boxSizing: 'border-box' }}
            placeholder="Saisissez vos réponses ici pour affiner le travail des agents..."
            value={clarifications}
            onChange={(e) => onClarificationsChange(e.target.value)}
          />
        </div>
      )}

      <RepoTargetFields
        repoOwner={repoOwner}
        repoName={repoName}
        baseBranch={baseBranch}
        onRepoOwnerChange={onRepoOwnerChange}
        onRepoNameChange={onRepoNameChange}
        onBaseBranchChange={onBaseBranchChange}
        disabled={loadingExec}
      />

      <div style={{ marginTop: '20px', display: 'flex', alignItems: 'center', gap: '10px' }}>
        <label htmlFor="workflow"><b>Workflow sélectionné :</b></label>
        <select
          id="workflow"
          value={selectedWorkflow}
          disabled={loadingExec}
          onChange={(e) => onWorkflowChange(e.target.value)}
          style={{ padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
        >
          <option value="ANALYSE_ONLY">ANALYSE_ONLY (Design & Cadrage)</option>
          <option value="BUGFIX">BUGFIX (Correction de bug)</option>
          <option value="FEATURE">FEATURE (Nouvelle fonctionnalité)</option>
          <option value="DESIGN_AND_DEV">DESIGN_AND_DEV (Pipeline complet)</option>
        </select>
      </div>

      <button
        type="button"
        onClick={onExecute}
        disabled={loadingExec || loadingQualif}
        style={{
          marginTop: '20px',
          width: '100%',
          padding: '14px',
          backgroundColor: loadingExec ? '#6ee7b7' : '#10b981',
          color: '#fff',
          border: 'none',
          borderRadius: '6px',
          cursor: loadingExec ? 'not-allowed' : 'pointer',
          fontWeight: 'bold',
          fontSize: '16px',
        }}
      >
        {loadingExec ? '⚙️ Les agents travaillent sur votre projet (veuillez patienter)...' : `2. Lancer le Workflow ${selectedWorkflow}`}
      </button>

      {loadingExec && <AgentStepIndicator steps={executionSteps} currentIndex={currentStep} />}
    </div>
  );
}
