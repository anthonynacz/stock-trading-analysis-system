import { useState, useEffect, useCallback, useMemo } from 'react';
import type {
  IndustryRecommendation,
  IndustryDetail,
  IndustryForwardPoint,
  IndustryHistoryPoint,
  IndustryTopComponent,
} from '../types';
import { getIndustries, getIndustryDetail } from '../utils/api';
import IndustryCard from '../components/IndustryCard';

const ACTION_COLOR: Record<string, string> = {
  STRONG_BUY: '#2ea043',
  BUY: '#56d364',
  HOLD: '#d29922',
  SELL: '#f85149',
  STRONG_SELL: '#da3633',
};

type ChartPoint = {
  date: string;       // ISO yyyy-mm-dd
  score: number;
  offset: number;     // calendar days relative to today (negative = past, 0 = today, positive = forward)
  forecast: boolean;
};

function daysBetween(a: Date, b: Date): number {
  const ms = b.getTime() - a.getTime();
  return Math.round(ms / 86_400_000);
}

function fmtOffset(off: number): string {
  if (off === 0) return 'Today';
  return off > 0 ? `T+${off}d` : `T${off}d`;
}

function ConvictionChart({
  history,
  forward,
}: {
  history: IndustryHistoryPoint[];
  forward: IndustryForwardPoint[];
}) {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const histPoints: ChartPoint[] = history.map((h) => {
    const d = new Date(h.rec_date);
    return {
      date: h.rec_date,
      score: Number(h.conviction_score),
      offset: daysBetween(today, d),
      forecast: false,
    };
  });
  const fwdPoints: ChartPoint[] = forward.map((f) => ({
    date: f.forecast_date,
    score: Number(f.conviction_score),
    offset: f.day_offset,
    forecast: true,
  }));

  const points = [...histPoints, ...fwdPoints].sort((a, b) => a.offset - b.offset);

  if (points.length < 2) {
    return (
      <div className="text-[11px] text-text-secondary italic">
        Not enough history yet ({points.length} pt
        {points.length === 1 ? '' : 's'}) — chart appears after 2+ runs.
      </div>
    );
  }

  const W = 320;
  const H = 90;
  const PADDING_X = 4;
  const PADDING_TOP = 4;
  const PADDING_BOTTOM = 14;
  const innerW = W - 2 * PADDING_X;
  const innerH = H - PADDING_TOP - PADDING_BOTTOM;

  const minOff = Math.min(...points.map((p) => p.offset));
  const maxOff = Math.max(...points.map((p) => p.offset));
  const offSpan = Math.max(1, maxOff - minOff);

  const minScore = Math.min(-20, ...points.map((p) => p.score));
  const maxScore = Math.max(20, ...points.map((p) => p.score));
  const scoreSpan = Math.max(1, maxScore - minScore);

  const xOf = (off: number) => PADDING_X + ((off - minOff) / offSpan) * innerW;
  const yOf = (s: number) => PADDING_TOP + (1 - (s - minScore) / scoreSpan) * innerH;
  const zeroY = yOf(0);
  const todayX = xOf(0);

  // Split into history segment (offset <= 0) and forward segment (offset >= 0).
  // We include offset=0 (today) in BOTH so the line connects through it.
  const histSeg = points.filter((p) => p.offset <= 0);
  const fwdSegRaw = points.filter((p) => p.offset >= 0);
  // If there's no anchor at exactly offset=0 in history, take the last hist
  // point and prepend it to the forward seg so the dashed line continues from
  // the last actual reading.
  const fwdSeg = fwdSegRaw;
  if (histSeg.length > 0 && fwdSegRaw.length > 0 && fwdSegRaw[0].offset !== 0) {
    fwdSeg.unshift(histSeg[histSeg.length - 1]);
  }

  const buildPath = (seg: ChartPoint[]) =>
    seg.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xOf(p.offset).toFixed(1)},${yOf(p.score).toFixed(1)}`).join(' ');

  const histPath = histSeg.length >= 2 ? buildPath(histSeg) : '';
  const fwdPath = fwdSeg.length >= 2 ? buildPath(fwdSeg) : '';

  // Decide axis tick offsets — first, last, today (if in range), midpoint.
  const tickOffsets = Array.from(
    new Set(
      [
        minOff,
        Math.round((minOff + maxOff) / 2),
        0,
        maxOff,
      ].filter((o) => o >= minOff && o <= maxOff),
    ),
  ).sort((a, b) => a - b);

  const lastHist = histSeg[histSeg.length - 1];
  const lastFwd = fwdSegRaw[fwdSegRaw.length - 1];

  return (
    <div>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-24">
        {/* Zero line */}
        <line
          x1={PADDING_X}
          y1={zeroY}
          x2={W - PADDING_X}
          y2={zeroY}
          stroke="#4b5563"
          strokeDasharray="3 3"
          strokeWidth={0.5}
        />
        {/* Today separator */}
        {minOff <= 0 && maxOff >= 0 && (
          <>
            <line
              x1={todayX}
              y1={PADDING_TOP}
              x2={todayX}
              y2={H - PADDING_BOTTOM}
              stroke="#a78bfa"
              strokeWidth={0.7}
              strokeDasharray="2 2"
            />
            <text
              x={todayX}
              y={PADDING_TOP + 8}
              fontSize="7"
              fill="#a78bfa"
              textAnchor={todayX < W / 2 ? 'start' : 'end'}
              dx={todayX < W / 2 ? 2 : -2}
            >
              Today
            </text>
          </>
        )}
        {/* History line */}
        {histPath && (
          <path d={histPath} fill="none" stroke={lastHist && lastHist.score >= 0 ? '#2ea043' : '#f85149'} strokeWidth={1.5} />
        )}
        {/* Forward line — dashed + slightly transparent */}
        {fwdPath && (
          <path
            d={fwdPath}
            fill="none"
            stroke={lastFwd && lastFwd.score >= 0 ? '#56d364' : '#fb7185'}
            strokeWidth={1.5}
            strokeDasharray="3 2"
            opacity={0.85}
          />
        )}
        {/* Points */}
        {points.map((p, i) => (
          <circle
            key={i}
            cx={xOf(p.offset)}
            cy={yOf(p.score)}
            r={p.offset === 0 ? 2.4 : 1.6}
            fill={p.score >= 0 ? '#2ea043' : '#f85149'}
            stroke={p.offset === 0 ? '#a78bfa' : 'none'}
            strokeWidth={p.offset === 0 ? 0.8 : 0}
            opacity={p.forecast && p.offset !== 0 ? 0.85 : 1}
          />
        ))}
        {/* X-axis offset labels */}
        {tickOffsets.map((off) => (
          <text
            key={off}
            x={xOf(off)}
            y={H - 3}
            fontSize="7"
            fill={off === 0 ? '#a78bfa' : '#8b949e'}
            textAnchor="middle"
          >
            {fmtOffset(off)}
          </text>
        ))}
      </svg>
      {/* Legend */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-1 text-[10px] text-text-secondary">
        <span className="flex items-center gap-1">
          <span className="inline-block w-3 h-px bg-green-500" />
          <span>History</span>
          {histSeg.length > 0 && (
            <span className="text-text-primary font-mono">
              {fmtOffset(histSeg[0].offset)} → {fmtOffset(histSeg[histSeg.length - 1].offset)}
            </span>
          )}
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block w-3 h-px"
            style={{
              borderTop: '1px dashed #56d364',
            }}
          />
          <span>Forecast</span>
          {fwdSegRaw.length > 0 && (
            <span className="text-text-primary font-mono">
              {fmtOffset(fwdSegRaw[0].offset)} → {fmtOffset(fwdSegRaw[fwdSegRaw.length - 1].offset)}
            </span>
          )}
        </span>
        <span className="flex items-center gap-1">
          <span
            className="inline-block w-px h-3"
            style={{ borderLeft: '1px dashed #a78bfa' }}
          />
          <span className="text-purple-300">Today (T0)</span>
        </span>
      </div>
    </div>
  );
}

function ExecutiveSummaryPanel({ summary }: { summary: string | null }) {
  if (!summary) return null;
  return (
    <div className="rounded-md border border-purple-500/25 bg-purple-950/20 p-3">
      <div className="text-[10px] font-semibold text-purple-300 uppercase tracking-wider mb-1">
        Executive Summary
      </div>
      <p className="text-xs text-text-primary leading-relaxed">{summary}</p>
    </div>
  );
}

function fmtCap(v: number | null): string {
  if (v == null || !isFinite(v) || v <= 0) return '—';
  if (v >= 1_000_000_000_000) return `$${(v / 1_000_000_000_000).toFixed(2)}T`;
  if (v >= 1_000_000_000) return `$${(v / 1_000_000_000).toFixed(1)}B`;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(0)}M`;
  return `$${v.toLocaleString()}`;
}

function TopComponentsPanel({ items }: { items: IndustryTopComponent[] }) {
  if (items.length === 0) {
    return (
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-1">Top Components</div>
        <p className="text-xs text-text-secondary italic">No representative tickers available.</p>
      </div>
    );
  }
  return (
    <div>
      <div className="text-[10px] text-text-secondary uppercase mb-2">
        Top Components ({items.length})
      </div>
      <div className="space-y-1.5">
        {items.map((c) => {
          const tc = ACTION_COLOR[c.action] ?? '#8b949e';
          const conv = Number(c.conviction);
          return (
            <div
              key={c.ticker}
              className="rounded border border-border bg-page/60 p-2 space-y-0.5"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 min-w-0">
                  <span className="text-xs font-bold text-text-primary">{c.ticker}</span>
                  {c.company_name && (
                    <span className="text-[10px] text-text-secondary truncate">
                      {c.company_name}
                    </span>
                  )}
                </div>
                <span
                  className="text-[10px] font-bold uppercase px-1.5 py-px rounded"
                  style={{ color: tc, background: `${tc}1a` }}
                >
                  {c.action.replace('_', ' ')} {conv >= 0 ? '+' : ''}
                  {conv.toFixed(0)}
                </span>
              </div>
              <div className="grid grid-cols-4 gap-x-2 text-[10px] font-mono tabular-nums">
                <CompMini label="Px" value={c.price != null ? `$${c.price.toFixed(2)}` : '—'} />
                <CompMini label="Cap" value={fmtCap(c.market_cap)} />
                <CompMini label="P/E" value={c.pe_ratio != null ? c.pe_ratio.toFixed(1) : '—'} />
                <CompMini
                  label="vs 52H"
                  value={
                    c.pct_from_52w_high != null
                      ? `${c.pct_from_52w_high >= 0 ? '+' : ''}${c.pct_from_52w_high.toFixed(1)}%`
                      : '—'
                  }
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function CompMini({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-text-secondary font-sans">{label}</span>
      <span className="text-text-primary">{value}</span>
    </div>
  );
}

function DetailPanel({ detail, loading }: { detail: IndustryDetail | null; loading: boolean }) {
  if (loading) {
    return (
      <div className="bg-card border border-border rounded-lg p-8 text-center text-sm text-text-secondary">
        <div className="w-4 h-4 border-2 border-text-secondary border-t-transparent rounded-full animate-spin mx-auto mb-2" />
        Loading…
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="bg-card border border-border rounded-lg p-8 text-center text-sm text-text-secondary">
        Select an industry to view breakdown.
      </div>
    );
  }
  const latest = detail.latest;
  const conv = Number(latest.conviction_score);

  return (
    <div className="bg-card border border-border rounded-lg p-4 space-y-4 sticky top-4">
      <div>
        <div className="flex items-center justify-between gap-2 mb-1">
          <h3 className="text-lg font-bold text-text-primary">{detail.industry}</h3>
          <span
            className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded"
            style={{
              color: ACTION_COLOR[latest.action],
              background: `${ACTION_COLOR[latest.action]}22`,
              border: `1px solid ${ACTION_COLOR[latest.action]}55`,
            }}
          >
            {latest.action.replace('_', ' ')}
          </span>
        </div>
        <p className="text-xs text-text-secondary">
          Conviction {conv >= 0 ? '+' : ''}{conv.toFixed(0)} · {latest.signal_count} signals ·{' '}
          {latest.member_count ?? 0} members
        </p>
      </div>

      {/* Executive summary */}
      <ExecutiveSummaryPanel summary={detail.executive_summary} />

      {/* Conviction trend with 5-day forward outlook */}
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-1">
          Conviction trend · 30d history + 5d forecast
        </div>
        <ConvictionChart history={detail.history} forward={detail.forward_outlook} />
      </div>

      {/* Top components with key financials */}
      <TopComponentsPanel items={detail.top_components} />

      {latest.rationale && (
        <div>
          <div className="text-[10px] text-text-secondary uppercase mb-1">Rationale</div>
          <p className="text-xs text-text-primary leading-snug">{latest.rationale}</p>
        </div>
      )}

      {/* Observations */}
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-2">Observations</div>
        <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
          <Obs label="Bullish members" value={`${latest.bullish_count ?? 0}/${latest.member_count ?? 0}`} />
          <Obs label="Bearish members" value={`${latest.bearish_count ?? 0}/${latest.member_count ?? 0}`} />
          <Obs
            label="% above 50d SMA"
            value={latest.breadth_above_50d_pct !== null ? `${Number(latest.breadth_above_50d_pct).toFixed(0)}%` : '—'}
          />
          <Obs
            label="Cap-weighted conv"
            value={latest.cap_weighted_conviction !== null ? `${Number(latest.cap_weighted_conviction).toFixed(0)}` : '—'}
          />
          <Obs label={`${latest.etf_symbol ?? 'ETF'} RSI`} value={latest.etf_rsi_14 !== null ? `${Number(latest.etf_rsi_14).toFixed(0)}` : '—'} />
          <Obs
            label={`${latest.etf_symbol ?? 'ETF'} 20d mom`}
            value={latest.etf_momentum_20d !== null ? `${(Number(latest.etf_momentum_20d) * 100).toFixed(1)}%` : '—'}
          />
          <Obs label="News sentiment" value={latest.avg_news_sentiment !== null ? `${Number(latest.avg_news_sentiment).toFixed(2)}` : '—'} />
          <Obs label="Articles" value={(latest.news_article_count ?? 0).toString()} />
          <Obs label="Geo impact" value={latest.geopolitical_points !== null ? `${Number(latest.geopolitical_points).toFixed(0)}` : '—'} />
          <Obs label="Catalysts (14d)" value={(latest.active_catalyst_count ?? 0).toString()} />
        </div>
      </div>

      {/* Signals */}
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-1">
          Signals ({latest.signals?.length ?? 0})
        </div>
        {!latest.signals || latest.signals.length === 0 ? (
          <p className="text-xs text-text-secondary italic">No signals firing.</p>
        ) : (
          <ul className="space-y-1">
            {latest.signals.map((s, i) => {
              const c = s.points >= 0 ? '#2ea043' : '#f85149';
              return (
                <li key={i} className="text-[11px]">
                  <div className="flex items-center justify-between gap-2">
                    <span className="font-semibold text-text-primary">{s.signal}</span>
                    <span className="font-mono tabular-nums" style={{ color: c }}>
                      {s.points >= 0 ? '+' : ''}
                      {s.points}
                    </span>
                  </div>
                  <p className="text-text-secondary leading-snug">{s.detail}</p>
                </li>
              );
            })}
          </ul>
        )}
      </div>

      {/* Members */}
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-1">
          Member recommendations ({detail.members.length})
        </div>
        {detail.members.length === 0 ? (
          <p className="text-xs text-text-secondary italic">No member recommendations today.</p>
        ) : (
          <div className="flex flex-wrap gap-1">
            {detail.members.map((m) => {
              const tc = ACTION_COLOR[m.action] ?? '#8b949e';
              return (
                <span
                  key={m.id}
                  className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                  style={{ color: tc, background: `${tc}1a` }}
                  title={`${m.ticker}: ${m.action} (${Number(m.conviction_score).toFixed(0)})`}
                >
                  {m.ticker} {Number(m.conviction_score) > 0 ? '+' : ''}
                  {Number(m.conviction_score).toFixed(0)}
                </span>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function Obs({ label, value }: { label: string; value: string }) {
  return (
    <>
      <span className="text-text-secondary">{label}</span>
      <span className="font-mono text-right tabular-nums text-text-primary">{value}</span>
    </>
  );
}

export default function IndustriesPage() {
  const [industries, setIndustries] = useState<IndustryRecommendation[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<IndustryDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchList = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getIndustries();
      setIndustries(d);
      if (!selected && d.length > 0) {
        setSelected(d[0].industry);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load industries');
    } finally {
      setLoading(false);
    }
  }, [selected]);

  useEffect(() => {
    fetchList();
  }, [fetchList]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    (async () => {
      setDetailLoading(true);
      try {
        const d = await getIndustryDetail(selected);
        if (!cancelled) setDetail(d);
      } catch {
        if (!cancelled) setDetail(null);
      } finally {
        if (!cancelled) setDetailLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const latestDate = industries[0]?.rec_date;

  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const i of industries) out[i.action] = (out[i.action] ?? 0) + 1;
    return out;
  }, [industries]);

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-7xl mx-auto px-4 py-6 space-y-4">
        <div>
          <h1 className="text-2xl font-bold mb-1">Industries</h1>
          <p className="text-sm text-text-secondary">
            Sector-level BUY/SELL recommendations. Each card is one industry,
            scored on its own signal stack — breadth, cap-weighted conviction,
            sector ETF technicals, aggregated news sentiment, geopolitical
            impact, and catalyst density.
            {latestDate && (
              <span className="ml-1 text-text-primary">Run: {latestDate}.</span>
            )}
          </p>
          {industries.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-x-3 text-[11px] text-text-secondary">
              {(['STRONG_BUY', 'BUY', 'HOLD', 'SELL', 'STRONG_SELL'] as const).map((a) => (
                <span key={a}>
                  <span className="font-semibold" style={{ color: ACTION_COLOR[a] }}>
                    {a.replace('_', ' ')}
                  </span>
                  : {counts[a] ?? 0}
                </span>
              ))}
            </div>
          )}
        </div>

        {/* Grid + detail */}
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
          <div className="lg:col-span-3">
            {loading ? (
              <div className="flex items-center justify-center py-16 text-sm text-text-secondary">
                <div className="w-4 h-4 border-2 border-text-secondary border-t-transparent rounded-full animate-spin mr-2" />
                Loading…
              </div>
            ) : error ? (
              <div className="bg-red-900/20 border border-red-900/40 rounded p-3 text-sm text-red-300">
                {error}
              </div>
            ) : industries.length === 0 ? (
              <div className="bg-card border border-border rounded-lg p-8 text-center text-sm text-text-secondary">
                No industry recommendations yet. Run the pipeline (Dashboard → Refresh)
                and the <span className="text-text-primary">industries</span> phase will populate this view.
              </div>
            ) : (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {industries.map((i) => (
                  <IndustryCard
                    key={i.id}
                    item={i}
                    selected={selected === i.industry}
                    onClick={() => setSelected(i.industry)}
                  />
                ))}
              </div>
            )}
          </div>
          <div className="lg:col-span-2">
            <DetailPanel detail={detail} loading={detailLoading} />
          </div>
        </div>
      </div>
    </div>
  );
}
