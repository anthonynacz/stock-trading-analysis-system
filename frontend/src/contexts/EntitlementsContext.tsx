import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import api from '../utils/api';
import { useAuth } from './AuthContext';

export type Tier = 'FREE' | 'STARTER' | 'PRO' | 'PREMIUM' | 'ADMIN';

export interface TierEntitlements {
  label: string;
  monthly_research_credits: number;
  monthly_deep_options_credits: number;
  monthly_scanner_credits: number;
  max_watchlist_tickers: number;
  max_open_positions: number;
  max_universe_slots: number;
  research_retention_days: number;
  alerts_enabled: boolean;
  custom_signal_weights: boolean;
  position_aware_recs: boolean;
  api_access: boolean;
  real_time_data: boolean;
  manual_pipeline_trigger: boolean;
  /** Per-tier pipeline-phase allowlist. Empty = locked; subset = intraday only;
   *  full 8 = full pipeline. Frontend uses this to decide which presets/buttons to expose. */
  allowed_pipeline_phases: string[];
}

export interface CreditState {
  balance: number;
  monthly_quota: number;
}

export interface EntitlementsResponse {
  tier: Tier;
  tier_label: string;
  subscription_status: string;
  current_period_end: string | null;
  entitlements: TierEntitlements;
  credits: Record<'research' | 'deep_options' | 'scanner', CreditState>;
  unlimited_sentinel: number;
}

interface State {
  data: EntitlementsResponse | null;
  loading: boolean;
  isUnlimited: (n: number) => boolean;
  isAdmin: boolean;
  refresh: () => Promise<void>;
}

const Ctx = createContext<State | undefined>(undefined);

export function EntitlementsProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const userId = user?.id;
  const [data, setData] = useState<EntitlementsResponse | null>(null);
  const [loading, setLoading] = useState(false);

  // Keyed on the id, not the user object: every /me refetch yields a new
  // object, which would otherwise trigger a redundant entitlements fetch.
  const refresh = useCallback(async () => {
    if (userId == null) {
      setData(null);
      return;
    }
    setLoading(true);
    try {
      const res = await api.get<EntitlementsResponse>('/me/entitlements');
      setData(res.data);
    } catch {
      // Non-fatal: consumers render without tier/credit info until the next refresh.
    } finally {
      setLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const isUnlimited = useCallback(
    (n: number) => (data ? n >= data.unlimited_sentinel : false),
    [data],
  );

  const value = useMemo<State>(
    () => ({
      data,
      loading,
      isUnlimited,
      isAdmin: data?.tier === 'ADMIN',
      refresh,
    }),
    [data, loading, isUnlimited, refresh],
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useEntitlements(): State {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useEntitlements must be used inside EntitlementsProvider');
  return ctx;
}

/** Render N as "∞" when N is the unlimited sentinel. */
export function fmtCount(n: number, sentinel: number): string {
  if (n >= sentinel) return '∞';
  return n.toString();
}
