import { supabase } from '../supabaseClient';
import Button from './ui/Button';
import type { NotifyPermission } from '../hooks/useNotifyPreference';

interface StudioHeaderProps {
  userEmail: string;
  historyOpen: boolean;
  onToggleHistory: () => void;
  metricsOpen: boolean;
  onToggleMetrics: () => void;
  // Notification de fin d'exécution (navigateur) ; le bouton n'existe que si le navigateur la gère.
  notifyEnabled: boolean;
  notifyPermission: NotifyPermission;
  onToggleNotify: () => void;
}

export default function StudioHeader({ userEmail, historyOpen, onToggleHistory, metricsOpen, onToggleMetrics, notifyEnabled, notifyPermission, onToggleNotify }: StudioHeaderProps) {
  return (
    <header className="studio-header">
      <h2>🎮 Studio CrewAI — Assistant de Développement</h2>
      <div className="studio-header__actions">
        <span className="muted">{userEmail}</span>
        <Button onClick={onToggleHistory} aria-pressed={historyOpen}>📜 Historique</Button>
        <Button onClick={onToggleMetrics} aria-pressed={metricsOpen}>📊 Performance</Button>
        {notifyPermission !== 'unsupported' && (
          <Button
            onClick={onToggleNotify}
            aria-pressed={notifyEnabled}
            title={notifyPermission === 'denied'
              ? 'Notifications bloquées : autorise-les dans les réglages du site de ton navigateur.'
              : 'Me prévenir à la fin d\'une exécution quand je suis sur un autre onglet'}
          >
            🔔 Me prévenir{notifyPermission === 'denied' ? ' (bloqué)' : ''}
          </Button>
        )}
        <Button onClick={() => supabase.auth.signOut()}>Déconnexion</Button>
      </div>
    </header>
  );
}
