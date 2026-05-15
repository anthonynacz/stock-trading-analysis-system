import { useState, useCallback } from 'react';
import { NavLink } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import { fmtCount, useEntitlements } from '../contexts/EntitlementsContext';

const links = [
  { to: '/', label: 'Dashboard' },
  { to: '/universe', label: 'Universe' },
  { to: '/industries', label: 'Industries' },
  { to: '/research', label: 'Research' },
  { to: '/options-lab', label: 'Options Lab' },
  { to: '/scanner', label: 'Scanner' },
  { to: '/charts', label: 'Charts' },
  { to: '/positions', label: 'Positions' },
  { to: '/schedule', label: 'Schedule' },
  { to: '/knowledge', label: 'Knowledge' },
] as const;

const TIER_STYLES: Record<string, { bg: string; text: string; border: string }> = {
  FREE:    { bg: 'bg-gray-900/60',    text: 'text-text-secondary', border: 'border-gray-700/60' },
  STARTER: { bg: 'bg-cyan-900/40',    text: 'text-cyan-300',       border: 'border-cyan-500/30' },
  PRO:     { bg: 'bg-emerald-900/40', text: 'text-emerald-300',    border: 'border-emerald-500/30' },
  PREMIUM: { bg: 'bg-violet-900/40',  text: 'text-violet-300',     border: 'border-violet-500/30' },
  ADMIN:   { bg: 'bg-amber-900/40',   text: 'text-amber-300',      border: 'border-amber-500/30' },
};

function CreditBadge() {
  const { data, loading } = useEntitlements();
  if (loading || !data) return null;
  const research = data.credits.research;
  const balLabel = fmtCount(research.balance, data.unlimited_sentinel);
  const quotaLabel = fmtCount(research.monthly_quota, data.unlimited_sentinel);
  return (
    <span
      className="text-[10px] font-mono tabular-nums px-1.5 py-px rounded bg-page/60 text-text-secondary border border-border"
      title={`Research credits this period: ${balLabel} of ${quotaLabel}.\nDeep options: ${fmtCount(data.credits.deep_options.balance, data.unlimited_sentinel)} / ${fmtCount(data.credits.deep_options.monthly_quota, data.unlimited_sentinel)}.\nScanner: ${fmtCount(data.credits.scanner.balance, data.unlimited_sentinel)} / ${fmtCount(data.credits.scanner.monthly_quota, data.unlimited_sentinel)}.`}
    >
      🔬 {balLabel}/{quotaLabel}
    </span>
  );
}

function UserBadge() {
  const { user, token, logout } = useAuth();
  const { data: ent } = useEntitlements();
  if (!user) return null;
  // Only show the LEGACY chip when the backend literally routed this request
  // through the legacy admin row — not just because the LEGACY_MODE env flag
  // is on. With JWT-preferred middleware, a real signed-in user has
  // provider != 'legacy' even while LEGACY_MODE remains on for anonymous users.
  const isLegacySession = user.provider === 'legacy';

  const initials = user.email.charAt(0).toUpperCase();
  const tier = ent?.tier ?? user.role;
  const tierStyle = TIER_STYLES[tier] ?? TIER_STYLES.FREE;
  const tierLabel = ent?.tier_label ?? user.role;

  return (
    <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
      {/* LEGACY / credit / tier chips: hidden on phones, shown sm+ to keep nav
          row narrow on a 360px viewport. Same info is still on the Settings page. */}
      <div className="hidden sm:flex items-center gap-2">
        {isLegacySession && (
          <span
            className="text-[9px] font-bold tracking-wider uppercase px-1.5 py-px rounded bg-amber-900/40 text-amber-300 border border-amber-500/30"
            title="You are signed in as the legacy admin (auth bypass). Click Sign in to switch."
          >
            Legacy
          </span>
        )}
        <CreditBadge />
        <span
          className={`text-[9px] font-bold tracking-wider uppercase px-1.5 py-px rounded ${tierStyle.bg} ${tierStyle.text} border ${tierStyle.border}`}
          title={`Tier: ${tierLabel}`}
        >
          {tierLabel}
        </span>
      </div>
      <span
        className="w-6 h-6 rounded-full bg-accent-700/60 text-white text-[11px] font-semibold flex items-center justify-center shrink-0"
        title={user.email}
      >
        {initials}
      </span>
      <span className="text-xs text-text-secondary hidden md:inline">{user.email}</span>
      <NavLink
        to="/settings"
        title="Settings"
        className={({ isActive }) =>
          `p-1 rounded transition-colors ${
            isActive
              ? 'text-text-primary bg-border/60'
              : 'text-text-secondary hover:text-text-primary hover:bg-border/60'
          }`
        }
      >
        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.8}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M10.325 4.317a1 1 0 011.35 0l1.184 1.184a1 1 0 00.943.243l1.612-.43a1 1 0 011.21.69l.515 1.547a1 1 0 00.69.69l1.547.515a1 1 0 01.69 1.21l-.43 1.612a1 1 0 00.243.943l1.184 1.184a1 1 0 010 1.414l-1.184 1.184a1 1 0 00-.243.943l.43 1.612a1 1 0 01-.69 1.21l-1.547.515a1 1 0 00-.69.69l-.515 1.547a1 1 0 01-1.21.69l-1.612-.43a1 1 0 00-.943.243l-1.184 1.184a1 1 0 01-1.414 0l-1.184-1.184a1 1 0 00-.943-.243l-1.612.43a1 1 0 01-1.21-.69l-.515-1.547a1 1 0 00-.69-.69l-1.547-.515a1 1 0 01-.69-1.21l.43-1.612a1 1 0 00-.243-.943L3.317 13.05a1 1 0 010-1.414l1.184-1.184a1 1 0 00.243-.943l-.43-1.612a1 1 0 01.69-1.21l1.547-.515a1 1 0 00.69-.69l.515-1.547a1 1 0 011.21-.69l1.612.43a1 1 0 00.943-.243l1.184-1.184z" />
          <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
        </svg>
      </NavLink>
      {token ? (
        <button
          onClick={logout}
          title="Sign out (returns to legacy admin in dev)"
          className="text-[11px] text-text-secondary hover:text-text-primary px-1.5 sm:px-2 py-1 rounded hover:bg-border/60 transition-colors"
        >
          Sign out
        </button>
      ) : (
        <NavLink
          to="/login"
          title="Sign in as a real user (legacy mode is on by default)"
          className="text-[11px] font-semibold text-accent-300 hover:text-accent-200 px-1.5 sm:px-2 py-1 rounded hover:bg-accent-900/30 transition-colors"
        >
          Sign in
        </NavLink>
      )}
    </div>
  );
}

export default function AppNav() {
  const [open, setOpen] = useState(false);
  const close = useCallback(() => setOpen(false), []);

  return (
    <nav className="bg-card border-b border-border">
      <div className="max-w-7xl mx-auto flex items-center h-10 gap-3 md:gap-6 px-3 sm:px-4">
        {/* Hamburger toggle — phones only */}
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-label={open ? 'Close navigation menu' : 'Open navigation menu'}
          aria-expanded={open}
          className="md:hidden p-1 -ml-1 text-text-secondary hover:text-text-primary rounded transition-colors"
        >
          <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            {open ? (
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            ) : (
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
            )}
          </svg>
        </button>

        <span className="text-sm font-bold text-text-primary tracking-wide">
          Vela
        </span>

        {/* Horizontal links — md (768px) and up */}
        <div className="hidden md:flex items-center gap-6">
          {links.map((l) => (
            <NavLink
              key={l.to}
              to={l.to}
              end={l.to === '/'}
              className={({ isActive }) =>
                `text-sm transition-colors ${
                  isActive
                    ? 'text-text-primary font-semibold border-b-2 border-accent-500'
                    : 'text-text-secondary hover:text-text-primary'
                }`
              }
            >
              {l.label}
            </NavLink>
          ))}
        </div>

        <UserBadge />
      </div>

      {/* Mobile dropdown — only rendered when open, below the bar */}
      {open && (
        <div className="md:hidden border-t border-border bg-card shadow-lg">
          <div className="flex flex-col py-1">
            {links.map((l) => (
              <NavLink
                key={l.to}
                to={l.to}
                end={l.to === '/'}
                onClick={close}
                className={({ isActive }) =>
                  `px-4 py-3 text-sm transition-colors min-h-[44px] flex items-center ${
                    isActive
                      ? 'text-text-primary font-semibold bg-border/40 border-l-2 border-accent-500'
                      : 'text-text-secondary hover:text-text-primary hover:bg-border/30 border-l-2 border-transparent'
                  }`
                }
              >
                {l.label}
              </NavLink>
            ))}
          </div>
        </div>
      )}
    </nav>
  );
}
