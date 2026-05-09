import { useState, useEffect, useRef, useCallback } from 'react';
import type { PipelineRunStatus } from '../types';
import { startPipeline, getPipelineStatus } from '../utils/api';
import { useEntitlements } from '../contexts/EntitlementsContext';

const ALL_PHASES = [
  { key: 'discovery', label: 'Discovery' },
  { key: 'watchlist', label: 'Watchlist' },
  { key: 'ratings', label: 'Ratings' },
  { key: 'earnings', label: 'Earnings' },
  { key: 'news', label: 'News' },
  { key: 'options', label: 'Options' },
  { key: 'recommendations', label: 'Recs' },
  { key: 'industries', label: 'Industries' },
];

// Curated quick-run presets — selective phase sets for common workflows.
// Full Pipeline is the morning-run preset: every phase, fresh universe
// rotation, full data refresh. Intraday Update is the same-day re-run
// preset: skips slow / once-a-day phases (discovery, watchlist rotation,
// earnings calendar, analyst ratings) and re-runs only price-sensitive
// ones — uses ~half the API quota and populates the REV badge / prior
// values on each existing same-day row.
const PRESETS: {
  key: string;
  label: string;
  description: string;
  phases: string[];
  iconPath: string;
  iconColor: string;
}[] = [
  {
    key: 'full',
    label: 'Full Pipeline',
    description: 'All 8 phases — fresh universe + full refresh',
    phases: [
      'discovery', 'watchlist', 'ratings', 'earnings',
      'news', 'options', 'recommendations', 'industries',
    ],
    iconPath: 'M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15',
    iconColor: 'text-green-400',
  },
  {
    key: 'intraday',
    label: 'Intraday Update',
    description: 'News + Options + Recs + Industries (skip slow phases)',
    phases: ['news', 'options', 'recommendations', 'industries'],
    iconPath: 'M13 10V3L4 14h7v7l9-11h-7z',
    iconColor: 'text-purple-400',
  },
];

const PHASE_COLORS: Record<string, string> = {
  idle: '#8b949e',
  pending: '#21262d',
  running: '#58a6ff',
  done: '#2ea043',
  failed: '#f85149',
};

interface Props {
  onComplete: () => void;
}

export default function PipelineRunner({ onComplete }: Props) {
  const [showMenu, setShowMenu] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [run, setRun] = useState<PipelineRunStatus | null>(null);
  const [starting, setStarting] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const completedRef = useRef(false);

  // Tier gate. Three render paths:
  //   - Empty allowlist → locked variant (FREE/STARTER)
  //   - Subset (e.g. PRO's intraday-only) → simplified single-button refresh
  //   - Full 8 phases → full UI with presets + custom phase picker (PREMIUM/ADMIN)
  const { data: ent } = useEntitlements();
  const canTrigger = ent?.entitlements.manual_pipeline_trigger ?? false;
  const allowedPhases = ent?.entitlements.allowed_pipeline_phases ?? [];
  const allowedSet = new Set(allowedPhases);
  const hasFullPipelineAccess = allowedPhases.length >= ALL_PHASES.length;

  const isRunning = run?.status === 'running';
  const isDone = run?.status === 'completed' || run?.status === 'failed';

  // Close menu on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setShowMenu(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  // Rehydrate on mount — if the backend has a pipeline running (e.g. user
  // triggered it, navigated to another page, then came back), pick up the
  // in-flight state so the progress indicator reappears.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const status = await getPipelineStatus();
        if (!cancelled && status.status === 'running') {
          setRun(status);
        }
      } catch {
        // ignore — no backend means no rehydration
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Poll pipeline status while running
  useEffect(() => {
    if (!isRunning && !starting) return;

    const poll = async () => {
      try {
        const status = await getPipelineStatus();
        setRun(status);

        if (status.status === 'completed' || status.status === 'failed') {
          if (pollRef.current) {
            clearInterval(pollRef.current);
            pollRef.current = null;
          }
          if (status.status === 'completed' && !completedRef.current) {
            completedRef.current = true;
            // Brief delay so the user sees 100% before refresh
            setTimeout(() => {
              onComplete();
              // Reset after refresh fires
              setTimeout(() => {
                setRun(null);
                completedRef.current = false;
              }, 500);
            }, 800);
          }
        }
      } catch {
        // ignore polling errors
      }
    };

    poll();
    pollRef.current = setInterval(poll, 1500);
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    };
  }, [isRunning, starting, onComplete]);

  const handleStart = useCallback(async (phases?: string[]) => {
    setStarting(true);
    setShowMenu(false);
    completedRef.current = false;
    try {
      await startPipeline(phases);
      // Immediately start polling
      const status = await getPipelineStatus();
      setRun(status);
    } catch (e: unknown) {
      const detail = e && typeof e === 'object' && 'response' in e
        ? (e as { response: { data: { detail: string } } }).response?.data?.detail
        : 'Failed to start pipeline';
      alert(detail);
    } finally {
      setStarting(false);
    }
  }, []);

  const handleRunSelected = useCallback(() => {
    if (selected.size === 0) return;
    // Sort by canonical order
    const ordered = ALL_PHASES.filter((p) => selected.has(p.key)).map((p) => p.key);
    handleStart(ordered);
    setSelected(new Set());
  }, [selected, handleStart]);

  const togglePhase = (key: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // ── Render: progress bar (while running or just completed) ──────────────

  if (isRunning || isDone || starting) {
    const phases = run?.phases ?? ALL_PHASES.map((p) => p.key);
    const completedSet = new Set(run?.completed ?? []);
    const current = run?.current;
    // current can be "ratings+earnings+news+options" for parallel batches
    const currentSet = new Set(current?.split('+') ?? []);
    const isParallel = currentSet.size > 1;
    const pct = phases.length > 0
      ? Math.round((completedSet.size / phases.length) * 100)
      : 0;

    // Build a display label for the current phase(s)
    const currentLabel = isParallel
      ? `${currentSet.size} phases`
      : current
        ? ALL_PHASES.find((p) => p.key === current)?.label ?? current
        : null;

    return (
      <div className="flex items-center gap-3">
        {/* Phase dots */}
        <div className="flex items-center gap-1">
          {phases.map((phase) => {
            const info = ALL_PHASES.find((p) => p.key === phase);
            const label = info?.label ?? phase;
            let color: string;
            let animate = false;

            if (completedSet.has(phase)) {
              color = PHASE_COLORS.done;
            } else if (currentSet.has(phase)) {
              color = PHASE_COLORS.running;
              animate = true;
            } else {
              color = PHASE_COLORS.pending;
            }

            return (
              <div
                key={phase}
                className="relative group"
              >
                <div
                  className={`w-2.5 h-2.5 rounded-full transition-colors ${animate ? 'animate-pulse' : ''}`}
                  style={{ backgroundColor: color }}
                />
                {/* Tooltip */}
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-1 hidden group-hover:block z-50">
                  <div className="bg-gray-900 text-white text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap">
                    {label}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {/* Progress bar */}
        <div className="w-24 h-1.5 bg-border rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${isDone ? 100 : pct}%`,
              backgroundColor: run?.status === 'failed' ? PHASE_COLORS.failed : PHASE_COLORS.done,
            }}
          />
        </div>

        {/* Label */}
        <span className="text-[11px] text-text-secondary whitespace-nowrap">
          {run?.status === 'failed'
            ? 'Failed'
            : isDone
              ? 'Done'
              : currentLabel
                ? `${currentLabel}...`
                : 'Starting...'}
        </span>
      </div>
    );
  }

  // ── Render: tier-locked variant ─────────────────────────────────────────

  if (!canTrigger) {
    return (
      <div
        className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-border/50 text-text-secondary text-xs font-medium cursor-not-allowed"
        title="Manual pipeline triggers are a Pro feature. The shared pipeline auto-runs daily; data updates for you when it does."
      >
        <svg
          className="w-3.5 h-3.5 opacity-60"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m0 0v2m0-2h2m-2 0h-2m9-9V7a4 4 0 00-8 0v3m12 0H4a2 2 0 00-2 2v6a2 2 0 002 2h16a2 2 0 002-2v-6a2 2 0 00-2-2z" />
        </svg>
        <span>Refresh</span>
        <span className="text-[9px] font-bold uppercase tracking-wider px-1 py-px rounded bg-amber-900/40 text-amber-300 border border-amber-500/30">
          Pro+
        </span>
      </div>
    );
  }

  // ── Render: intraday-only variant (PRO) ─────────────────────────────────
  // Single button that runs the user's full allowed phase set (which for PRO
  // is the intraday subset). No phase picker — users at this tier shouldn't
  // need to think about which subset; the once-a-day expensive phases are
  // off-limits and handled by the cron.

  if (!hasFullPipelineAccess) {
    const phaseLabels = ALL_PHASES
      .filter((p) => allowedSet.has(p.key))
      .map((p) => p.label)
      .join(' · ');
    return (
      <button
        onClick={() => handleStart(allowedPhases)}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-border text-text-primary text-xs font-medium hover:bg-text-secondary/20 transition-colors"
        title={`Refresh runs the intraday subset for your tier: ${phaseLabels}. Once-a-day phases (discovery, watchlist, ratings, earnings) are handled by the daily cron.`}
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M13 10V3L4 14h7v7l9-11h-7z"
          />
        </svg>
        <span>Refresh</span>
        <span className="text-[9px] font-bold tracking-wider uppercase px-1 py-px rounded bg-purple-500/20 text-purple-300">
          Intraday
        </span>
      </button>
    );
  }

  // ── Render: full-pipeline variant (PREMIUM / ADMIN) ─────────────────────

  return (
    <div className="relative" ref={menuRef}>
      <div className="flex items-center">
        {/* Main button */}
        <button
          onClick={() => handleStart()}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-l bg-border text-text-primary text-xs font-medium hover:bg-text-secondary/20 transition-colors"
          title="Run full pipeline"
        >
          <svg
            className="w-3.5 h-3.5"
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
            />
          </svg>
          Refresh
        </button>

        {/* Dropdown arrow */}
        <button
          onClick={() => setShowMenu((v) => !v)}
          className="px-1.5 py-1.5 rounded-r bg-border text-text-primary text-xs hover:bg-text-secondary/20 transition-colors border-l border-background"
          title="Choose phases"
        >
          <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
          </svg>
        </button>
      </div>

      {/* Phase picker dropdown */}
      {showMenu && (
        <div className="absolute right-0 top-full mt-1 bg-card border border-border rounded-lg shadow-lg z-50 py-1 min-w-[230px]">
          {/* Quick presets */}
          <div className="px-3 pt-1 pb-1 text-[9px] uppercase tracking-wider text-text-secondary">
            Quick run
          </div>
          {PRESETS.map((preset) => (
            <button
              key={preset.key}
              onClick={() => handleStart(preset.phases)}
              className="w-full text-left px-3 py-1.5 hover:bg-border/40 transition-colors group"
              title={preset.description}
            >
              <div className="flex items-center gap-2 text-xs text-text-primary">
                <svg className={`w-3 h-3 ${preset.iconColor}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d={preset.iconPath} />
                </svg>
                <span className="font-medium">{preset.label}</span>
              </div>
              <div className="text-[10px] text-text-secondary pl-5 mt-0.5">
                {preset.description}
              </div>
            </button>
          ))}

          {/* Individual phases */}
          <div className="border-t border-border mt-1 px-3 pt-1 pb-1 text-[9px] uppercase tracking-wider text-text-secondary">
            Custom phases
          </div>
          {ALL_PHASES.map((phase) => (
            <label
              key={phase.key}
              className="flex items-center gap-2 px-3 py-1.5 hover:bg-border/40 cursor-pointer text-xs text-text-primary"
            >
              <input
                type="checkbox"
                checked={selected.has(phase.key)}
                onChange={() => togglePhase(phase.key)}
                className="accent-purple-500"
              />
              {phase.label}
            </label>
          ))}
          <div className="border-t border-border mt-1 pt-1 px-3 pb-1">
            <button
              onClick={handleRunSelected}
              disabled={selected.size === 0}
              className="w-full text-xs py-1 rounded bg-purple-500/20 text-purple-400 hover:bg-purple-500/30 font-medium disabled:opacity-40 transition-colors"
            >
              Run {selected.size > 0 ? `${selected.size} phase${selected.size > 1 ? 's' : ''}` : 'selected'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
