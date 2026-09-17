import { useEffect, useState } from 'react';
import { WORKFLOW_STEPS, DEFAULT_STEPS } from '../constants/workflowSteps';

const STEP_ADVANCE_MS = 9000;

export default function StepIndicator({ workflow }: { workflow?: string }) {
  const steps = (workflow && WORKFLOW_STEPS[workflow]) || DEFAULT_STEPS;
  const [activeIndex, setActiveIndex] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setActiveIndex((i) => Math.min(i + 1, steps.length - 1));
    }, STEP_ADVANCE_MS);
    return () => clearInterval(interval);
  }, [steps.length]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', padding: '4px 0' }}>
      {steps.map((step, i) => {
        const done = i < activeIndex;
        const current = i === activeIndex;
        return (
          <div
            key={step.key}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              fontSize: '13px',
              color: done ? '#16a34a' : current ? '#0070f3' : '#999',
            }}
          >
            <span>{done ? '✅' : current ? step.icon : '⏳'}</span>
            <span style={{ fontWeight: current ? 600 : 400 }}>
              {step.label}
              {current ? '…' : ''}
            </span>
          </div>
        );
      })}
      <p style={{ fontSize: '11px', color: '#999', margin: '4px 0 0 0', fontStyle: 'italic' }}>
        Progression estimée — l'étape réellement en cours côté serveur peut différer.
      </p>
    </div>
  );
}
