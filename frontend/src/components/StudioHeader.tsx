import { supabase } from '../supabaseClient';
import Button from './ui/Button';

interface StudioHeaderProps {
  userEmail: string;
  historyOpen: boolean;
  onToggleHistory: () => void;
  metricsOpen: boolean;
  onToggleMetrics: () => void;
}

export default function StudioHeader({ userEmail, historyOpen, onToggleHistory, metricsOpen, onToggleMetrics }: StudioHeaderProps) {
  return (
    <header className="studio-header">
      <h2>🎮 Studio CrewAI — Assistant de Développement</h2>
      <div className="studio-header__actions">
        <span className="muted">{userEmail}</span>
        <Button onClick={onToggleHistory} aria-pressed={historyOpen}>📜 Historique</Button>
        <Button onClick={onToggleMetrics} aria-pressed={metricsOpen}>📊 Performance</Button>
        <Button onClick={() => supabase.auth.signOut()}>Déconnexion</Button>
      </div>
    </header>
  );
}
