import { useState, type FormEvent } from 'react';
import type { QualificationReport } from './types';
import { useExecutionSteps } from './hooks/useExecutionSteps';
import StudioHeader from './components/StudioHeader';
import QualificationForm from './components/QualificationForm';
import QualificationReportPanel from './components/QualificationReportPanel';
import ExecutionResultPanel from './components/ExecutionResultPanel';
import HistoryPanel from './components/HistoryPanel';

export default function Studio({ accessToken, userEmail }: { accessToken: string; userEmail: string }) {
  const [prompt, setPrompt] = useState('');
  const [loadingQualif, setLoadingQualif] = useState(false);
  const [loadingExec, setLoadingExec] = useState(false);
  const [showHistory, setShowHistory] = useState(false);

  const [report, setReport] = useState<QualificationReport | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>('');
  const [clarifications, setClarifications] = useState('');
  const [executionResult, setExecutionResult] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [repoOwner, setRepoOwner] = useState('');
  const [repoName, setRepoName] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');

  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
  const { steps: executionSteps, currentStep, setCurrentStep } = useExecutionSteps(loadingExec, selectedWorkflow);

  // Étape 1 : Appel API Qualification
  const handleQualify = async (e: FormEvent) => {
    e.preventDefault();
    if (!prompt.trim()) return;

    setLoadingQualif(true);
    setErrorMessage(null);
    setExecutionResult(null);

    try {
      const res = await fetch(`${API_URL}/api/qualify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${accessToken}` },
        body: JSON.stringify({ user_request: prompt }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Erreur serveur (${res.status})`);
      }

      const data: QualificationReport = await res.json();
      setReport(data);
      setSelectedWorkflow(data.request_type);
    } catch (err: any) {
      console.error('Erreur qualification:', err);
      setErrorMessage(err.message || "Impossible de contacter le serveur d'analyse.");
    } finally {
      setLoadingQualif(false);
    }
  };

  // Étape 2 : Appel API Exécution
  const handleExecute = async () => {
    setLoadingExec(true);
    setCurrentStep(0);
    setErrorMessage(null);
    setExecutionResult(null);

    try {
      const res = await fetch(`${API_URL}/api/execute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${accessToken}` },
        body: JSON.stringify({
          user_request: prompt,
          target_workflow: selectedWorkflow,
          clarifications: clarifications,
          repo_owner: repoOwner.trim() || undefined,
          repo_name: repoName.trim() || undefined,
          base_branch: baseBranch.trim() || 'main',
        }),
      });

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Erreur durant l'exécution (${res.status})`);
      }

      const data = await res.json();
      setExecutionResult(data.result);
      setCurrentStep(executionSteps.length);
    } catch (err: any) {
      console.error('Erreur execution:', err);
      setErrorMessage(err.message || "Erreur lors de l'exécution du workflow.");
    } finally {
      setLoadingExec(false);
    }
  };

  return (
    <div style={{ maxWidth: '850px', margin: '40px auto', fontFamily: 'system-ui, sans-serif', padding: '20px', color: '#333' }}>
      <StudioHeader
        userEmail={userEmail}
        historyOpen={showHistory}
        onToggleHistory={() => setShowHistory((open) => !open)}
      />

      {showHistory && <HistoryPanel apiUrl={API_URL} accessToken={accessToken} />}

      {errorMessage && (
        <div style={{ padding: '12px 16px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#991b1b', marginBottom: '20px', fontWeight: '500' }}>
          ❌ {errorMessage}
        </div>
      )}

      <QualificationForm
        prompt={prompt}
        onPromptChange={setPrompt}
        onSubmit={handleQualify}
        disabled={loadingQualif || loadingExec}
        loading={loadingQualif}
      />

      {report && (
        <QualificationReportPanel
          report={report}
          clarifications={clarifications}
          onClarificationsChange={setClarifications}
          repoOwner={repoOwner}
          repoName={repoName}
          baseBranch={baseBranch}
          onRepoOwnerChange={setRepoOwner}
          onRepoNameChange={setRepoName}
          onBaseBranchChange={setBaseBranch}
          selectedWorkflow={selectedWorkflow}
          onWorkflowChange={setSelectedWorkflow}
          onExecute={handleExecute}
          loadingExec={loadingExec}
          loadingQualif={loadingQualif}
          executionSteps={executionSteps}
          currentStep={currentStep}
        />
      )}

      {executionResult && <ExecutionResultPanel result={executionResult} />}
    </div>
  );
}
