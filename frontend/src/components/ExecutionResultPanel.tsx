export default function ExecutionResultPanel({ result }: { result: string }) {
  return (
    <div style={{ marginTop: '30px', padding: '20px', backgroundColor: '#1e293b', color: '#f8fafc', borderRadius: '8px' }}>
      <h3 style={{ marginTop: 0, color: '#38bdf8' }}>🚀 Livrables générés :</h3>
      <pre style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: '14px', fontFamily: 'monospace' }}>
        {result}
      </pre>
    </div>
  );
}
