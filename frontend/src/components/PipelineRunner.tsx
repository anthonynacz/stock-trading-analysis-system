import { useState, useEffect, useRef, useCallback } from 'react';
import type { PipelineRunStatus } from '../types';
import { startPipeline, getPipelineStatus } from '../utils/api';

const ALL_PHASES = [
  { key: 'discovery', label: 'Discovery' },
  { key: 'watchlist', label: 'Watchlist' },
  { key: 'ratings', label: 'Ratings' },
  { key: 'earnings', label: 'Earnings' },
  { key: 'news', label: 'News' },
  { key: 'options', label: 'Options' },
  { key: 'recommendations', label: 'Recs' },
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

  // ── Render: button with dropdown menu ───────────────────────────────────

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
        <div className="absolute right-0 top-full mt-1 bg-card border border-border rounded-lg shadow-lg z-50 py-1 min-w-[180px]">
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
