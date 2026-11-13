import { useState } from 'react';
import { Link, Navigate, useLocation, useNavigate, type Location } from 'react-router-dom';
import api, { getApiErrorMessage } from '../utils/api';
import { useAuth } from '../contexts/AuthContext';
import { ErrorBox } from '../components/ui/feedback';
import { BRAND } from '../utils/theme';

/**
 * Owner sign-in: username and password checked by POST /api/auth/login against the
 * values kept in the server's .env. The token it returns is stored by AuthContext.
 * A real identity provider would replace the form body and keep the
 * setToken -> refresh -> navigate flow.
 */
export default function LoginPage() {
  const { user, token, legacyMode, setToken, refresh } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // RequireAuth passes the guarded location as `state.from`; return there after login.
  const fromLoc = (location.state as { from?: Location } | null)?.from;
  const from = fromLoc ? `${fromLoc.pathname}${fromLoc.search}${fromLoc.hash}` : '/';

  // Bounce only when truly signed in (a token is held). In legacy mode the page stays reachable.
  if (user && token) {
    return <Navigate to={from} replace />;
  }

  const ready = username.trim() !== '' && password !== '';

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setSubmitting(true);
    setError(null);
    try {
      const res = await api.post<{ access_token: string }>('/auth/login', {
        username: username.trim(),
        password,
      });
      setToken(res.data.access_token);
      await refresh();
      navigate(from, { replace: true });
    } catch (e) {
      setPassword('');
      setError(getApiErrorMessage(e, 'Sign-in failed'));
    } finally {
      setSubmitting(false);
    }
  };

  const inputClass =
    'mt-1 w-full bg-page border border-border rounded px-3 py-2 text-sm text-text-primary focus:border-accent-500';
  const labelClass = 'text-[11px] uppercase tracking-wider text-text-secondary';

  return (
    <div className="min-h-screen bg-page text-text-primary flex items-center justify-center px-4">
      <div className="w-full max-w-sm bg-card border border-border rounded-lg p-6 space-y-5">
        <h1 className="text-xl font-bold tracking-tight">Sign in to {BRAND}</h1>

        <form onSubmit={handleLogin} className="space-y-3">
          <label className="block">
            <span className={labelClass}>Username</span>
            <input
              type="text"
              name="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              autoFocus
              required
              className={inputClass}
            />
          </label>
          <label className="block">
            <span className={labelClass}>Password</span>
            <input
              type="password"
              name="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className={inputClass}
            />
          </label>

          {error && <ErrorBox message={error} size="xs" />}

          <button type="submit" disabled={submitting || !ready} className="btn-primary w-full justify-center py-2 text-sm">
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>

        {legacyMode && (
          <p className="text-[11px] text-amber-300 bg-amber-900/15 border border-amber-500/25 rounded px-2.5 py-2">
            <span className="font-semibold">The site is open.</span> The server runs in legacy mode,
            so the <Link to="/" className="text-accent-400 hover:underline">dashboard</Link> opens
            without signing in.
          </p>
        )}
      </div>
    </div>
  );
}
