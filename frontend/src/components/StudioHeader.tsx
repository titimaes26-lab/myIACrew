import { supabase } from '../supabaseClient';
import { clearChatDraft } from '../utils/chatDraft';

const handleSignOut = () => {
  clearChatDraft();
  supabase.auth.signOut();
};

interface StudioHeaderProps {
  userEmail: string;
  historyOpen: boolean;
  onToggleHistory: () => void;
}

export default function StudioHeader({ userEmail, historyOpen, onToggleHistory }: StudioHeaderProps) {
  return (
    <header style={{ borderBottom: '2px solid #eaeaea', paddingBottom: '10px', marginBottom: '20px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '10px' }}>
      <h2 style={{ margin: 0 }}>🎮 Studio CrewAI — Assistant de Développement</h2>
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: '10px', fontSize: '14px' }}>
        <span style={{ color: '#666' }}>{userEmail}</span>
        <button
          type="button"
          onClick={onToggleHistory}
          style={{ padding: '6px 12px', backgroundColor: historyOpen ? '#e0f2fe' : '#f3f4f6', color: '#333', border: '1px solid #ccc', borderRadius: '6px', cursor: 'pointer' }}
        >
          📜 Historique
        </button>
        <button
          type="button"
          onClick={handleSignOut}
          style={{ padding: '6px 12px', backgroundColor: '#f3f4f6', color: '#333', border: '1px solid #ccc', borderRadius: '6px', cursor: 'pointer' }}
        >
          Déconnexion
        </button>
      </div>
    </header>
  );
}
