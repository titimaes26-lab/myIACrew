import React, { useState } from 'react';
import { supabase } from './supabaseClient';

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
    } catch (err: any) {
      setError(err.message || "Erreur d'authentification.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div style={{ maxWidth: '380px', margin: '80px auto', fontFamily: 'system-ui, sans-serif', padding: '30px', border: '1px solid #e1e4e8', borderRadius: '8px', color: '#333' }}>
      <h2 style={{ marginTop: 0, textAlign: 'center' }}>🔐 Studio CrewAI</h2>
      <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <input
          type="email"
          required
          placeholder="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          style={{ padding: '10px', borderRadius: '6px', border: '1px solid #ccc', boxSizing: 'border-box' }}
        />
        <input
          type="password"
          required
          minLength={6}
          placeholder="Mot de passe"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          style={{ padding: '10px', borderRadius: '6px', border: '1px solid #ccc', boxSizing: 'border-box' }}
        />
        {error && <div style={{ color: '#991b1b', fontSize: '14px' }}>❌ {error}</div>}
        <button
          type="submit"
          disabled={loading}
          style={{ padding: '12px', backgroundColor: '#0070f3', color: '#fff', border: 'none', borderRadius: '6px', cursor: loading ? 'not-allowed' : 'pointer', fontWeight: 'bold' }}
        >
          {loading ? 'Patientez...' : 'Se connecter'}
        </button>
      </form>
      <p style={{ textAlign: 'center', marginTop: '15px', fontSize: '13px', color: '#666' }}>
        Les comptes sont créés depuis le dashboard Supabase.
      </p>
    </div>
  );
}
