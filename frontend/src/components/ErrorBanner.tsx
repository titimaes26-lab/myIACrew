import Alert from './ui/Alert';
import Button from './ui/Button';

interface ErrorBannerProps {
  message: string;
  // « Réessayer » n'apparaît que si l'erreur est transitoire (retryable) ET que l'appelant sait relancer l'action.
  onRetry?: () => void;
  retryable?: boolean;
}

export default function ErrorBanner({ message, onRetry, retryable = false }: ErrorBannerProps) {
  return (
    <Alert tone="danger">
      <span>❌ {message}</span>
      {onRetry && retryable && <Button size="sm" onClick={onRetry}>Réessayer</Button>}
    </Alert>
  );
}
