interface ErrorBannerProps {
  message: string;
  // Affiche « Réessayer » quand l'erreur est transitoire et que l'appelant sait relancer l'action.
  onRetry?: () => void;
  retryable?: boolean;
}

export default function ErrorBanner({ message, onRetry, retryable = true }: ErrorBannerProps) {
  return (
    <div
      role="alert"
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: '10px', padding: '10px 14px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#991b1b', fontSize: '14px', marginBottom: '10px' }}
    >
      <span>❌ {message}</span>
      {onRetry && retryable && (
        <button
          type="button"
          onClick={onRetry}
          style={{ padding: '4px 10px', backgroundColor: '#fff', color: '#991b1b', border: '1px solid #fca5a5', borderRadius: '6px', cursor: 'pointer', fontSize: '13px' }}
        >
          Réessayer
        </button>
      )}
    </div>
  );
}
