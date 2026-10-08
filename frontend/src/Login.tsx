import React, { useState } from 'react';
import { supabase } from './supabaseClient';
import Button from './components/ui/Button';

export default function Login() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const { error: signInError } = await supabase.auth.signInWithPassword({ email, password });
      if (signInError) throw signInError;
    } catch (err) {
      setError((err instanceof Error && err.message) || "Erreur d'authentification.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="login">
      <h2>🔐 Studio CrewAI</h2>
      <form onSubmit={handleSubmit}>
        <input
          type="email"
          required
          placeholder="Email"
          aria-label="Email"
          className="field"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <input
          type="password"
          required
          placeholder="Mot de passe"
          aria-label="Mot de passe"
          className="field"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <div role="alert" className="login__error">❌ {error}</div>}
        <Button type="submit" variant="primary" disabled={loading}>
          {loading ? 'Patientez...' : 'Se connecter'}
        </Button>
      </form>
      <p className="login__note">Les comptes sont créés depuis le dashboard Supabase.</p>
    </div>
  );
}
