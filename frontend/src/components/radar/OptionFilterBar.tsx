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
  'bg-card border border-border rounded px-2 py-1 text-xs text-text-primary focus:border-text-secondary w-full';

function Field({ label, hint, children }: { label: string; hint: string; children: ReactNode }) {
  return (
    <label className="block space-y-1" title={hint}>
      <span className="block text-[11px] text-text-secondary">{label}</span>
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
    <select aria-label={label} className={SELECT} value={value} onChange={(e) => onChange(Number(e.target.value))}>
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
  const btn = 'px-2.5 py-1.5 rounded text-xs border transition-colors';

  return (
    <div className="w-full">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-expanded={open}
          className={`${btn} ${n ? 'border-accent-500/50 bg-accent-500/10 text-accent-200' : 'border-border text-text-secondary hover:text-text-primary'}`}
        >
          ⚙ Call filters{n ? ` (${n})` : ''}
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
          className={`${btn} ${isCallPreset(value) ? 'border-green-500/50 bg-green-900/30 text-green-200' : 'border-border text-text-secondary hover:text-text-primary'}`}
        >
          ▲ Call-friendly
        </button>
        {n > 0 && (
          <button type="button" onClick={() => onChange(NO_FILTERS)} className="text-xs text-text-secondary hover:text-text-primary underline-offset-2 hover:underline">
            Clear
          </button>
        )}
      </div>
      {open && (
        <div className="mt-2 rounded border border-border bg-page/60 p-3 space-y-3">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Field label="Stock $ volume / day" hint="Typical dollar volume of the stock (20-day median)">
              <NumSelect label="Minimum stock dollar volume" value={value.minAdvM} choices={ADV_CHOICES} any="Any" fmt={(v) => `≥ $${v}M`} onChange={(v) => set('minAdvM', v)} />
            </Field>
            <Field label="Stock price" hint="Low-priced stocks have coarse strikes and wide option spreads">
              <NumSelect label="Minimum stock price" value={value.minPrice} choices={PRICE_CHOICES} any="Any" fmt={(v) => `≥ $${v}`} onChange={(v) => set('minPrice', v)} />
            </Field>
            <Field label="Call liquidity" hint="Grade of the near-the-money calls (open interest, volume, spread)">
              <select aria-label="Minimum call liquidity" className={SELECT} value={value.liquidity} onChange={(e) => set('liquidity', e.target.value as OptionFilters['liquidity'])}>
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
            <label className="flex items-center gap-2 text-xs text-text-primary self-end pb-1" title="Earnings on or before the expiry: IV usually collapses right after the report">
              <input type="checkbox" checked={value.noEarnings} onChange={(e) => set('noEarnings', e.target.checked)} />
              No earnings before expiry
            </label>
            <label className="flex items-center gap-2 text-xs text-text-primary self-end pb-1" title="Weekly expiries give finer timing choices">
              <input type="checkbox" checked={value.weekliesOnly} onChange={(e) => set('weekliesOnly', e.target.checked)} />
              Weekly options only
            </label>
          </div>
          <p className="text-[11px] text-text-secondary leading-snug">
            Option data are Yahoo quotes (delayed), refreshed after each scan for the stocks on the radar and warming up,
            using the first expiry at least 7 days out. Filters hide stocks without option data yet. Information for your
            own analysis, not a recommendation.
          </p>
        </div>
      )}
    </div>
  );
}
