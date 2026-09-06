import { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { ErrorBox, PageSpinner } from './ui/feedback';

/**
 * Guards a route by requiring an authenticated user.
 *
 * In LEGACY_MODE the backend auto-resolves the legacy admin, so as long as
 * /api/me succeeded we render through. In JWT mode, missing/invalid token
 * means user is null and we redirect to /login (preserving where they came
 * from so post-login can return them). A non-401 failure of /api/me (backend
 * down, 5xx) is an outage, not a logout, so it shows a retry panel instead.
 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading, error, refresh } = useAuth();
  const location = useLocation();

  if (loading) {
    return <PageSpinner />;
  }

  if (error && !user) {
    return (
      <div className="min-h-screen bg-page text-text-primary flex items-center justify-center px-4">
        <div className="w-full max-w-sm bg-card border border-border rounded-lg p-6 space-y-4">
          <h1 className="text-lg font-bold tracking-tight">Cannot reach the API</h1>
          <ErrorBox message={error} size="xs" />
          <button
            type="button"
            onClick={() => refresh()}
            className="w-full py-2 rounded-md text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white transition-colors"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
