import type { RadarCatalyst, RadarIvRiskLevel, RadarOptionMetrics } from '../../types';
import { fmtStrike } from '../../utils/format';
import { fmtEt, toMs } from './clock';
import { LIQ_CLASS, LIQ_HINT, LIQ_TEXT, fmtCompact, fmtShortDate, fmtUsdCompact } from './optionFilters';

const n1 = (v: number | null | undefined, suffix = '') =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(1)}${suffix}`;
const n0 = (v: number | null | undefined, suffix = '') =>
  v === null || v === undefined || Number.isNaN(v) ? '—' : `${v.toFixed(0)}${suffix}`;

// Literal class maps (Tailwind purge).
const RISK_CLASS: Record<RadarIvRiskLevel, string> = {
  crushing: 'bg-red-500/25 text-red-200 border-red-400/60',
  high: 'bg-orange-500/20 text-orange-200 border-orange-400/50',
  elevated: 'bg-amber-500/15 text-amber-200 border-amber-400/40',
  normal: 'bg-gray-500/10 text-text-secondary border-border',
};
const RISK_ICON: Record<RadarIvRiskLevel, string> = { crushing: '▼', high: '⚠', elevated: '▲', normal: '' };

const EARN_TIME: Record<string, string> = { bmo: 'before the open', amc: 'after the close' };

/** "IV 38% · 1.5× · ↓18% today", coloured by crush risk; the reasons are in the tooltip. */
export function IvBadge({ o }: { o: RadarOptionMetrics | undefined }) {
  if (!o || !o.iv_pct) return null;
  const risk = o.iv_risk?.level ?? 'normal';
  const chg = o.iv_change_pct;
  const tip = [
    o.iv_risk?.label ?? 'Implied volatility',
    ...(o.iv_risk?.reasons ?? []),
    o.vega_per_pt_usd ? `Each IV point is worth about $${o.vega_per_pt_usd.toFixed(0)} per contract.` : '',
  ]
    .filter(Boolean)
    .join('\n');
  return (
    <span className={`px-1.5 py-px rounded border font-semibold tabular-nums cursor-help whitespace-nowrap ${RISK_CLASS[risk]}`} title={tip}>
      {RISK_ICON[risk] && <span aria-hidden="true">{RISK_ICON[risk]} </span>}
      IV {n0(o.iv_pct, '%')}
      {o.iv_hv ? ` · ${o.iv_hv.toFixed(1)}×` : ''}
      {chg !== null && chg !== undefined && Math.abs(chg) >= 3 ? ` · ${chg < 0 ? '↓' : '↑'}${Math.abs(chg).toFixed(0)}% today` : ''}
    </span>
  );
}

const KIND_ICON: Record<RadarCatalyst['kind'], string> = { '8-K': '📄', press: '📣', analyst: '🎯', news: '📰' };

/** Why it may be moving: the best catalyst, plus earnings and low-float badges. */
export function CatalystLine({ o }: { o: RadarOptionMetrics | undefined }) {
  if (!o || o.context_source !== 'fmp') return null;
  const top = o.catalysts?.[0];
  const t = toMs(top?.at);
  const earnSoon = o.days_to_earnings !== null && o.days_to_earnings !== undefined && o.days_to_earnings <= 7;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] leading-snug min-w-0">
      {top ? (
        <span className="text-text-secondary min-w-0" title={o.catalysts?.map((c) => `${c.kind}: ${c.title}`).join('\n')}>
          <span aria-hidden="true">{KIND_ICON[top.kind]} </span>
          <span className="text-text-primary">{top.title}</span>
          {t !== null && <span className="tabular-nums"> · {fmtEt(t)}</span>}
          {(o.catalysts?.length ?? 0) > 1 && <span> · +{(o.catalysts?.length ?? 1) - 1} more</span>}
        </span>
      ) : (
        <span className="text-text-secondary/80" title="No news, press release, 8-K or analyst action in the last 24 hours">
          No news found: a technical or sector move
        </span>
      )}
      {earnSoon && (
        <span className="px-1.5 py-px rounded border border-orange-400/50 bg-orange-500/15 text-orange-200 font-semibold">
          Earnings {fmtShortDate(o.earnings_date)}
          {o.earnings_time && EARN_TIME[o.earnings_time] ? ` ${o.earnings_time.toUpperCase()}` : ''}
        </span>
      )}
      {o.float?.low && (
        <span className="px-1.5 py-px rounded border border-fuchsia-400/50 bg-fuchsia-500/15 text-fuchsia-200 font-semibold" title="Free float under 30M shares: moves can be violent both ways">
          Low float {fmtCompact(o.float.shares)}
        </span>
      )}
    </span>
  );
}

/** Compact one-line summary: liquidity pill, expiry, call OI, spread, IV badge, earnings flag. */
export function OptionChip({ o }: { o: RadarOptionMetrics | undefined }) {
  if (!o) return <span className="text-[11px] text-text-secondary/70">Option data after the next scan</span>;
  const earn = o.earnings_before_expiry;
  return (
    <span className="inline-flex flex-wrap items-center gap-x-2 gap-y-1 text-[11px] text-text-secondary">
      <span className={`px-1.5 py-px rounded border font-semibold cursor-help ${LIQ_CLASS[o.liquidity]}`} title={LIQ_HINT[o.liquidity]}>
        {LIQ_TEXT[o.liquidity]}
      </span>
      <IvBadge o={o} />
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
        </>
      )}
      {earn && (
        <span
          className="px-1.5 py-px rounded border border-red-500/40 bg-red-900/30 text-red-300 font-semibold cursor-help"
          title="Earnings on or before this expiry: option prices usually drop sharply right after the report (IV crush)."
        >
          Earnings {fmtShortDate(o.earnings_date)} before expiry
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

/** IV crush risk: label, reasons, and the numbers behind them. */
function IvSection({ o }: { o: RadarOptionMetrics }) {
  const risk = o.iv_risk?.level ?? 'normal';
  const first = o.iv_first;
  const firstAt = toMs(first?.at);
  return (
    <div className={`rounded border px-2.5 py-2 space-y-1.5 ${RISK_CLASS[risk]}`}>
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <span className="text-[11px] font-bold uppercase tracking-wider">
          {RISK_ICON[risk] && <span aria-hidden="true">{RISK_ICON[risk]} </span>}
          {o.iv_risk?.label ?? 'IV'}
        </span>
        {o.vega_per_pt_usd ? (
          <span className="text-[11px] tabular-nums" title="Black-Scholes vega of one at-the-money contract (100 shares)">
            1 IV point ≈ ${o.vega_per_pt_usd.toFixed(0)} per contract
          </span>
        ) : null}
      </div>
      {o.iv_risk?.reasons?.length ? (
        <ul className="list-disc pl-4 text-[11px] leading-snug space-y-0.5">
          {o.iv_risk.reasons.map((r) => (
            <li key={r}>{r}</li>
          ))}
        </ul>
      ) : (
        <p className="text-[11px] leading-snug">IV is in line with the stock's usual volatility and no event is priced into this expiry.</p>
      )}
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-[11px] text-text-primary">
        <Fact label="IV vs normal" hint="At-the-money implied volatility against the stock's realised volatility (20 days)">
          {n1(o.iv_pct, '%')} vs {n1(o.hv_pct, '%')}
          {o.iv_hv ? ` (${o.iv_hv.toFixed(2)}×)` : ''}
        </Fact>
        <Fact label="Front vs ~30 days" hint="Front-week IV over the IV ~30 days out: above 1.2 an event is priced into this week">
          {n1(o.iv_pct, '%')} vs {n1(o.iv_30d_pct, '%')}
          {o.term_ratio ? ` (${o.term_ratio.toFixed(2)}×)` : ''}
        </Fact>
        <Fact label="Today" hint="Change of the at-the-money IV since the first reading today (same expiry)">
          {first && firstAt !== null
            ? `${n1(first.pct, '%')} at ${fmtEt(firstAt)} → ${n1(o.iv_pct, '%')} (${o.iv_change_pts !== null && o.iv_change_pts !== undefined ? `${o.iv_change_pts > 0 ? '+' : ''}${o.iv_change_pts.toFixed(1)} pts` : '—'})`
            : '—'}
        </Fact>
      </dl>
    </div>
  );
}

/** The full option picture of one name, for the expanded member details. Informational only. */
export function OptionDetails({ o }: { o: RadarOptionMetrics | undefined }) {
  if (!o) return null;
  const a = o.atm;
  return (
    <div className="rounded border border-border/70 bg-card/40 px-3 py-2.5 space-y-2">
      <div className="flex items-baseline justify-between gap-2 flex-wrap">
        <span className="text-[10px] font-bold uppercase tracking-wider text-text-secondary">Calls, IV & catalysts</span>
        <span className="text-[11px] text-text-secondary">Option quotes from Yahoo (delayed) · {o.as_of.slice(11, 16)} UTC</span>
      </div>
      {o.has_options && o.iv_pct ? <IvSection o={o} /> : null}
      {o.catalysts && o.catalysts.length > 0 && (
        <ul className="space-y-0.5 text-[11px] leading-snug">
          {o.catalysts.map((c) => {
            const t = toMs(c.at);
            return (
              <li key={`${c.kind}|${c.at}|${c.title}`} className="text-text-secondary">
                <span aria-hidden="true">{KIND_ICON[c.kind]} </span>
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noreferrer" className="text-text-primary hover:text-accent-300">
                    {c.title}
                  </a>
                ) : (
                  <span className="text-text-primary">{c.title}</span>
                )}
                <span className="tabular-nums">
                  {' '}
                  · {c.source} · {t !== null ? fmtEt(t) : ''}
                </span>
              </li>
            );
          })}
        </ul>
      )}
      <dl className="flex flex-wrap gap-x-5 gap-y-1 text-xs">
        <Fact label="Stock $ volume" hint="Typical dollar volume traded per day (20-day median)">
          {fmtUsdCompact(o.adv_usd)}/day
        </Fact>
        {o.float && (
          <Fact label="Free float" hint="Shares available to trade">
            {fmtCompact(o.float.shares)}
            {o.float.free_pct !== null ? ` (${o.float.free_pct}%)` : ''}
          </Fact>
        )}
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
            ? `${fmtShortDate(o.earnings_date)}${o.earnings_time && EARN_TIME[o.earnings_time] ? ` ${EARN_TIME[o.earnings_time]}` : ''} (${o.days_to_earnings} days${
                o.earnings_estimate ? ', estimated' : ''
              })${o.earnings_before_expiry ? ' · before expiry' : ''}`
            : '—'}
        </Fact>
        {o.last_earnings_date && (
          <Fact label="Last earnings">
            {fmtShortDate(o.last_earnings_date)}
            {o.last_earnings_time && EARN_TIME[o.last_earnings_time] ? ` ${EARN_TIME[o.last_earnings_time]}` : ''}
          </Fact>
        )}
      </dl>
    </div>
  );
}
