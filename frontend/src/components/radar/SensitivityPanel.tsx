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

/** Colour per notch: blue = stricter / quicker, grey = calibrated, amber to rose = more sensitive / patient. */
const LEVEL_TONE: { pill: string; text: string; hex: string }[] = [
  { pill: 'bg-sky-500/25 text-sky-200', text: 'text-sky-300', hex: '#7dd3fc' },
  { pill: 'bg-sky-500/20 text-sky-200', text: 'text-sky-300', hex: '#7dd3fc' },
  { pill: 'bg-sky-500/15 text-sky-200', text: 'text-sky-300', hex: '#7dd3fc' },
  { pill: 'bg-gray-500/25 text-gray-200', text: 'text-gray-300', hex: '#a1a1aa' },
  { pill: 'bg-amber-500/20 text-amber-200', text: 'text-amber-300', hex: '#fcd34d' },
  { pill: 'bg-orange-500/25 text-orange-200', text: 'text-orange-300', hex: '#fdba74' },
  { pill: 'bg-rose-500/25 text-rose-200', text: 'text-rose-300', hex: '#fda4af' },
];

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
        className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border transition-colors ${
          open || tuned
            ? 'border-violet-400/60 bg-violet-500/20 text-violet-100 hover:bg-violet-500/30'
            : 'border-violet-500/40 bg-violet-500/10 text-violet-200 hover:bg-violet-500/20'
        }`}
        title="Tune how easily stocks enter and leave the radar"
      >
        <span aria-hidden="true">🎚</span> Scan sensitivity
        {tuned && <span className="ml-0.5 rounded-full bg-amber-400/90 px-1.5 text-[9px] font-bold uppercase text-gray-900">tuned</span>}
        <span aria-hidden="true" className={`text-[9px] transition-transform ${open ? 'rotate-180' : ''}`}>▼</span>
      </button>
      {open && (
        <div className="mt-2 rounded-lg border border-violet-500/30 border-l-4 border-l-violet-400 bg-violet-950/20 px-3 py-2.5 space-y-2.5">
          {!settings || !draft ? (
            error ? <p className="text-xs text-red-300">{error}</p> : <p className="text-xs text-text-secondary">Loading…</p>
          ) : (
            <>
              <div className="grid gap-x-5 gap-y-2.5 sm:grid-cols-2 xl:grid-cols-3">
                {settings.dials.map((d) => {
                  const v = draft[d.key] ?? settings.saved.default;
                  const words = WORDS[d.key] ?? NOTCH_WORDS;
                  const now = appliedLevels?.[d.key];
                  const tone = LEVEL_TONE[v] ?? LEVEL_TONE[3];
                  const pct = (v / (d.notches.length - 1)) * 100;
                  return (
                    <div key={d.key} className="min-w-0" title={`${d.hint}\n${PREFIX[d.key] ?? 'Enters with: '}${d.notches[v]?.text ?? ''}`}>
                      <div className="flex items-center justify-between gap-2">
                        <label htmlFor={`sens-${d.key}`} className="text-[11px] font-semibold text-violet-100 truncate">
                          {d.label}
                        </label>
                        <span className={`shrink-0 rounded px-1.5 py-px text-[10px] font-bold ${tone.pill}`}>
                          {words[v]}
                          {now !== undefined && now !== v ? <span className="font-normal opacity-80"> · now {words[now]}</span> : null}
                        </span>
                      </div>
                      <input
                        id={`sens-${d.key}`}
                        type="range"
                        min={0}
                        max={d.notches.length - 1}
                        step={1}
                        value={v}
                        onChange={(e) => setDraft((x) => ({ ...(x ?? {}), [d.key]: Number(e.target.value) }))}
                        className={`range-sm mt-1 ${tone.text}`}
                        style={{ background: `linear-gradient(to right, ${tone.hex} ${pct}%, rgba(139,148,158,0.25) ${pct}%)` }}
                        aria-valuetext={`${words[v]}: ${d.notches[v]?.text}`}
                      />
                      <div className="flex justify-between text-[9px] uppercase tracking-wide text-text-secondary/80">
                        <span>{d.left}</span>
                        <span>{d.right}</span>
                      </div>
                      <p className="text-[10.5px] text-text-secondary leading-tight truncate">{d.notches[v]?.text}</p>
                    </div>
                  );
                })}
              </div>
              <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-violet-500/20">
                <button
                  type="button"
                  onClick={apply}
                  disabled={!changed || saving || busy}
                  className="px-3 py-1 rounded-md bg-violet-500 text-white text-xs font-semibold hover:bg-violet-400 disabled:opacity-40 disabled:cursor-not-allowed"
                  title={busy ? 'Wait for the running scan to finish' : 'Save and recompute the session with these settings'}
                >
                  {saving ? 'Applying…' : 'Apply & refresh'}
                </button>
                <button
                  type="button"
                  onClick={() => setDraft(Object.fromEntries(settings.dials.map((d) => [d.key, settings.saved.default])))}
                  className="px-2.5 py-1 rounded-md border border-violet-500/40 text-xs text-violet-200 hover:bg-violet-500/15"
                >
                  Reset to calibrated
                </button>
                {changed && (
                  <button type="button" onClick={() => setDraft({ ...settings.saved.levels })} className="text-xs text-violet-300 hover:text-violet-100">
                    Undo
                  </button>
                )}
                {error && <span className="text-xs text-red-300">{error}</span>}
                {pendingApply && !changed && <span className="text-xs text-amber-300">Saved; recomputing…</span>}
                <span
                  className="ml-auto text-[10.5px] text-text-secondary cursor-help"
                  title={
                    'Apply recomputes the whole session from the open with the new settings (about 10-30 s): the lists, ' +
                    'exits and history of the day change to what these settings would have shown; stocks are not alerted ' +
                    'again. Outside market hours it recomputes the last session. Only the calibrated (middle) notch is ' +
                    'backtested: more sensitive settings add stocks earlier but with more false starts.'
                  }
                >
                  Recomputes the day · only Calibrated is backtested ⓘ
                  {settings.updated_at ? ` · saved ${settings.updated_at.slice(5, 16).replace('T', ' ')} UTC` : ''}
                </span>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
