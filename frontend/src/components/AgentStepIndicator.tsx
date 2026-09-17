export default function AgentStepIndicator({ steps, currentIndex }: { steps: string[]; currentIndex: number }) {
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
