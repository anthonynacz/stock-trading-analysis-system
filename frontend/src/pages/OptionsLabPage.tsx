import { useState, useCallback, useEffect, useMemo, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import type {
  DeepOptionsAnalysis,
  ExpiryGreeksRow,
  ExtendedGreeks,
  HiddenRisk,
  Liquidity,
  Positioning,
  RiskSeverity,
  StrategyLeg,
  StrategyRecommendation,
  VolStructure,
} from '../types';
import { getDeepOptionsResults, deleteDeepOptionsResult } from '../utils/api';
import { useOptionsLabContext } from '../contexts/OptionsLabContext';

// ── helpers ──────────────────────────────────────────────────────────────

function fmtNum(v: number | null | undefined, digits = 2, fallback = '—'): string {
  if (v === null || v === undefined || Number.isNaN(v)) return fallback;
  return v.toFixed(digits);
}

function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)}%`;
}

function fmtInt(v: number | null | undefined, fallback = '—'): string {
  if (v === null || v === undefined || Number.isNaN(v)) return fallback;
  return Math.round(v).toLocaleString();
}

function fmtTimestamp(ts: string): string {
  const d = new Date(ts);
  return d.toLocaleDateString('en-US', {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
}

function fmtExpiry(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

const VERDICT_COLORS: Record<string, string> = {
  BUY_CALL: '#2ea043',
  BUY_CALL_SPREAD: '#2ea043',
  BUY_PUT: '#da3633',
  BUY_PUT_SPREAD: '#da3633',
  SELL_PUT_SPREAD: '#56d364',
  SELL_CALL_SPREAD: '#f85149',
  SELL_IRON_CONDOR: '#d29922',
  BUY_STRADDLE: '#8b5cf6',
  NO_TRADE: '#6e7681',
};

const BIAS_COLORS: Record<string, string> = {
  BULLISH: '#2ea043',
  BEARISH: '#da3633',
  NEUTRAL: '#8b949e',
};

const SEVERITY_STYLES: Record<RiskSeverity, { bg: string; text: string; border: string }> = {
  HIGH: { bg: 'bg-red-900/30', text: 'text-red-400', border: 'border-red-800/50' },
  MEDIUM: { bg: 'bg-amber-900/30', text: 'text-amber-400', border: 'border-amber-800/50' },
  LOW: { bg: 'bg-blue-900/20', text: 'text-blue-400', border: 'border-blue-800/40' },
};

// ── SectionHeader ────────────────────────────────────────────────────────

function SectionHeader({ title, rightSlot }: { title: string; rightSlot?: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between mb-2">
      <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">{title}</h4>
      {rightSlot}
    </div>
  );
}

// ── Card thumbnail for result grid ───────────────────────────────────────

function ResultCard({ r, selected, onClick, onDelete }: {
  r: DeepOptionsAnalysis;
  selected: boolean;
  onClick: () => void;
  onDelete: () => void;
}) {
  const verdictColor = VERDICT_COLORS[r.verdict ?? 'NO_TRADE'] ?? '#6e7681';
  const biasColor = BIAS_COLORS[r.directional_bias ?? 'NEUTRAL'];
  const ivRank = r.iv_rank ?? 0;
  const risks = r.hidden_risks ?? [];
  const highRisks = risks.filter((x) => x.severity === 'HIGH').length;
  const medRisks = risks.filter((x) => x.severity === 'MEDIUM').length;

  return (
    <div
      onClick={onClick}
      className={`bg-card border rounded-lg p-3 cursor-pointer transition-all hover:border-text-secondary group relative ${
        selected ? 'border-accent-500 ring-1 ring-accent-500/30' : 'border-border'
      }`}
    >
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        className="absolute top-2 right-2 p-1 rounded opacity-0 group-hover:opacity-100 hover:bg-red-900/40 transition-all text-text-secondary hover:text-red-400"
        title="Delete"
      >
        <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
        </svg>
      </button>
      <div className="flex items-center gap-2 mb-2">
        <span className="text-sm font-bold text-text-primary">{r.ticker}</span>
        <span
          className="px-1.5 py-0.5 rounded text-[9px] font-bold uppercase"
          style={{ backgroundColor: verdictColor + '22', color: verdictColor }}
        >
          {(r.verdict ?? '').replace(/_/g, ' ') || '—'}
        </span>
      </div>
      {r.company_name && (
        <p className="text-[10px] text-text-secondary mb-1.5 truncate">{r.company_name}</p>
      )}
      <div className="grid grid-cols-3 gap-1 text-[10px] mb-1.5">
        <div>
          <div className="text-text-secondary">Price</div>
          <div className="font-mono text-text-primary">
            {r.stock_price != null ? `$${r.stock_price.toFixed(2)}` : '—'}
          </div>
        </div>
        <div>
          <div className="text-text-secondary">IV Rank</div>
          <div className="font-mono text-text-primary">
            {r.iv_rank != null ? ivRank.toFixed(0) : '—'}
          </div>
        </div>
        <div>
          <div className="text-text-secondary">Bias</div>
          <div className="font-mono" style={{ color: biasColor }}>
            {r.directional_bias ?? '—'}
          </div>
        </div>
      </div>
      <div className="flex items-center justify-between text-[10px] text-text-secondary">
        <div className="flex gap-1.5">
          {highRisks > 0 && (
            <span className="px-1.5 py-px rounded bg-red-900/40 text-red-400 font-semibold">
              {highRisks} high
            </span>
          )}
          {medRisks > 0 && (
            <span className="px-1.5 py-px rounded bg-amber-900/40 text-amber-400 font-semibold">
              {medRisks} med
            </span>
          )}
        </div>
        <span>{fmtTimestamp(r.analyzed_at)}</span>
      </div>
    </div>
  );
}

// ── StrategyCard ─────────────────────────────────────────────────────────

function StrategyCard({ strategy }: { strategy: StrategyRecommendation | null }) {
  if (!strategy) return null;
  const color = VERDICT_COLORS[strategy.verdict] ?? '#6e7681';
  const verdictLabel = strategy.verdict.replace(/_/g, ' ');

  return (
    <div
      className="rounded-lg p-4 border"
      style={{
        backgroundColor: color + '10',
        borderColor: color + '55',
      }}
    >
      <div className="flex items-center gap-3 mb-3">
        <div>
          <div className="text-[10px] text-text-secondary uppercase tracking-wider">Strategy Verdict</div>
          <div className="text-lg font-bold" style={{ color }}>{verdictLabel}</div>
        </div>
        <div className="ml-auto text-right">
          <div className="text-[10px] text-text-secondary uppercase tracking-wider">Target</div>
          <div className="text-xs font-mono text-text-primary">
            {strategy.target_expiry
              ? `${fmtExpiry(strategy.target_expiry)} · ${strategy.target_dte}d`
              : '—'}
          </div>
        </div>
      </div>

      {/* IV bucket + earnings tag */}
      <div className="flex flex-wrap gap-1.5 mb-3">
        <span className="px-1.5 py-px rounded text-[10px] font-semibold uppercase bg-border text-text-secondary">
          IV {strategy.iv_bucket}
        </span>
        {strategy.near_earnings && strategy.earnings_dte !== null && (
          <span className="px-1.5 py-px rounded text-[10px] font-semibold uppercase bg-accent-900/40 text-accent-400">
            Earnings {strategy.earnings_dte}d
          </span>
        )}
      </div>

      {/* Legs */}
      {strategy.legs.length > 0 && (
        <div className="space-y-1.5 mb-3">
          {strategy.legs.map((leg, idx) => (
            <LegRow key={idx} leg={leg} />
          ))}
        </div>
      )}

      {/* Notes */}
      {strategy.notes.length > 0 && (
        <ul className="space-y-1 text-[11px] text-text-secondary">
          {strategy.notes.map((n, i) => (
            <li key={i} className="leading-snug">• {n}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LegRow({ leg }: { leg: StrategyLeg }) {
  const actionColor = leg.action === 'BUY' ? '#56d364' : '#f85149';
  const sideLabel = leg.side === 'CALL' ? 'C' : 'P';
  const sideBg = leg.side === 'CALL' ? 'bg-green-900/30 text-green-400' : 'bg-red-900/30 text-red-400';

  return (
    <div className="bg-card border border-border rounded p-2 text-xs">
      <div className="flex items-center gap-2 mb-1">
        <span
          className="px-1.5 py-0.5 rounded text-[10px] font-bold"
          style={{ backgroundColor: actionColor + '22', color: actionColor }}
        >
          {leg.action}
        </span>
        <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${sideBg}`}>
          {sideLabel}
        </span>
        <span className="font-mono text-text-primary">${leg.strike.toFixed(2)}</span>
        <span className="text-text-secondary">·</span>
        <span className="font-mono text-text-secondary">{fmtExpiry(leg.expiry)}</span>
        <span className="text-text-secondary ml-auto">qty {leg.qty}</span>
      </div>
      <p className="text-[11px] text-text-secondary leading-snug">{leg.rationale}</p>
    </div>
  );
}

// ── HiddenRisksPanel ─────────────────────────────────────────────────────

function HiddenRisksPanel({ risks }: { risks: HiddenRisk[] | null }) {
  if (!risks || risks.length === 0) {
    return (
      <div className="bg-card border border-border rounded-lg p-3 text-center text-xs text-text-secondary">
        No hidden risks flagged. Standard trade risks still apply.
      </div>
    );
  }
  // Sort by severity: HIGH, MEDIUM, LOW
  const order: RiskSeverity[] = ['HIGH', 'MEDIUM', 'LOW'];
  const sorted = [...risks].sort(
    (a, b) => order.indexOf(a.severity) - order.indexOf(b.severity),
  );
  return (
    <div className="space-y-2">
      {sorted.map((r, i) => {
        const s = SEVERITY_STYLES[r.severity];
        return (
          <div key={i} className={`rounded-lg p-3 border ${s.bg} ${s.border}`}>
            <div className="flex items-center gap-2 mb-1">
              <span className={`px-1.5 py-px rounded text-[10px] font-bold uppercase ${s.text}`}>
                {r.severity}
              </span>
              <span className="text-[10px] font-mono text-text-secondary">{r.code}</span>
              <span className={`text-xs font-semibold ${s.text} ml-1`}>{r.title}</span>
            </div>
            <p className="text-[11px] text-text-secondary leading-snug">{r.detail}</p>
          </div>
        );
      })}
    </div>
  );
}

// ── GreeksTable ──────────────────────────────────────────────────────────

function GreeksTable({ rows }: { rows: ExpiryGreeksRow[] }) {
  if (rows.length === 0) {
    return <p className="text-xs text-text-secondary">No greek data available.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[11px] font-mono">
        <thead>
          <tr className="text-text-secondary text-left">
            <th className="py-1 pr-2 font-normal">Exp</th>
            <th className="py-1 pr-2 font-normal">DTE</th>
            <th className="py-1 pr-2 font-normal">ATM</th>
            <th className="py-1 pr-2 font-normal">IV</th>
            <th className="py-1 pr-2 font-normal">EM%</th>
            <th className="py-1 pr-2 font-normal text-right">Δ</th>
            <th className="py-1 pr-2 font-normal text-right">Γ</th>
            <th className="py-1 pr-2 font-normal text-right">Θ/d</th>
            <th className="py-1 pr-2 font-normal text-right">V</th>
            <th className="py-1 pr-2 font-normal text-right">ρ</th>
            <th className="py-1 pr-2 font-normal text-right" title="Vanna: delta per 1% IV">Vn</th>
            <th className="py-1 pr-2 font-normal text-right" title="Charm: delta per day">Ch</th>
            <th className="py-1 pr-2 font-normal text-right" title="Vomma: vega per 1% IV">Vm</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <GreeksRow key={row.expiry} row={row} leg="call" />
          ))}
        </tbody>
      </table>
    </div>
  );
}

function GreeksRow({ row, leg }: { row: ExpiryGreeksRow; leg: 'call' | 'put' }) {
  const g: ExtendedGreeks | null = leg === 'call' ? row.atm_call : row.atm_put;
  const hot = g?.theta_pct != null && g.theta_pct > 3;
  return (
    <tr className="border-t border-border">
      <td className="py-1 pr-2 text-text-primary">{fmtExpiry(row.expiry)}</td>
      <td className="py-1 pr-2 text-text-secondary">{row.dte}</td>
      <td className="py-1 pr-2 text-text-primary">
        {row.atm_strike != null ? `$${row.atm_strike.toFixed(0)}` : '—'}
      </td>
      <td className="py-1 pr-2 text-text-secondary">
        {row.atm_iv != null ? `${(row.atm_iv * 100).toFixed(1)}%` : '—'}
      </td>
      <td className="py-1 pr-2 text-text-secondary">{fmtPct(row.expected_move_pct, 1)}</td>
      <td className="py-1 pr-2 text-right text-text-primary">{fmtNum(g?.delta, 2)}</td>
      <td className="py-1 pr-2 text-right text-text-primary">{fmtNum(g?.gamma, 4)}</td>
      <td className={`py-1 pr-2 text-right ${hot ? 'text-red-400' : 'text-text-primary'}`}>
        {g?.theta != null ? g.theta.toFixed(3) : '—'}
      </td>
      <td className="py-1 pr-2 text-right text-text-primary">{fmtNum(g?.vega, 3)}</td>
      <td className="py-1 pr-2 text-right text-text-secondary">{fmtNum(g?.rho, 3)}</td>
      <td className="py-1 pr-2 text-right text-text-secondary">{fmtNum(g?.vanna, 4)}</td>
      <td className="py-1 pr-2 text-right text-text-secondary">{fmtNum(g?.charm, 4)}</td>
      <td className="py-1 pr-2 text-right text-text-secondary">{fmtNum(g?.vomma, 3)}</td>
    </tr>
  );
}

// ── VolStructurePanel ────────────────────────────────────────────────────

function VolStructurePanel({ v }: { v: VolStructure | null }) {
  if (!v) return null;
  const shapeColor =
    v.term_shape === 'BACKWARDATION' ? '#f85149'
      : v.term_shape === 'CONTANGO' ? '#56d364'
      : v.term_shape === 'FLAT' ? '#d29922'
      : '#8b949e';
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase">Term Shape</div>
          <div className="text-sm font-bold" style={{ color: shapeColor }}>
            {v.term_shape}
          </div>
          <div className="text-[10px] text-text-secondary mt-0.5">
            front − back: {v.front_minus_back_iv_pts != null ? `${v.front_minus_back_iv_pts > 0 ? '+' : ''}${v.front_minus_back_iv_pts.toFixed(2)} pts` : '—'}
          </div>
        </div>
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase">25-Δ Skew</div>
          <div className="text-sm font-bold text-text-primary">
            {v.avg_skew_25d_iv_pts != null
              ? `${v.avg_skew_25d_iv_pts > 0 ? '+' : ''}${v.avg_skew_25d_iv_pts.toFixed(1)} pts`
              : '—'}
          </div>
          <div className="text-[10px] text-text-secondary mt-0.5">
            put IV − call IV (positive = crash hedges bid)
          </div>
        </div>
      </div>

      {/* Term structure mini-table */}
      {v.term_structure.length > 0 && (
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase mb-1">Term Structure</div>
          <div className="flex gap-1.5 flex-wrap">
            {v.term_structure.map((t) => (
              <div key={t.expiry} className="px-1.5 py-1 rounded bg-border text-[10px] font-mono">
                <div className="text-text-secondary">{t.dte}d</div>
                <div className="text-text-primary">
                  {t.atm_iv != null ? `${(t.atm_iv * 100).toFixed(1)}%` : '—'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Expected moves */}
      {v.expected_moves.length > 0 && (
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase mb-1">Expected Moves (ATM straddle)</div>
          <div className="flex gap-1.5 flex-wrap">
            {v.expected_moves.map((m) => (
              <div key={m.expiry} className="px-1.5 py-1 rounded bg-border text-[10px] font-mono">
                <div className="text-text-secondary">{m.dte}d</div>
                <div className="text-text-primary">
                  ±{m.expected_move_pct != null ? m.expected_move_pct.toFixed(1) : '?'}%
                </div>
                {m.expected_move_abs != null && (
                  <div className="text-text-secondary">±${m.expected_move_abs.toFixed(2)}</div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── PositioningPanel ─────────────────────────────────────────────────────

function PositioningPanel({ p }: { p: Positioning | null }) {
  if (!p) return null;
  const regimeColor =
    p.gex_regime === 'POSITIVE' ? '#56d364'
      : p.gex_regime === 'NEGATIVE' ? '#f85149'
      : '#8b949e';
  return (
    <div className="space-y-2">
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase">Dealer Gamma (GEX)</div>
          <div className="text-sm font-bold" style={{ color: regimeColor }}>
            {p.gex_regime}
          </div>
          <div className="text-[10px] font-mono text-text-secondary mt-0.5">
            {p.gex_total > 0 ? '+' : ''}{fmtInt(p.gex_total)}
          </div>
        </div>
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase">Pin Magnets</div>
          <div className="text-sm font-bold text-text-primary">{p.pin_magnets.length}</div>
          <div className="text-[10px] text-text-secondary mt-0.5">near-dated OI clusters</div>
        </div>
      </div>

      {/* Max pain by expiry */}
      {p.max_pain_by_expiry.length > 0 && (
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase mb-1">Max Pain</div>
          <div className="grid grid-cols-2 gap-1.5 text-[11px] font-mono">
            {p.max_pain_by_expiry.map((mp) => (
              <div key={mp.expiry} className="flex justify-between">
                <span className="text-text-secondary">{fmtExpiry(mp.expiry)} ({mp.dte}d)</span>
                <span className="text-text-primary">
                  {mp.max_pain_strike != null ? `$${mp.max_pain_strike.toFixed(0)}` : '—'}
                  {mp.max_pain_distance_pct != null && (
                    <span className="text-text-secondary ml-1">
                      ({mp.max_pain_distance_pct > 0 ? '+' : ''}{mp.max_pain_distance_pct.toFixed(1)}%)
                    </span>
                  )}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pin magnets */}
      {p.pin_magnets.length > 0 && (
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase mb-1">Pin Magnets (near-dated clusters)</div>
          <div className="space-y-1 text-[11px]">
            {p.pin_magnets.slice(0, 5).map((m, i) => (
              <div key={i} className="flex items-center justify-between gap-2">
                <span className="font-mono text-text-primary">
                  ${m.strike.toFixed(0)} · {m.dte}d
                </span>
                <span className="text-text-secondary">
                  C:{fmtInt(m.call_oi)} / P:{fmtInt(m.put_oi)}
                </span>
                <span className="text-amber-400 font-mono">
                  {m.oi_share_of_near_spot.toFixed(0)}%
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* P/C OI by expiry */}
      {p.pc_oi_by_expiry.length > 0 && (
        <div className="bg-card border border-border rounded p-2">
          <div className="text-[10px] text-text-secondary uppercase mb-1">P/C OI by Expiry</div>
          <div className="flex gap-1.5 flex-wrap">
            {p.pc_oi_by_expiry.map((e) => (
              <div key={e.expiry} className="px-1.5 py-1 rounded bg-border text-[10px] font-mono">
                <div className="text-text-secondary">{e.dte}d</div>
                <div className={e.pc_oi_ratio && e.pc_oi_ratio > 1.2 ? 'text-red-400' : e.pc_oi_ratio && e.pc_oi_ratio < 0.8 ? 'text-green-400' : 'text-text-primary'}>
                  {e.pc_oi_ratio != null ? e.pc_oi_ratio.toFixed(2) : '—'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── LiquidityPanel ───────────────────────────────────────────────────────

function LiquidityPanel({ l }: { l: Liquidity | null }) {
  if (!l) return null;
  const ratingColor =
    l.rating === 'TIGHT' ? '#56d364'
      : l.rating === 'MODERATE' ? '#d29922'
      : l.rating === 'WIDE' ? '#f85149'
      : '#8b949e';
  return (
    <div className="bg-card border border-border rounded p-2">
      <div className="flex items-center justify-between mb-2">
        <div>
          <div className="text-[10px] text-text-secondary uppercase">Liquidity</div>
          <div className="text-sm font-bold" style={{ color: ratingColor }}>{l.rating}</div>
        </div>
        <div className="text-right">
          <div className="text-[10px] text-text-secondary uppercase">Avg Spread</div>
          <div className="text-sm font-mono text-text-primary">
            {l.avg_spread_pct != null ? `${l.avg_spread_pct.toFixed(1)}%` : '—'}
          </div>
        </div>
      </div>
      {l.by_expiry.length > 0 && (
        <div className="grid grid-cols-2 gap-1 text-[10px] font-mono">
          {l.by_expiry.map((e) => (
            <div key={e.expiry} className="flex justify-between">
              <span className="text-text-secondary">{e.dte}d</span>
              <span className="flex items-center gap-1">
                <span className={e.wide_spreads ? 'text-red-400' : 'text-text-primary'}>
                  {e.median_spread_pct != null ? `${e.median_spread_pct.toFixed(1)}%` : '—'}
                </span>
                <span className={e.thin_oi ? 'text-amber-400' : 'text-text-secondary'}>
                  OI:{fmtInt(e.total_oi)}
                </span>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Detail panel ─────────────────────────────────────────────────────────

function DeepDetail({
  r,
  onClose,
  onReanalyze,
  analyzing,
}: {
  r: DeepOptionsAnalysis;
  onClose: () => void;
  onReanalyze: (ticker: string) => void;
  analyzing: boolean;
}) {
  const biasColor = BIAS_COLORS[r.directional_bias ?? 'NEUTRAL'];
  const expirationRows = r.greeks_detail?.expirations ?? [];

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-4 sticky top-4 max-h-[calc(100vh-2rem)] overflow-y-auto">
      {/* Header */}
      <div className="flex items-start gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-bold text-text-primary">{r.ticker}</h2>
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-bold uppercase"
              style={{ color: biasColor, backgroundColor: biasColor + '22' }}
            >
              {r.directional_bias ?? '—'}
            </span>
          </div>
          {r.company_name && (
            <p className="text-[11px] text-text-secondary truncate">{r.company_name}</p>
          )}
          <p className="text-[10px] text-text-secondary">{fmtTimestamp(r.analyzed_at)}</p>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => onReanalyze(r.ticker)}
            disabled={analyzing}
            className="p-1.5 rounded hover:bg-border text-text-secondary hover:text-text-primary transition-colors disabled:opacity-40"
            title="Re-analyze"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
          <button
            onClick={onClose}
            className="p-1.5 rounded hover:bg-border text-text-secondary hover:text-text-primary transition-colors"
            title="Close"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      {/* Price + IV */}
      <div className="grid grid-cols-3 gap-2">
        <StatTile label="Price" value={r.stock_price != null ? `$${r.stock_price.toFixed(2)}` : '—'} />
        <StatTile label="IV Rank" value={r.iv_rank != null ? r.iv_rank.toFixed(0) : '—'} />
        <StatTile label="IV %tile" value={r.iv_percentile != null ? r.iv_percentile.toFixed(0) : '—'} />
      </div>

      {/* Strategy card */}
      <section>
        <SectionHeader title="Strategy Recommendation" />
        <StrategyCard strategy={r.strategy} />
      </section>

      {/* Hidden risks */}
      <section>
        <SectionHeader
          title="Hidden Risks"
          rightSlot={
            r.hidden_risks && r.hidden_risks.length > 0 ? (
              <span className="text-[10px] text-text-secondary">
                {r.hidden_risks.length} flagged
              </span>
            ) : null
          }
        />
        <HiddenRisksPanel risks={r.hidden_risks} />
      </section>

      {/* Vol structure */}
      <section>
        <SectionHeader title="Volatility Structure" />
        <VolStructurePanel v={r.vol_structure} />
      </section>

      {/* Positioning */}
      <section>
        <SectionHeader title="Positioning" />
        <PositioningPanel p={r.positioning} />
      </section>

      {/* Liquidity */}
      <section>
        <SectionHeader title="Liquidity" />
        <LiquidityPanel l={r.liquidity} />
      </section>

      {/* Greeks table */}
      <section>
        <SectionHeader title="ATM Call Greeks by Expiry" />
        <GreeksTable rows={expirationRows} />
        <p className="text-[10px] text-text-secondary mt-1 italic">
          Δ delta · Γ gamma · Θ theta/day · V vega/1%IV · ρ rho/1% rate · Vn vanna · Ch charm · Vm vomma
        </p>
      </section>

      {/* Rationale */}
      {r.rationale && (
        <section>
          <SectionHeader title="Rationale" />
          <p className="text-xs text-text-secondary leading-relaxed">{r.rationale}</p>
        </section>
      )}
    </div>
  );
}

function StatTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-page border border-border rounded p-2">
      <div className="text-[10px] text-text-secondary uppercase">{label}</div>
      <div className="text-sm font-mono font-semibold text-text-primary">{value}</div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────────────────

export default function OptionsLabPage() {
  const {
    analyzingTicker,
    analyzeError,
    lastResult,
    batchProgress,
    startAnalysis,
    refreshAll,
    cancelBatch,
    consumeResult,
    clearError,
  } = useOptionsLabContext();

  const [tickerInput, setTickerInput] = useState('');
  const [results, setResults] = useState<DeepOptionsAnalysis[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [filterTicker, setFilterTicker] = useState('');
  const [searchParams, setSearchParams] = useSearchParams();
  const autoAnalyzedRef = useRef<string | null>(null);

  const fetchResults = useCallback(async () => {
    try {
      setLoading(true);
      const data = await getDeepOptionsResults(filterTicker || undefined);
      setResults(data);
    } catch {
      // silent
    } finally {
      setLoading(false);
    }
  }, [filterTicker]);

  useEffect(() => { fetchResults(); }, [fetchResults]);

  useEffect(() => {
    if (lastResult) {
      setSelectedId(lastResult.id);
      consumeResult();
      fetchResults();
    }
  }, [lastResult, consumeResult, fetchResults]);

  // Auto-analyze when navigated with ?ticker=X&auto=1 (e.g., from RecommendationCard)
  useEffect(() => {
    const qTicker = (searchParams.get('ticker') || '').trim().toUpperCase();
    const auto = searchParams.get('auto') === '1';
    if (!qTicker) return;
    if (auto && !analyzingTicker && autoAnalyzedRef.current !== qTicker) {
      autoAnalyzedRef.current = qTicker;
      clearError();
      startAnalysis(qTicker);
      // Strip auto so a refresh doesn't re-run; keep ticker for context
      setSearchParams({ ticker: qTicker }, { replace: true });
    }
  }, [searchParams, analyzingTicker, startAnalysis, clearError, setSearchParams]);

  const handleAnalyze = () => {
    const ticker = tickerInput.trim().toUpperCase();
    if (!ticker || analyzingTicker) return;
    clearError();
    setTickerInput('');
    startAnalysis(ticker);
  };

  const handleReanalyze = (ticker: string) => {
    if (analyzingTicker) return;
    clearError();
    startAnalysis(ticker);
  };

  const handleDelete = async (id: number) => {
    try {
      await deleteDeepOptionsResult(id);
      if (selectedId === id) setSelectedId(null);
      setResults((prev) => prev.filter((r) => r.id !== id));
    } catch {
      // silent
    }
  };

  const handleRefreshAll = () => {
    if (analyzingTicker || results.length === 0) return;
    clearError();
    const tickers = Array.from(new Set(results.map((r) => r.ticker)));
    refreshAll(tickers);
  };

  const handleClearAll = async () => {
    if (analyzingTicker || results.length === 0) return;
    const n = results.length;
    if (!window.confirm(`Delete all ${n} saved analyses? This cannot be undone.`)) return;
    clearError();
    const ids = results.map((r) => r.id);
    await Promise.allSettled(ids.map((id) => deleteDeepOptionsResult(id)));
    setSelectedId(null);
    setResults([]);
  };

  const selectedResult = useMemo(
    () => results.find((r) => r.id === selectedId) ?? null,
    [results, selectedId],
  );

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-7xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-4 sm:space-y-6">
        {/* Header */}
        <div className="flex items-center gap-3">
          <div>
            <h1 className="text-xl font-bold text-text-primary">Options Lab</h1>
            <p className="text-[11px] text-text-secondary">
              Expert options analysis: greeks, vol structure, positioning, strategy + hidden risks
            </p>
          </div>
          <div className="flex items-center gap-2 ml-auto">
            <button
              type="button"
              onClick={handleRefreshAll}
              disabled={!!analyzingTicker || results.length === 0}
              title={
                results.length === 0
                  ? 'No tickers to refresh'
                  : `Re-run deep analysis for all ${results.length} ticker${results.length !== 1 ? 's' : ''}`
              }
              className="px-3 py-1.5 text-sm font-semibold rounded bg-card border border-border text-text-primary hover:border-accent-500 hover:text-accent-300 disabled:opacity-40 disabled:hover:border-border disabled:hover:text-text-primary transition-colors flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
              </svg>
              Refresh All
            </button>
            <button
              type="button"
              onClick={handleClearAll}
              disabled={!!analyzingTicker || results.length === 0}
              title={
                results.length === 0
                  ? 'No analyses to clear'
                  : `Delete all ${results.length} saved analysis${results.length !== 1 ? 'es' : ''}`
              }
              className="px-3 py-1.5 text-sm font-semibold rounded bg-card border border-border text-text-primary hover:border-red-500 hover:text-red-400 disabled:opacity-40 disabled:hover:border-border disabled:hover:text-text-primary transition-colors flex items-center gap-1.5"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
              Clear All
            </button>
            <form
              onSubmit={(e) => { e.preventDefault(); handleAnalyze(); }}
              className="flex items-center gap-2"
            >
              <input
                type="text"
                value={tickerInput}
                onChange={(e) => { setTickerInput(e.target.value); clearError(); }}
                placeholder="Enter ticker..."
                disabled={!!analyzingTicker}
                className="w-32 px-3 py-1.5 text-sm bg-card border border-border rounded text-text-primary placeholder-text-secondary focus:outline-none focus:border-accent-500 disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={!!analyzingTicker || !tickerInput.trim()}
                className="px-4 py-1.5 text-sm font-semibold rounded bg-accent-900/60 text-accent-400 hover:bg-accent-800/60 disabled:opacity-40 transition-colors flex items-center gap-2"
              >
                {analyzingTicker && !batchProgress ? (
                  <>
                    <div className="w-3.5 h-3.5 border-2 border-accent-400 border-t-transparent rounded-full animate-spin" />
                    Analyzing...
                  </>
                ) : (
                  'Analyze'
                )}
              </button>
            </form>
          </div>
        </div>

        {analyzeError && <p className="text-xs text-red-400">{analyzeError}</p>}

        {analyzingTicker && (
          <div className="bg-card border border-border rounded-lg p-4 flex items-center gap-3">
            <div className="w-4 h-4 border-2 border-accent-400 border-t-transparent rounded-full animate-spin shrink-0" />
            {batchProgress ? (
              <>
                <div className="flex-1 flex items-center gap-3">
                  <span className="text-sm text-text-secondary">
                    Refreshing <span className="text-text-primary font-semibold">{batchProgress.ticker}</span>
                    <span className="text-text-secondary"> ({batchProgress.current} of {batchProgress.total})</span>
                  </span>
                  <div className="flex-1 max-w-xs h-1.5 rounded-full bg-border overflow-hidden">
                    <div
                      className="h-full rounded-full bg-accent-500 transition-all"
                      style={{ width: `${(batchProgress.current / batchProgress.total) * 100}%` }}
                    />
                  </div>
                </div>
                <button
                  onClick={cancelBatch}
                  className="text-xs text-text-secondary hover:text-red-400 transition-colors"
                >
                  Cancel
                </button>
              </>
            ) : (
              <span className="text-sm text-text-secondary">
                Running deep options analysis for <span className="text-text-primary font-semibold">{analyzingTicker}</span>...
              </span>
            )}
          </div>
        )}

        {/* Grid + Detail */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
          <div className="lg:col-span-3 space-y-3">
            {/* Filter */}
            <div className="flex items-center gap-2">
              <input
                type="text"
                value={filterTicker}
                onChange={(e) => setFilterTicker(e.target.value)}
                placeholder="Filter by ticker..."
                className="w-36 px-2 py-1 text-xs bg-card border border-border rounded text-text-primary placeholder-text-secondary focus:outline-none focus:border-accent-500"
              />
              {filterTicker && (
                <button
                  onClick={() => setFilterTicker('')}
                  className="text-xs text-text-secondary hover:text-text-primary"
                >
                  Clear
                </button>
              )}
              <span className="text-xs text-text-secondary ml-auto">
                {results.length} result{results.length !== 1 ? 's' : ''}
              </span>
            </div>

            {loading ? (
              <div className="flex items-center justify-center py-12">
                <div className="w-5 h-5 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
              </div>
            ) : results.length === 0 ? (
              <div className="bg-card border border-border rounded-lg p-8 text-center text-text-secondary text-sm">
                {filterTicker
                  ? `No analyses for "${filterTicker.toUpperCase()}"`
                  : 'No analyses yet. Enter a ticker above to start.'}
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
                {results.map((r) => (
                  <ResultCard
                    key={r.id}
                    r={r}
                    selected={r.id === selectedId}
                    onClick={() => setSelectedId(r.id === selectedId ? null : r.id)}
                    onDelete={() => handleDelete(r.id)}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="lg:col-span-2">
            {selectedResult ? (
              <DeepDetail
                r={selectedResult}
                onClose={() => setSelectedId(null)}
                onReanalyze={handleReanalyze}
                analyzing={analyzingTicker === selectedResult.ticker}
              />
            ) : (
              <div className="bg-card border border-border rounded-lg p-8 text-center text-text-secondary text-sm">
                Click an analysis to view details
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
