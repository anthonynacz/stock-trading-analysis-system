import type { RadarOptionMetrics } from '../../types';
import { fmtStrike } from '../../utils/format';
import { LIQ_CLASS, LIQ_HINT, LIQ_TEXT, fmtCompact, fmtShortDate, fmtUsdCompact } from './optionFilters';

const n1 = (v: number | null | undefined, suffix = '') =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(1)}${suffix}`;

/** Compact one-line summary: liquidity pill, expiry, call OI, spread, IV, earnings flag. */
export function OptionChip({ o }: { o: RadarOptionMetrics | undefined }) {
  if (!o) return <span className="text-[11px] text-text-secondary/70">Option data after the next scan</span>;
  const earn = o.earnings_before_expiry;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-text-secondary">
      <span className={`px-1.5 py-px rounded border font-semibold cursor-help ${LIQ_CLASS[o.liquidity]}`} title={LIQ_HINT[o.liquidity]}>
        {LIQ_TEXT[o.liquidity]}
      </span>
      {o.has_options && o.expiry && (
        <>
          <span className="tabular-nums" title="First expiry at least 7 days out">
            {fmtShortDate(o.expiry)} ({o.dte}d)
          </span>
          <span className="tabular-nums" title="Open interest of calls within ±10% of the price">
            OI {fmtCompact(o.ntm_call_oi)}
          </span>
          <span className="tabular-nums" title="At-the-money call bid-ask spread, % of mid">
            spread {n1(o.atm?.spread_pct, '%')}
          </span>
          <span className="tabular-nums" title="At-the-money call implied volatility">
            IV {n1(o.iv_pct, '%')}
          </span>
        </>
      )}
      {earn && (
        <span
          className="px-1.5 py-px rounded border border-red-500/40 bg-red-900/30 text-red-300 font-semibold cursor-help"
          title="Earnings on or before this expiry: option prices usually drop sharply right after the report (IV crush)."
        >
          Earnings {fmtShortDate(o.earnings_date)}
        </span>
      )}
    </span>
  );
}

function Fact({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div title={hint} className={hint ? 'cursor-help' : undefined}>
      <dt className="inline text-text-secondary">{label} </dt>
      <dd className="inline text-text-primary tabular-nums">{children}</dd>
    </div>
  );
}

/** The full option picture of one name, for the expanded member details. Informational only. */
export function OptionDetails({ o }: { o: RadarOptionMetrics | undefined }) {
  if (!o) return null;
  const a = o.atm;
  return (
    <div className="rounded border border-border/70 bg-card/40 px-3 py-2.5 space-y-1.5">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <span className="text-[10px] font-bold uppercase tracking-wider text-text-secondary">Calls & liquidity</span>
        <span className="text-[11px] text-text-secondary">Yahoo quotes (delayed) · {o.as_of.slice(11, 16)} UTC</span>
      </div>
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
        <Fact label="Stock $ volume" hint="Typical dollar volume traded per day (20-day median)">
          {fmtUsdCompact(o.adv_usd)}/day
        </Fact>
        {o.has_options && o.expiry ? (
          <>
            <Fact label="Expiry" hint="First expiry at least 7 days out">
              {fmtShortDate(o.expiry)} ({o.dte} days){o.weeklies ? ' · weeklies' : ' · monthlies only'}
            </Fact>
            {a && (
              <Fact label="At-the-money call" hint="Strike nearest the price: bid / ask, volume today, open interest">
                {fmtStrike(a.strike)} · {a.bid ?? '—'} / {a.ask ?? '—'} · vol {fmtCompact(a.volume)} · OI {fmtCompact(a.oi)}
              </Fact>
            )}
            <Fact label="Near-the-money calls" hint="Calls with strikes within ±10% of the price">
              OI {fmtCompact(o.ntm_call_oi)} · {fmtCompact(o.ntm_call_volume)} traded today
            </Fact>
            <Fact label="Spread" hint="At-the-money call bid-ask spread in % of mid: what a round trip can cost">
              {n1(a?.spread_pct, '%')}
            </Fact>
            <Fact
              label="IV vs realised"
              hint="Implied volatility of the at-the-money call against the stock's realised volatility; above 1.5× the option is priced for a big move"
            >
              {n1(o.iv_pct, '%')} vs {n1(o.hv_pct, '%')}
              {o.iv_hv !== null ? ` (${o.iv_hv.toFixed(2)}×)` : ''}
            </Fact>
            <Fact
              label="Break-even"
              hint="Stock move needed by expiry for the at-the-money call bought at the ask to break even, against the 1σ move its IV implies"
            >
              {a?.breakeven_pct !== null && a?.breakeven_pct !== undefined ? `+${a.breakeven_pct.toFixed(1)}%` : '—'}
              {o.expected_move_pct !== null ? ` · implied ±${o.expected_move_pct.toFixed(1)}%` : ''}
            </Fact>
            <Fact label="Put/call volume" hint="Put volume over call volume today for this expiry">
              {o.put_call_volume !== null ? o.put_call_volume.toFixed(2) : '—'}
            </Fact>
          </>
        ) : (
          <Fact label="Options">none listed</Fact>
        )}
        <Fact label="Next earnings">
          {o.earnings_date
            ? `${fmtShortDate(o.earnings_date)} (${o.days_to_earnings} days${o.earnings_estimate ? ', estimated' : ''})${
                o.earnings_before_expiry ? ' · before expiry' : ''
              }`
            : '—'}
        </Fact>
      </dl>
    </div>
  );
}
