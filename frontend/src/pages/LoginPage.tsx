import { useState } from 'react';
import { Navigate, useNavigate } from 'react-router-dom';
import api from '../utils/api';
import { useAuth } from '../contexts/AuthContext';

/**
 * Minimal login surface. Two states:
 *
 * 1. LEGACY_MODE backend → no login required; this page just shows a notice
 *    and redirects to dashboard.
 * 2. JWT mode + DEV_TOKEN_ENABLED → mints a short-lived HS256 dev token
 *    via POST /api/dev/token. This is a temporary scaffold; the real
 *    Supabase / Clerk / Auth0 flow replaces this once a provider is picked.
 */
export default function LoginPage() {
  const { user, token, legacyMode, setToken, refresh } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Bounce only when truly signed in (real token). The LEGACY_MODE-resolved
  // legacy admin still lets the user reach this page to switch identity.
  if (user && token) {
    return <Navigate to="/" replace />;
  }

  const handleDevLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.post<{ access_token: string }>('/dev/token', {
        email: email.trim(),
      });
      setToken(res.data.access_token);
      await refresh();
      navigate('/', { replace: true });
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
      setError(detail || (e instanceof Error ? e.message : 'Sign-in failed'));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-page text-text-primary flex items-center justify-center px-4">
      <div className="w-full max-w-sm bg-card border border-border rounded-lg p-6 space-y-5">
        <div>
          <h1 className="text-xl font-bold tracking-tight mb-1">Sign in to Vela</h1>
          <p className="text-xs text-text-secondary leading-snug">
            Auth provider integration (Supabase / Clerk / Auth0) lands in a follow-up.
            This page is a dev-token fallback for local testing; ask the backend to
            enable <code className="font-mono text-text-primary">DEV_TOKEN_ENABLED</code>.
          </p>
        </div>

        <form onSubmit={handleDevLogin} className="space-y-3">
          <label className="block">
            <span className="text-[11px] uppercase tracking-wider text-text-secondary">Email</span>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              required
              className="mt-1 w-full bg-page border border-border rounded px-3 py-2 text-sm text-text-primary focus:outline-none focus:border-purple-500"
            />
          </label>

          {error && (
            <p className="text-xs text-red-400 bg-red-900/20 border border-red-900/40 rounded px-2 py-1.5">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={submitting || !email.trim()}
            className="w-full py-2 rounded-md text-sm font-semibold bg-purple-600 hover:bg-purple-500 text-white transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {submitting ? 'Signing in…' : 'Continue with dev token'}
          </button>
        </form>

        {legacyMode && (
          <p className="text-[11px] text-amber-300 bg-amber-900/15 border border-amber-500/25 rounded px-2.5 py-2">
            <span className="font-semibold">Legacy mode is on.</span> If you don't sign in
            here, the <a href="/" className="text-purple-400 hover:underline">dashboard</a> opens
            as the legacy admin. Sign in above to switch to a real user (e.g.{' '}
            <code className="font-mono">anthony@vela.io</code>).
          </p>
        )}
      </div>
    </div>
  );
}
