import { useState, useMemo } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { useEntitlements, fmtCount } from '../contexts/EntitlementsContext';
import {
  usePreferences,
  type RiskProfile,
  type UserPreferences,
} from '../hooks/usePreferences';
import { LockedBadge } from '../components/ui/LockedBadge';
import { LoadingRow, ErrorBox } from '../components/ui/feedback';
import { SegmentedControl } from '../components/ui/SegmentedControl';

// ── Tier ordering for "tier_min" gating ────────────────────────────────────
const TIER_RANK: Record<string, number> = {
  FREE: 0,
  STARTER: 1,
  PRO: 2,
  PREMIUM: 3,
  ADMIN: 99,
};

function tierMeetsMin(currentTier: string | undefined, minTier: string | undefined): boolean {
  if (!minTier) return true;
  const cur = TIER_RANK[currentTier ?? 'FREE'] ?? 0;
  const min = TIER_RANK[minTier] ?? 0;
  return cur >= min;
}

// ── Section types ──────────────────────────────────────────────────────────
type SectionKey = 'profile' | 'risk' | 'signals' | 'industries' | 'alerts' | 'universe' | 'digest';

const SECTIONS: { key: SectionKey; label: string; description: string }[] = [
  { key: 'profile',    label: 'Profile',          description: 'Account, tier, retention.' },
  { key: 'risk',       label: 'Risk Profile',     description: 'Strike recommender + signal lean.' },
  { key: 'signals',    label: 'Signal Weights',   description: 'Boost or mute signal categories.' },
  { key: 'industries', label: 'Industry Weights', description: 'Sector boost / mute.' },
  { key: 'alerts',     label: 'Alerts',           description: 'Email triggers and thresholds.' },
  { key: 'universe',   label: 'Custom Universe',  description: 'Add tickers to your daily pipeline.' },
  { key: 'digest',     label: 'AM Digest',        description: '7am summary email.' },
];

// ── Reusable bits ──────────────────────────────────────────────────────────

function WeightSlider({
  label,
  value,
  onChange,
  disabled,
  description,
  rightSlot,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  description?: string;
  rightSlot?: React.ReactNode;
}) {
  const muted = value === 0;
  const boosted = value > 1.05;
  const tone = muted ? 'text-text-secondary' : boosted ? 'text-emerald-400' : 'text-text-primary';
  return (
    <div className={`px-3 py-2.5 rounded border border-border ${disabled ? 'opacity-50' : ''}`}>
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-text-primary">{label}</div>
          {description && <div className="text-[11px] text-text-secondary leading-snug">{description}</div>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {rightSlot}
          <span className={`text-xs font-mono tabular-nums w-10 text-right ${tone}`}>
            {value.toFixed(2)}×
          </span>
        </div>
      </div>
      <input
        type="range"
        min={0}
        max={2}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
        className="w-full accent-accent-500"
      />
      <div className="flex justify-between text-[10px] text-text-secondary mt-0.5">
        <span>Mute</span>
        <span>Neutral</span>
        <span>Boost</span>
      </div>
    </div>
  );
}

function PaneHeader({ title, description }: { title: string; description: string }) {
  return (
    <div className="mb-4">
      <h2 className="text-lg font-bold text-text-primary">{title}</h2>
      <p className="text-xs text-text-secondary">{description}</p>
    </div>
  );
}

// ── Panes ──────────────────────────────────────────────────────────────────

function ProfilePane() {
  const { user } = useAuth();
  const { data: ent } = useEntitlements();
  if (!user) return null;
  const isLegacySession = user.provider === 'legacy';
  return (
    <div>
      <PaneHeader title="Profile" description="Read-only summary of your account state." />
      <div className="space-y-2 text-sm">
        <Row label="Email" value={user.email} />
        <Row label="Provider" value={user.provider ?? '—'} />
        <Row label="Role" value={user.role} />
        <Row label="Tier" value={ent?.tier_label ?? user.role} />
        <Row label="Subscription" value={ent?.subscription_status ?? '—'} />
        <Row
          label="Period ends"
          value={ent?.current_period_end ? new Date(ent.current_period_end).toLocaleString() : '—'}
        />
        <Row
          label="Research retention"
          value={
            ent
              ? `${fmtCount(ent.entitlements.research_retention_days, ent.unlimited_sentinel)} days`
              : '—'
          }
        />
        {isLegacySession && (
          <p className="text-[11px] text-amber-300 bg-amber-900/20 border border-amber-500/30 rounded px-2 py-1.5 mt-3">
            You are signed in as the <code className="font-mono">legacy admin</code> via auth bypass.
            All quota checks are skipped. Sign in via the top-right link to switch to a real user.
          </p>
        )}
      </div>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between border-b border-border/50 pb-1.5">
      <span className="text-text-secondary">{label}</span>
      <span className="font-mono text-text-primary text-right">{value}</span>
    </div>
  );
}

function RiskProfilePane({
  draft,
  setDraft,
  catalog,
}: {
  draft: UserPreferences;
  setDraft: ReturnType<typeof usePreferences>['setDraft'];
  catalog: NonNullable<ReturnType<typeof usePreferences>['catalog']>;
}) {
  return (
    <div>
      <PaneHeader
        title="Risk Profile"
        description="Drives the strike recommender's preferred delta range and DTE window."
      />
      <div className="space-y-2">
        {catalog.risk_profiles.map((rp) => {
          const active = draft.risk_profile === rp.key;
          return (
            <button
              key={rp.key}
              onClick={() => setDraft('risk_profile', rp.key as RiskProfile)}
              className={`w-full text-left px-3 py-3 rounded border transition-colors ${
                active
                  ? 'border-accent-500 bg-accent-900/20'
                  : 'border-border hover:border-text-secondary/40 hover:bg-border/20'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-semibold text-text-primary capitalize">{rp.label}</span>
                {active && (
                  <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-px rounded bg-accent-500/20 text-accent-300">
                    Active
                  </span>
                )}
              </div>
              <p className="text-xs text-text-secondary mt-1 leading-snug">{rp.description}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

function SignalWeightsPane({
  draft,
  catalog,
  patchSignalWeight,
}: {
  draft: UserPreferences;
  catalog: NonNullable<ReturnType<typeof usePreferences>['catalog']>;
  patchSignalWeight: (group: string, v: number) => void;
}) {
  const { data: ent } = useEntitlements();
  const allowed = !!ent?.entitlements.custom_signal_weights;
  return (
    <div>
      <PaneHeader
        title="Signal Weights"
        description={
          allowed
            ? 'Multiply how much each signal category contributes to conviction. 1.0 = neutral, 0 = mute, 2 = double.'
            : 'Custom signal weighting is a Pro feature — your settings are saved but not yet applied.'
        }
      />
      {!allowed && (
        <div className="mb-3 px-3 py-2 rounded border border-amber-500/30 bg-amber-900/20 text-xs text-amber-200">
          🔒 Signal weights take effect on the <strong>Pro</strong> tier and above. You can configure
          them now — they'll activate when you upgrade.
        </div>
      )}
      <div className="space-y-2">
        {catalog.signal_groups.map((g) => (
          <WeightSlider
            key={g.key}
            label={g.label}
            description={g.description}
            value={draft.signal_group_weights[g.key] ?? 1.0}
            onChange={(v) => patchSignalWeight(g.key, v)}
            disabled={false /* still allow editing; gating is on apply */}
          />
        ))}
      </div>
    </div>
  );
}

function IndustryWeightsPane({
  draft,
  catalog,
  patchIndustryWeight,
}: {
  draft: UserPreferences;
  catalog: NonNullable<ReturnType<typeof usePreferences>['catalog']>;
  patchIndustryWeight: (industry: string, v: number) => void;
}) {
  return (
    <div>
      <PaneHeader
        title="Industry Weights"
        description="Boost or mute sectors in the dashboard ranking. 0 = hidden, 2 = double-weighted."
      />
      <div className="space-y-2">
        {catalog.industries.map((sector) => (
          <WeightSlider
            key={sector}
            label={sector}
            value={draft.industry_weights[sector] ?? 1.0}
            onChange={(v) => patchIndustryWeight(sector, v)}
          />
        ))}
      </div>
    </div>
  );
}

function AlertsPane({
  draft,
  patchAlerts,
  catalog,
}: {
  draft: UserPreferences;
  patchAlerts: ReturnType<typeof usePreferences>['patchAlerts'];
  catalog: NonNullable<ReturnType<typeof usePreferences>['catalog']>;
}) {
  const { data: ent } = useEntitlements();
  const tier = ent?.tier;
  const alertsEnabled = !!ent?.entitlements.alerts_enabled;
  const alerts = draft.alerts_config;
  const discordAllowed = tier === 'PREMIUM' || tier === 'ADMIN';

  return (
    <div>
      <PaneHeader
        title="Alerts"
        description="Email or Discord when watchlist tickers cross trigger conditions."
      />
      {!alertsEnabled && (
        <div className="mb-3 px-3 py-2 rounded border border-amber-500/30 bg-amber-900/20 text-xs text-amber-200">
          🔒 Alerts unlock on <strong>Starter</strong> tier and above. Save your settings now and
          they'll trigger on upgrade.
        </div>
      )}

      <div className="space-y-2">
        {catalog.alert_keys.map((a) => {
          const meets = tierMeetsMin(tier, a.tier_min);
          const fieldKey = a.key as keyof typeof alerts;
          const enabled = alerts[fieldKey] as boolean;
          return (
            <div
              key={a.key}
              className={`px-3 py-2.5 rounded border border-border ${meets ? '' : 'opacity-60'}`}
            >
              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => patchAlerts(fieldKey as 'rec_change', e.target.checked as never)}
                  className="mt-1 accent-accent-500"
                />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-text-primary">{a.label}</span>
                    {a.tier_min && !meets && <LockedBadge minTier={a.tier_min} />}
                  </div>
                  <p className="text-[11px] text-text-secondary leading-snug">{a.description}</p>
                </div>
              </label>
              {a.key === 'conviction_breach' && enabled && (
                <div className="mt-2 ml-7 flex items-center gap-2 text-xs">
                  <span className="text-text-secondary">Threshold (|conviction|):</span>
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={alerts.conviction_threshold}
                    onChange={(e) =>
                      patchAlerts('conviction_threshold', Number(e.target.value))
                    }
                    className="w-16 bg-page border border-border rounded px-2 py-0.5 text-text-primary font-mono"
                  />
                </div>
              )}
              {a.key === 'earnings_proximity' && enabled && (
                <div className="mt-2 ml-7 flex items-center gap-2 text-xs">
                  <span className="text-text-secondary">Days before:</span>
                  <input
                    type="number"
                    min={0}
                    max={14}
                    value={alerts.earnings_days_before}
                    onChange={(e) =>
                      patchAlerts('earnings_days_before', Number(e.target.value))
                    }
                    className="w-16 bg-page border border-border rounded px-2 py-0.5 text-text-primary font-mono"
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      <div className="mt-4 px-3 py-2.5 rounded border border-border">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="text-sm font-semibold text-text-primary">Delivery</div>
            <p className="text-[11px] text-text-secondary leading-snug">
              Where triggered alerts are sent.
            </p>
          </div>
          <SegmentedControl
            variant="joined"
            value={alerts.channel}
            onChange={(v) => patchAlerts('channel', v)}
            options={[
              { key: 'email', label: 'Email' },
              {
                key: 'discord',
                label: 'Discord',
                disabled: !discordAllowed,
                title: discordAllowed ? undefined : 'Premium tier only',
              },
            ]}
          />
        </div>
      </div>

      <div className="mt-2 px-3 py-2.5 rounded border border-border">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-semibold text-text-primary">Discord webhook</span>
          <LockedBadge minTier="PREMIUM" />
        </div>
        <p className="text-[11px] text-text-secondary mb-2">Real-time push to a Discord channel.</p>
        <input
          type="text"
          placeholder="https://discord.com/api/webhooks/..."
          value={alerts.discord_webhook_url ?? ''}
          onChange={(e) => {
            const url = e.target.value || null;
            const hadUrl = !!alerts.discord_webhook_url;
            patchAlerts('discord_webhook_url', url);
            // Auto-switch only on the empty→filled and filled→empty transitions so an
            // explicit Email choice survives later edits to the webhook.
            if (url && !hadUrl && alerts.channel === 'email') {
              patchAlerts('channel', 'discord');
            } else if (!url && alerts.channel === 'discord') {
              patchAlerts('channel', 'email');
            }
          }}
          disabled={!discordAllowed}
          className="w-full bg-page border border-border rounded px-3 py-1.5 text-xs text-text-primary font-mono disabled:opacity-50"
        />
      </div>
    </div>
  );
}

function CustomUniversePane({
  draft,
  setDraft,
}: {
  draft: UserPreferences;
  setDraft: ReturnType<typeof usePreferences>['setDraft'];
}) {
  const { data: ent } = useEntitlements();
  const slots = ent?.entitlements.max_universe_slots ?? 0;
  const sentinel = ent?.unlimited_sentinel ?? 1_000_000;
  const slotsLabel = fmtCount(slots, sentinel);
  const used = draft.custom_universe.length;
  const [input, setInput] = useState('');

  const canAdd = slots === 0 ? false : used < slots;

  const add = () => {
    const t = input.trim().toUpperCase();
    if (!t || draft.custom_universe.includes(t)) return;
    setDraft('custom_universe', [...draft.custom_universe, t]);
    setInput('');
  };

  const remove = (t: string) => {
    setDraft(
      'custom_universe',
      draft.custom_universe.filter((x) => x !== t),
    );
  };

  return (
    <div>
      <PaneHeader
        title="Custom Universe"
        description="Tickers to add to the shared daily pipeline. Globally deduped — if another user has the same ticker, it doesn't count twice in our cost."
      />
      {slots === 0 && (
        <div className="mb-3 px-3 py-2 rounded border border-amber-500/30 bg-amber-900/20 text-xs text-amber-200">
          🔒 Custom universe slots unlock on <strong>Pro</strong> tier (5 slots) and{' '}
          <strong>Premium</strong> (25). Upgrade to add tickers outside the curated 100-name universe.
        </div>
      )}

      <div className="text-xs text-text-secondary mb-2">
        Used <span className="font-mono text-text-primary">{used}</span> of{' '}
        <span className="font-mono text-text-primary">{slotsLabel}</span> slots.
      </div>

      <div className="flex gap-2 mb-3">
        <input
          type="text"
          placeholder="TICKER"
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          onKeyDown={(e) => {
            if (e.key === 'Enter') add();
          }}
          disabled={!canAdd}
          className="flex-1 bg-page border border-border rounded px-3 py-1.5 text-sm font-mono text-text-primary uppercase disabled:opacity-50"
        />
        <button
          onClick={add}
          disabled={!canAdd || !input.trim()}
          className="px-4 py-1.5 rounded text-xs font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        >
          Add
        </button>
      </div>

      {draft.custom_universe.length === 0 ? (
        <p className="text-xs text-text-secondary italic">No custom tickers yet.</p>
      ) : (
        <div className="flex flex-wrap gap-1.5">
          {draft.custom_universe.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded text-xs font-mono bg-accent-900/30 text-accent-300 border border-accent-500/30"
            >
              {t}
              <button
                onClick={() => remove(t)}
                className="text-accent-400 hover:text-white"
                title={`Remove ${t}`}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function DigestPane({
  draft,
  patchDigest,
}: {
  draft: UserPreferences;
  patchDigest: ReturnType<typeof usePreferences>['patchDigest'];
}) {
  const { data: ent } = useEntitlements();
  // Digest is part of the alerts feature surface — gated on alerts_enabled.
  const allowed = !!ent?.entitlements.alerts_enabled;
  const d = draft.digest_config;

  return (
    <div>
      <PaneHeader
        title="AM Digest"
        description="Single morning email with watchlist highlights, conviction breaches, and upcoming catalysts."
      />
      {!allowed && (
        <div className="mb-3 px-3 py-2 rounded border border-amber-500/30 bg-amber-900/20 text-xs text-amber-200">
          🔒 AM digest is part of the alerts feature — unlocks on <strong>Starter</strong> tier.
        </div>
      )}

      <label className="flex items-center gap-3 px-3 py-2.5 rounded border border-border cursor-pointer mb-3">
        <input
          type="checkbox"
          checked={d.enabled}
          onChange={(e) => patchDigest('enabled', e.target.checked)}
          className="accent-accent-500"
        />
        <div className="flex-1">
          <div className="text-sm font-semibold text-text-primary">Enable AM digest</div>
          <p className="text-[11px] text-text-secondary leading-snug">
            One email per day at the configured time. Skipped on weekends.
          </p>
        </div>
      </label>

      <div className="px-3 py-2.5 rounded border border-border space-y-2">
        <label className="flex items-center justify-between gap-3 text-sm">
          <span className="text-text-secondary">Send time (UTC)</span>
          <input
            type="time"
            value={d.send_time_utc}
            onChange={(e) => patchDigest('send_time_utc', e.target.value)}
            className="bg-page border border-border rounded px-2 py-1 text-sm font-mono text-text-primary"
          />
        </label>
        <p className="text-[11px] text-text-secondary -mt-1">
          UTC. 11:00 ≈ 7am EDT / 6am EST.
        </p>
      </div>

      <div className="mt-3 space-y-1.5">
        <div className="text-[11px] uppercase tracking-wider text-text-secondary mb-1">Sections to include</div>
        {[
          { key: 'include_top_picks',        label: 'Top picks (highest conviction)' },
          { key: 'include_position_health',  label: 'Open-position health (P&L + signal flips)' },
          { key: 'include_catalysts_3d',     label: 'Catalysts in next 3 days' },
          { key: 'include_industry_summary', label: 'Industry summary blurbs' },
        ].map((row) => (
          <label key={row.key} className="flex items-center gap-3 px-3 py-1.5 rounded border border-border cursor-pointer">
            <input
              type="checkbox"
              checked={d[row.key as keyof typeof d] as boolean}
              onChange={(e) => patchDigest(row.key as 'include_top_picks', e.target.checked as never)}
              className="accent-accent-500"
            />
            <span className="text-sm text-text-primary">{row.label}</span>
          </label>
        ))}
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function SettingsPage() {
  const [active, setActive] = useState<SectionKey>('profile');
  const p = usePreferences();
  const { catalog, draft, dirty, loading, saving, error, save, reset, setDraft, patchAlerts, patchDigest, patchSignalWeight, patchIndustryWeight } = p;

  const activeMeta = useMemo(() => SECTIONS.find((s) => s.key === active)!, [active]);

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-6xl mx-auto px-3 py-4 sm:px-4 sm:py-6">
        <h1 className="text-2xl font-bold mb-1">Settings</h1>
        <p className="text-sm text-text-secondary mb-5">
          Personalization. Save once — applies to your dashboard, recommendations, alerts, and the
          daily pipeline.
        </p>

        {error && <ErrorBox message={error} className="mb-4" />}

        {loading || !draft || !catalog ? (
          <LoadingRow label="Loading preferences…" py="py-12" size={4} />
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-[200px_1fr] gap-6">
            {/* Vertical tab nav */}
            <nav className="space-y-1">
              {SECTIONS.map((s) => (
                <button
                  key={s.key}
                  onClick={() => setActive(s.key)}
                  className={`w-full text-left px-3 py-2 rounded text-sm transition-colors ${
                    active === s.key
                      ? 'bg-accent-900/30 text-text-primary border-l-2 border-accent-500'
                      : 'text-text-secondary hover:text-text-primary hover:bg-border/30'
                  }`}
                >
                  {s.label}
                </button>
              ))}
            </nav>

            {/* Active pane */}
            <section
              className="bg-card border border-border rounded-lg p-5"
              title={activeMeta.description}
            >
              {active === 'profile' && <ProfilePane />}
              {active === 'risk' && (
                <RiskProfilePane draft={draft} setDraft={setDraft} catalog={catalog} />
              )}
              {active === 'signals' && (
                <SignalWeightsPane
                  draft={draft}
                  catalog={catalog}
                  patchSignalWeight={patchSignalWeight}
                />
              )}
              {active === 'industries' && (
                <IndustryWeightsPane
                  draft={draft}
                  catalog={catalog}
                  patchIndustryWeight={patchIndustryWeight}
                />
              )}
              {active === 'alerts' && (
                <AlertsPane draft={draft} patchAlerts={patchAlerts} catalog={catalog} />
              )}
              {active === 'universe' && <CustomUniversePane draft={draft} setDraft={setDraft} />}
              {active === 'digest' && <DigestPane draft={draft} patchDigest={patchDigest} />}
            </section>
          </div>
        )}

        {/* Sticky save bar */}
        {dirty && (
          <div className="sticky bottom-4 mt-6 bg-card border border-accent-500/40 rounded-lg p-3 flex items-center justify-between shadow-lg">
            <span className="text-sm text-text-primary">
              <span className="text-accent-300 font-semibold">Unsaved changes.</span> Review and save.
            </span>
            <div className="flex gap-2">
              <button
                onClick={reset}
                disabled={saving}
                className="px-4 py-1.5 rounded text-sm font-semibold text-text-secondary hover:text-text-primary hover:bg-border/40 transition-colors"
              >
                Discard
              </button>
              <button
                onClick={save}
                disabled={saving}
                className="px-4 py-1.5 rounded text-sm font-semibold bg-accent-600 hover:bg-accent-500 text-white disabled:opacity-50 transition-colors"
              >
                {saving ? 'Saving…' : 'Save changes'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
