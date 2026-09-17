import React, { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from './supabaseClient';
import Login from './Login';

interface QualificationReport {
  summary: string;
  is_clear: boolean;
  request_type: 'ANALYSE_ONLY' | 'BUGFIX' | 'FEATURE' | 'DESIGN_AND_DEV';
  questions: string[];
}

// Séquence d'agents par workflow, alignée sur run_dynamic_crew (backend/crewquestion.py).
// Le backend ne fait pas de streaming de progression : cette séquence sert à afficher une
// estimation de l'étape en cours pendant l'attente de la réponse finale de /api/execute.
const WORKFLOW_STEPS: Record<string, string[]> = {
  ANALYSE_ONLY: ['Game Designer — Spécifications', 'Architecte — Structure technique'],
  BUGFIX: ['Développeur — Implémentation', 'QA — Revue qualité'],
  FEATURE: ['Architecte — Structure technique', 'Développeur — Implémentation', 'QA — Revue qualité'],
  DESIGN_AND_DEV: ['Game Designer — Spécifications', 'Architecte — Structure technique', 'Développeur — Implémentation', 'QA — Revue qualité'],
};

const ESTIMATED_STEP_DURATION_MS = 25000;

function AgentStepIndicator({ steps, currentIndex }: { steps: string[]; currentIndex: number }) {
  if (steps.length === 0) return null;

  return (
    <div style={{ marginTop: '20px', backgroundColor: '#fff', padding: '15px', borderRadius: '6px', border: '1px solid #e1e4e8' }}>
      <p style={{ margin: '0 0 10px 0', fontWeight: 'bold' }}>⚙️ Progression estimée :</p>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
        {steps.map((step, i) => {
          const isDone = i < currentIndex;
          const isCurrent = i === currentIndex;
          return (
            <li key={step} style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '14px', color: isCurrent ? '#0070f3' : isDone ? '#059669' : '#9ca3af', transition: 'color 0.2s ease' }}>
              <span aria-hidden style={{ display: 'inline-block', width: '18px', textAlign: 'center' }}>
                {isDone ? '✅' : isCurrent ? '🔄' : '⚪'}
              </span>
              <span style={{ fontWeight: isCurrent ? 'bold' : 'normal' }}>{step}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [authLoading, setAuthLoading] = useState(true);

  useEffect(() => {
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setAuthLoading(false);
    });

    const { data: { subscription } } = supabase.auth.onAuthStateChange((_event, newSession) => {
      setSession(newSession);
    });

    return () => subscription.unsubscribe();
  }, []);

  if (authLoading) {
    return <div style={{ textAlign: 'center', marginTop: '80px', fontFamily: 'system-ui, sans-serif' }}>Chargement...</div>;
  }

  if (!session) {
    return <Login />;
  }

  return <Studio accessToken={session.access_token} userEmail={session.user.email ?? ''} />;
}

function Studio({ accessToken, userEmail }: { accessToken: string; userEmail: string }) {
  const [prompt, setPrompt] = useState('');
  const [loadingQualif, setLoadingQualif] = useState(false);
  const [loadingExec, setLoadingExec] = useState(false);
  
  const [report, setReport] = useState<QualificationReport | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<string>('');
  const [clarifications, setClarifications] = useState('');
  const [executionResult, setExecutionResult] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [repoOwner, setRepoOwner] = useState('');
  const [repoName, setRepoName] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [currentStep, setCurrentStep] = useState(0);

  const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';
  const executionSteps = WORKFLOW_STEPS[selectedWorkflow] ?? [];

  // Avance l'indicateur d'étape à un rythme estimé pendant l'exécution des agents.
  // Reste bloqué sur la dernière étape tant que la réponse finale n'est pas arrivée.
  useEffect(() => {
    if (!loadingExec || executionSteps.length === 0) return;

    const interval = setInterval(() => {
      setCurrentStep((step) => Math.min(step + 1, executionSteps.length - 1));
    }, ESTIMATED_STEP_DURATION_MS);

    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loadingExec]);

  // Étape 1 : Appel API Qualification
  const handleQualify = async (e: React.FormEvent) => {
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
      <header style={{ borderBottom: '2px solid #eaeaea', paddingBottom: '10px', marginBottom: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
        <h2 style={{ margin: 0 }}>🎮 Studio CrewAI — Assistant de Développement</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '14px' }}>
          <span style={{ color: '#666' }}>{userEmail}</span>
          <button
            type="button"
            onClick={() => supabase.auth.signOut()}
            style={{ padding: '6px 12px', backgroundColor: '#f3f4f6', color: '#333', border: '1px solid #ccc', borderRadius: '6px', cursor: 'pointer' }}
          >
            Déconnexion
          </button>
        </div>
      </header>

      {/* Affichage des erreurs en haut du formulaire */}
      {errorMessage && (
        <div style={{ padding: '12px 16px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#991b1b', marginBottom: '20px', fontWeight: '500' }}>
          ❌ {errorMessage}
        </div>
      )}

      {/* Saisie de la demande */}
      <form onSubmit={handleQualify} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <label htmlFor="prompt"><b>Décrivez votre besoin ou bug :</b></label>
        <textarea
          id="prompt"
          rows={4}
          disabled={loadingQualif || loadingExec}
          style={{ width: '100%', padding: '12px', borderRadius: '6px', border: '1px solid #ccc', fontSize: '15px', boxSizing: 'border-box' }}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Ex: Mon application affiche un écran blanc lors de l'ouverture du composant Dashboard..."
        />
        <button 
          type="submit" 
          disabled={loadingQualif || loadingExec || !prompt.trim()} 
          style={{ 
            padding: '12px', 
            backgroundColor: loadingQualif ? '#93c5fd' : '#0070f3', 
            color: '#fff', 
            border: 'none', 
            borderRadius: '6px', 
            cursor: loadingQualif ? 'not-allowed' : 'pointer', 
            fontWeight: 'bold' 
          }}
        >
          {loadingQualif ? '🔍 Analyse et qualification en cours...' : '1. Examiner la demande'}
        </button>
      </form>

      {/* Rapport de qualification */}
      {report && (
        <div style={{ marginTop: '30px', padding: '20px', backgroundColor: '#f8f9fa', border: '1px solid #e1e4e8', borderRadius: '8px' }}>
          <h3>📋 Rapport de Qualification</h3>
          <p><strong>Synthèse :</strong> {report.summary}</p>
          <p><strong>Clarté de la demande :</strong> {report.is_clear ? '✅ Claire' : '⚠️ Ambiguïtés détectées'}</p>

          {/* Questions de clarification */}
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
                onChange={(e) => setClarifications(e.target.value)}
              />
            </div>
          )}

          {/* Repository GitHub cible */}
          <div style={{ marginTop: '20px', backgroundColor: '#fff', padding: '15px', borderRadius: '6px', border: '1px solid #e1e4e8' }}>
            <p style={{ margin: '0 0 10px 0', fontWeight: 'bold' }}>🔗 Repository GitHub cible (optionnel) :</p>
            <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
              <input
                type="text"
                placeholder="owner (ex: titimaes26-lab)"
                disabled={loadingExec}
                value={repoOwner}
                onChange={(e) => setRepoOwner(e.target.value)}
                style={{ flex: '1 1 180px', padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
              />
              <input
                type="text"
                placeholder="repo (ex: myIACrew)"
                disabled={loadingExec}
                value={repoName}
                onChange={(e) => setRepoName(e.target.value)}
                style={{ flex: '1 1 180px', padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
              />
              <input
                type="text"
                placeholder="branche de base"
                disabled={loadingExec}
                value={baseBranch}
                onChange={(e) => setBaseBranch(e.target.value)}
                style={{ flex: '1 1 140px', padding: '8px', borderRadius: '4px', border: '1px solid #ccc' }}
              />
            </div>
            <p style={{ margin: '8px 0 0 0', fontSize: '13px', color: '#666' }}>
              Si renseigné, les agents liront le code depuis ce repository et proposeront leurs changements via une Pull Request.
            </p>
          </div>

          {/* Workflow recommandé & Sélection */}
          <div style={{ marginTop: '20px', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <label htmlFor="workflow"><b>Workflow sélectionné :</b></label>
            <select
              id="workflow"
              value={selectedWorkflow}
              disabled={loadingExec}
              onChange={(e) => setSelectedWorkflow(e.target.value)}
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
            onClick={handleExecute}
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
              fontSize: '16px' 
            }}
          >
            {loadingExec ? '⚙️ Les agents travaillent sur votre projet (veuillez patienter)...' : `2. Lancer le Workflow ${selectedWorkflow}`}
          </button>

          {loadingExec && <AgentStepIndicator steps={executionSteps} currentIndex={currentStep} />}
        </div>
      )}

      {/* Résultat d'exécution */}
      {executionResult && (
        <div style={{ marginTop: '30px', padding: '20px', backgroundColor: '#1e293b', color: '#f8fafc', borderRadius: '8px' }}>
          <h3 style={{ marginTop: 0, color: '#38bdf8' }}>🚀 Livrables générés :</h3>
          <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '14px', fontFamily: 'monospace' }}>
            {executionResult}
          </pre>
        </div>
      )}
    </div>
  );
}
