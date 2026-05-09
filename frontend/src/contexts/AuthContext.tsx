import { createContext, useContext, useEffect, useState, useCallback, ReactNode } from 'react';
import api, { TOKEN_STORAGE_KEY } from '../utils/api';

export interface CurrentUser {
  id: number;
  email: string;
  role: 'USER' | 'ADMIN' | string;
  provider: string | null;
  is_active: boolean;
  legacy_mode: boolean;
  /** User's saved risk profile preference; drives default StrikeRecommender tab. */
  risk_profile: 'conservative' | 'moderate' | 'aggressive';
}

interface AuthState {
  user: CurrentUser | null;
  loading: boolean;
  error: string | null;
  /** When true, backend is in LEGACY_MODE — user is auto-injected, no login required. */
  legacyMode: boolean;
  /** Set by login flow; cleared by logout. Persisted to localStorage. */
  token: string | null;
  setToken: (t: string | null) => void;
  logout: () => void;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | undefined>(undefined);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [token, setTokenState] = useState<string | null>(
    () => localStorage.getItem(TOKEN_STORAGE_KEY),
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const setToken = useCallback((t: string | null) => {
    if (t) localStorage.setItem(TOKEN_STORAGE_KEY, t);
    else localStorage.removeItem(TOKEN_STORAGE_KEY);
    setTokenState(t);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<CurrentUser>('/me');
      setUser(res.data);
    } catch (e) {
      const status = (e as { response?: { status?: number } }).response?.status;
      if (status === 401) {
        // Stale token — clear and surface as unauthenticated rather than error
        setToken(null);
        setUser(null);
      } else {
        setError(e instanceof Error ? e.message : 'Failed to load user');
      }
    } finally {
      setLoading(false);
    }
  }, [setToken]);

  useEffect(() => {
    refresh();
  }, [refresh, token]);

  const logout = useCallback(() => {
    setToken(null);
    setUser(null);
  }, [setToken]);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        error,
        legacyMode: user?.legacy_mode ?? false,
        token,
        setToken,
        logout,
        refresh,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider');
  return ctx;
}
