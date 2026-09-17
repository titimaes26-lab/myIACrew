import { useEffect, useState } from 'react';
import { WORKFLOW_STEPS, DEFAULT_STEPS } from '../constants/workflowSteps';
import { formatSeconds } from '../utils/formatDuration';
import { toServerDate } from '../utils/serverDate';

const STEP_ADVANCE_MS = 9000;
const TICK_MS = 1000;

function elapsedSecondsSince(since: string): number {
  return (Date.now() - toServerDate(since).getTime()) / 1000;
}

interface StepIndicatorProps {
  workflow?: string;
  // Timestamp de départ du tour (turn.createdAt), utilisé plutôt que l'instant de montage
  // du composant : celui-ci peut être remonté (changement de `key`) une fois le workflow
  // connu, ce qui décalerait un chrono basé sur le montage.
  since: string;
}

export default function StepIndicator({ workflow, since }: StepIndicatorProps) {
  const steps = (workflow && WORKFLOW_STEPS[workflow]) || DEFAULT_STEPS;
  const [activeIndex, setActiveIndex] = useState(0);
  const [elapsed, setElapsed] = useState(() => elapsedSecondsSince(since));

  useEffect(() => {
    const stepTimer = setInterval(() => {
      setActiveIndex((i) => Math.min(i + 1, steps.length - 1));
    }, STEP_ADVANCE_MS);
    const tickTimer = setInterval(() => {
      setElapsed(elapsedSecondsSince(since));
    }, TICK_MS);
    return () => {
      clearInterval(stepTimer);
      clearInterval(tickTimer);
    };
  }, [steps.length, since]);

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
        En cours depuis {formatSeconds(elapsed)} — progression estimée, l'étape réellement en cours côté serveur peut différer.
      </p>
    </div>
  );
}
