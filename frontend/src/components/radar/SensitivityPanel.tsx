import { useEffect, useState } from 'react';
import type { RadarSensitivity, RadarSettings } from '../../types';
import { getApiErrorMessage } from '../../utils/api';
import { applyRadarSettings, getRadarSettings } from './radarApi';

type Levels = Record<string, number>;

const same = (a: Levels, b: Levels) => Object.keys({ ...a, ...b }).every((k) => a[k] === b[k]);
const NOTCH_WORDS = ['Strictest', 'Stricter', 'Strict', 'Calibrated', 'Sensitive', 'More sensitive', 'Most sensitive'];
const EXIT_WORDS = ['Quickest', 'Quicker', 'Quick', 'Calibrated', 'Patient', 'More patient', 'Most patient'];
const CUTOFF_WORDS = ['Until 13:30', 'Until 14:00', 'Until 14:30', 'Until 15:00 (calibrated)', 'Until 15:20', 'Until 15:35', 'Until 15:45'];
const WORDS: Record<string, string[]> = { exit: EXIT_WORDS, cutoff: CUTOFF_WORDS };
const PREFIX: Record<string, string> = { exit: 'A stock on the radar ', cutoff: '' };

/**
 * Scan sensitivity: four 7-notch sliders (notch 3 = the replay-calibrated
 * setting). Apply saves them and queues a recompute of the session from the
 * open, so the lists refresh with the new settings within seconds; the
 * Scan now status line follows the recompute.
 */
export default function SensitivityPanel({
  applied,
  busy,
  onApplied,
}: {
  /** The sensitivity the current results were computed with (state.sensitivity). */
  applied: RadarSensitivity | undefined;
  /** A scan or recompute is pending or running. */
  busy: boolean;
  onApplied: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<RadarSettings | null>(null);
  const [draft, setDraft] = useState<Levels | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    let live = true;
    getRadarSettings()
      .then((s) => {
        if (!live) return;
        setSettings(s);
        setDraft((d) => d ?? { ...s.saved.levels });
      })
      .catch((e) => live && setError(getApiErrorMessage(e, 'Could not load the settings')));
    return () => {
      live = false;
    };
  }, [open]);

  const appliedLevels = applied?.levels;
  const tuned = applied ? !applied.calibrated : false;
  const saved = settings?.saved.levels;
  const changed = !!draft && !!saved && !same(draft, saved);
  const pendingApply = !!saved && !!appliedLevels && !same(saved, appliedLevels);

  const apply = async () => {
    if (!draft) return;
    setSaving(true);
    setError(null);
    try {
      const res = await applyRadarSettings(draft);
      setSettings((s) => (s ? { ...s, saved: res.saved } : s));
      onApplied();
    } catch (e) {
      setError(getApiErrorMessage(e, 'Could not apply the settings'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="w-full">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className={`px-3 py-1.5 rounded text-xs border transition-colors ${
          tuned ? 'border-amber-500/50 bg-amber-900/20 text-amber-200' : 'border-border text-text-secondary hover:text-text-primary'
        }`}
        title="Tune how easily stocks enter and leave the radar"
      >
        🎚 Scan sensitivity{tuned ? ' (tuned)' : ''}
      </button>
      {open && (
        <div className="mt-2 rounded-lg border border-border bg-card p-3 sm:p-4 space-y-4">
          {!settings || !draft ? (
            error ? <p className="text-sm text-red-300">{error}</p> : <p className="text-sm text-text-secondary">Loading…</p>
          ) : (
            <>
              {settings.dials.map((d) => {
                const v = draft[d.key] ?? settings.saved.default;
                const words = WORDS[d.key] ?? NOTCH_WORDS;
                const now = appliedLevels?.[d.key];
                return (
                  <div key={d.key} className="space-y-1">
                    <div className="flex items-baseline justify-between gap-2 flex-wrap">
                      <label htmlFor={`sens-${d.key}`} className="text-sm font-semibold text-text-primary" title={d.hint}>
                        {d.label}
                      </label>
                      <span className={`text-xs font-semibold ${v === settings.saved.default ? 'text-text-secondary' : 'text-amber-300'}`}>
                        {words[v]}
                        {now !== undefined && now !== v ? <span className="text-text-secondary font-normal"> (now: {words[now]})</span> : null}
                      </span>
                    </div>
                    <input
                      id={`sens-${d.key}`}
                      type="range"
                      min={0}
                      max={d.notches.length - 1}
                      step={1}
                      value={v}
                      list={`sens-${d.key}-ticks`}
                      onChange={(e) => setDraft((x) => ({ ...(x ?? {}), [d.key]: Number(e.target.value) }))}
                      className="w-full accent-emerald-500 cursor-pointer"
                      aria-valuetext={`${words[v]}: ${d.notches[v]?.text}`}
                    />
                    <datalist id={`sens-${d.key}-ticks`}>
                      {d.notches.map((n) => (
                        <option key={n.level} value={n.level} />
                      ))}
                    </datalist>
                    <div className="flex justify-between text-[10px] uppercase tracking-wider text-text-secondary">
                      <span>{d.left}</span>
                      <span>{d.right}</span>
                    </div>
                    <p className="text-xs text-text-secondary leading-snug">
                      <span className="text-text-primary">{PREFIX[d.key] ?? 'Enters with: '}</span>
                      {d.notches[v]?.text}
                    </p>
                  </div>
                );
              })}
              <div className="flex items-center gap-2 flex-wrap pt-1 border-t border-border/60">
                <button
                  type="button"
                  onClick={apply}
                  disabled={!changed || saving || busy}
                  className="px-3 py-1.5 rounded border border-accent-500/50 bg-accent-500/15 text-accent-200 text-xs font-semibold hover:bg-accent-500/25 disabled:opacity-50 disabled:cursor-not-allowed"
                  title={busy ? 'Wait for the running scan to finish' : 'Save and recompute the session with these settings'}
                >
                  {saving ? 'Applying…' : 'Apply & refresh'}
                </button>
                <button
                  type="button"
                  onClick={() => setDraft(Object.fromEntries(settings.dials.map((d) => [d.key, settings.saved.default])))}
                  className="px-3 py-1.5 rounded border border-border text-xs text-text-secondary hover:text-text-primary"
                >
                  Reset to calibrated
                </button>
                {changed && (
                  <button
                    type="button"
                    onClick={() => setDraft({ ...settings.saved.levels })}
                    className="text-xs text-text-secondary hover:text-text-primary"
                  >
                    Undo changes
                  </button>
                )}
                {error && <span className="text-xs text-red-300">{error}</span>}
                {pendingApply && !changed && <span className="text-xs text-amber-300">Saved; the recompute is on its way.</span>}
              </div>
              <p className="text-[11px] text-text-secondary leading-snug">
                Apply recomputes the whole session from the open with the new settings (about 10-30 s), so the lists, exits
                and history of the day change to what these settings would have shown; stocks are not alerted again. Outside
                market hours it recomputes the last session. Only the middle notch is calibrated on past sessions: more
                sensitive settings add stocks earlier but with more false starts.
                {settings.updated_at ? ` Last saved ${settings.updated_at.slice(0, 16).replace('T', ' ')} UTC.` : ''}
              </p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
