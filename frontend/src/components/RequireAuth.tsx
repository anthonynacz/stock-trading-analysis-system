import { ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

/**
 * Guards a route by requiring an authenticated user.
 *
 * In LEGACY_MODE the backend auto-resolves the legacy admin, so as long as
 * /api/me succeeded we render through. In JWT mode, missing/invalid token
 * means user is null and we redirect to /login (preserving where they came
 * from so post-login can return them).
 */
export default function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen bg-page flex items-center justify-center text-text-secondary text-sm">
        <div className="w-4 h-4 border-2 border-text-secondary border-t-transparent rounded-full animate-spin mr-2" />
        Loading…
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  return <>{children}</>;
}
