import { useEffect, useState } from 'react';
import type { Session } from '@supabase/supabase-js';
import { supabase } from './supabaseClient';
import Login from './Login';
import Studio from './Studio';

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
