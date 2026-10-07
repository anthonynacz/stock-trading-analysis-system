import { useState, type ReactNode } from 'react';
import {
  ADV_CHOICES,
  CALL_PRESET,
  IVHV_CHOICES,
  NO_FILTERS,
  OI_CHOICES,
  PRICE_CHOICES,
  SPREAD_CHOICES,
  activeCount,
  isCallPreset,
  type OptionFilters,
} from './optionFilters';

const SELECT =
  'bg-card border rounded px-2 py-1 text-xs text-text-primary focus:border-sky-300 w-full transition-colors';
/** Set filters stand out (sky ring); unset ones stay quiet. */
const selectClass = (active: boolean) =>
  `${SELECT} ${active ? 'border-sky-400/80 bg-sky-500/10 text-sky-100 font-semibold' : 'border-sky-500/25'}`;

function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <label className="block space-y-1" title={hint}>
      <span className="block text-[11px] font-medium text-sky-200/90">{label}</span>
      {children}
    </label>
  );
}

function NumSelect({
  value,
  choices,
  any,
  fmt,
  onChange,
  label,
}: {
  value: number;
  choices: number[];
  any: string;
  fmt: (v: number) => string;
  onChange: (v: number) => void;
  label: string;
}) {
  const opts = choices.includes(value) ? choices : [...choices, value].sort((a, b) => a - b);
  return (
    <select aria-label={label} className={selectClass(value !== 0)} value={value} onChange={(e) => onChange(Number(e.target.value))}>
      {opts.map((c) => (
        <option key={c} value={c}>
          {c === 0 ? any : fmt(c)}
        </option>
      ))}
    </select>
  );
}

/**
 * "Call filters" toggle and panel: stock liquidity, option liquidity, option
 * cost and event risk. `onCallPreset` also switches the direction to Up.
 */
export default function OptionFilterBar({
  value,
  onChange,
  onCallPreset,
}: {
  value: OptionFilters;
  onChange: (f: OptionFilters) => void;
  onCallPreset: () => void;
}) {
  const [open, setOpen] = useState(false);
  const n = activeCount(value);
  const set = <K extends keyof OptionFilters>(k: K, v: OptionFilters[K]) => onChange({ ...value, [k]: v });
  const btn = 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-semibold border transition-colors';

  return (
    <div className="w-full">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className={`${btn} ${
            n || open
              ? 'border-sky-400/60 bg-sky-500/20 text-sky-100 hover:bg-sky-500/30'
              : 'border-sky-500/40 bg-sky-500/10 text-sky-200 hover:bg-sky-500/20'
          }`}
        >
          <span aria-hidden="true">⚙</span> Call filters
          {n > 0 && <span className="rounded-full bg-sky-400 px-1.5 text-[10px] font-bold text-gray-900">{n}</span>}
          <span aria-hidden="true" className={`text-[9px] transition-transform ${open ? 'rotate-180' : ''}`}>▼</span>
        </button>
        <button
          type="button"
          onClick={() => {
            if (isCallPreset(value)) onChange(NO_FILTERS);
            else {
              onChange(CALL_PRESET);
              onCallPreset();
            }
          }}
          aria-pressed={isCallPreset(value)}
          title="Up movers with a liquid stock, tradable calls, a premium not inflated and no earnings before expiry"
          className={`${btn} ${
            isCallPreset(value)
              ? 'border-emerald-400 bg-emerald-500 text-gray-900 hover:bg-emerald-400'
              : 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200 hover:bg-emerald-500/20'
          }`}
        >
          ▲ Call-friendly
        </button>
        {n > 0 && (
          <button type="button" onClick={() => onChange(NO_FILTERS)} className="text-xs text-sky-300 hover:text-sky-100 underline-offset-2 hover:underline">
            Clear
          </button>
        )}
      </div>
      {open && (
        <div className="mt-2 rounded-lg border border-sky-500/30 border-l-4 border-l-sky-400 bg-sky-950/20 px-3 py-2.5 space-y-2.5">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-3 gap-y-2">
            <Field label="Stock $ volume / day" hint="Typical dollar volume of the stock (20-day median)">
              <NumSelect label="Minimum stock dollar volume" value={value.minAdvM} choices={ADV_CHOICES} any="Any" fmt={(v) => `≥ $${v}M`} onChange={(v) => set('minAdvM', v)} />
            </Field>
            <Field label="Stock price" hint="Low-priced stocks have coarse strikes and wide option spreads">
              <NumSelect label="Minimum stock price" value={value.minPrice} choices={PRICE_CHOICES} any="Any" fmt={(v) => `≥ $${v}`} onChange={(v) => set('minPrice', v)} />
            </Field>
            <Field label="Call liquidity" hint="Grade of the near-the-money calls (open interest, volume, spread)">
              <select aria-label="Minimum call liquidity" className={selectClass(value.liquidity !== 'any')} value={value.liquidity} onChange={(e) => set('liquidity', e.target.value as OptionFilters['liquidity'])}>
                <option value="any">Any</option>
                <option value="fair">Tradable or better</option>
                <option value="good">Liquid only</option>
              </select>
            </Field>
            <Field label="Call open interest" hint="Open interest of calls with strikes within ±10% of the price">
              <NumSelect label="Minimum call open interest" value={value.minNtmOi} choices={OI_CHOICES} any="Any" fmt={(v) => `≥ ${v.toLocaleString('en-US')}`} onChange={(v) => set('minNtmOi', v)} />
            </Field>
            <Field label="Call bid-ask spread" hint="At-the-money call spread in % of mid: what a round trip can cost">
              <NumSelect label="Maximum call spread" value={value.maxSpreadPct} choices={SPREAD_CHOICES} any="Any" fmt={(v) => `≤ ${v}%`} onChange={(v) => set('maxSpreadPct', v)} />
            </Field>
            <Field label="IV vs realised vol" hint="Skip calls priced for a much bigger move than the stock usually makes">
              <NumSelect label="Maximum IV to realised volatility" value={value.maxIvHv} choices={IVHV_CHOICES} any="Any" fmt={(v) => `≤ ${v}×`} onChange={(v) => set('maxIvHv', v)} />
            </Field>
            <label
              className={`flex items-center gap-2 text-xs self-end pb-1 ${value.noEarnings ? 'text-sky-100 font-semibold' : 'text-text-primary'}`}
              title="Earnings on or before the expiry: IV usually collapses right after the report"
            >
              <input type="checkbox" className="accent-sky-400" checked={value.noEarnings} onChange={(e) => set('noEarnings', e.target.checked)} />
              No earnings before expiry
            </label>
            <label
              className={`flex items-center gap-2 text-xs self-end pb-1 ${value.weekliesOnly ? 'text-sky-100 font-semibold' : 'text-text-primary'}`}
              title="Weekly expiries give finer timing choices"
            >
              <input type="checkbox" className="accent-sky-400" checked={value.weekliesOnly} onChange={(e) => set('weekliesOnly', e.target.checked)} />
              Weekly options only
            </label>
          </div>
          <p className="text-[10.5px] text-text-secondary leading-snug">
            Option data are Yahoo quotes (delayed), refreshed after each scan for the stocks on the radar and warming up,
            using the first expiry at least 7 days out. Filters hide stocks without option data yet. Information for your
            own analysis, not a recommendation.
          </p>
        </div>
      )}
    </div>
  );
}
