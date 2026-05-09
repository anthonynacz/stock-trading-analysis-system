import { useCallback, useEffect, useState } from 'react';
import api from '../utils/api';

// ── Types ───────────────────────────────────────────────────────────────────

export type RiskProfile = 'conservative' | 'moderate' | 'aggressive';

export interface AlertsConfig {
  channel: 'email' | 'discord';
  rec_change: boolean;
  conviction_breach: boolean;
  conviction_threshold: number;
  earnings_proximity: boolean;
  earnings_days_before: number;
  unusual_flow: boolean;
  news_spike: boolean;
  insider_filing: boolean;
  discord_webhook_url: string | null;
}

export interface DigestConfig {
  enabled: boolean;
  send_time_utc: string;
  include_top_picks: boolean;
  include_position_health: boolean;
  include_catalysts_3d: boolean;
  include_industry_summary: boolean;
}

export interface UserPreferences {
  risk_profile: RiskProfile;
  signal_group_weights: Record<string, number>;
  industry_weights: Record<string, number>;
  custom_universe: string[];
  alerts_config: AlertsConfig;
  digest_config: DigestConfig;
  updated_at: string | null;
}

export interface CatalogEntry {
  key: string;
  label: string;
  description: string;
  tier_min?: string;
}

export interface PreferencesCatalog {
  risk_profiles: CatalogEntry[];
  signal_groups: CatalogEntry[];
  alert_keys: CatalogEntry[];
  industries: string[];
}

interface PreferencesPayload {
  preferences: UserPreferences;
  catalog: PreferencesCatalog;
}

// ── Hook ────────────────────────────────────────────────────────────────────

interface State {
  prefs: UserPreferences | null;
  draft: UserPreferences | null;
  catalog: PreferencesCatalog | null;
  loading: boolean;
  saving: boolean;
  error: string | null;
  dirty: boolean;
  setDraft: <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => void;
  patchAlerts: <K extends keyof AlertsConfig>(key: K, value: AlertsConfig[K]) => void;
  patchDigest: <K extends keyof DigestConfig>(key: K, value: DigestConfig[K]) => void;
  patchSignalWeight: (group: string, value: number) => void;
  patchIndustryWeight: (industry: string, value: number) => void;
  reset: () => void;
  save: () => Promise<void>;
}

export function usePreferences(): State {
  const [prefs, setPrefs] = useState<UserPreferences | null>(null);
  const [draft, setDraftState] = useState<UserPreferences | null>(null);
  const [catalog, setCatalog] = useState<PreferencesCatalog | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.get<PreferencesPayload>('/me/preferences');
      setPrefs(res.data.preferences);
      setDraftState(res.data.preferences);
      setCatalog(res.data.catalog);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load preferences');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const setDraft = useCallback(
    <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => {
      setDraftState((d) => (d ? { ...d, [key]: value } : d));
    },
    [],
  );

  const patchAlerts = useCallback(
    <K extends keyof AlertsConfig>(key: K, value: AlertsConfig[K]) => {
      setDraftState((d) =>
        d ? { ...d, alerts_config: { ...d.alerts_config, [key]: value } } : d,
      );
    },
    [],
  );

  const patchDigest = useCallback(
    <K extends keyof DigestConfig>(key: K, value: DigestConfig[K]) => {
      setDraftState((d) =>
        d ? { ...d, digest_config: { ...d.digest_config, [key]: value } } : d,
      );
    },
    [],
  );

  const patchSignalWeight = useCallback((group: string, value: number) => {
    setDraftState((d) =>
      d
        ? { ...d, signal_group_weights: { ...d.signal_group_weights, [group]: value } }
        : d,
    );
  }, []);

  const patchIndustryWeight = useCallback((industry: string, value: number) => {
    setDraftState((d) =>
      d ? { ...d, industry_weights: { ...d.industry_weights, [industry]: value } } : d,
    );
  }, []);

  const reset = useCallback(() => {
    setDraftState(prefs);
  }, [prefs]);

  const save = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const res = await api.put<{ preferences: UserPreferences }>('/me/preferences', draft);
      setPrefs(res.data.preferences);
      setDraftState(res.data.preferences);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save preferences');
    } finally {
      setSaving(false);
    }
  }, [draft]);

  const dirty = !!prefs && !!draft && JSON.stringify(prefs) !== JSON.stringify(draft);

  return {
    prefs,
    draft,
    catalog,
    loading,
    saving,
    error,
    dirty,
    setDraft,
    patchAlerts,
    patchDigest,
    patchSignalWeight,
    patchIndustryWeight,
    reset,
    save,
  };
}
