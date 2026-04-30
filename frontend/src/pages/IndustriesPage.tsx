import { useState, useEffect, useCallback, useMemo } from 'react';
import type { IndustryRecommendation, IndustryDetail } from '../types';
import { getIndustries, getIndustryDetail } from '../utils/api';
import IndustryCard from '../components/IndustryCard';

const ACTION_COLOR: Record<string, string> = {
  STRONG_BUY: '#2ea043',
  BUY: '#56d364',
  HOLD: '#d29922',
  SELL: '#f85149',
  STRONG_SELL: '#da3633',
};

function TrendSparkline({ points }: { points: { date: string; score: number }[] }) {
  if (points.length < 2) {
    return (
      <div className="text-[11px] text-text-secondary italic">
        Not enough history yet ({points.length} pt{points.length === 1 ? '' : 's'}) — sparkline appears after 2+ runs.
      </div>
    );
  }
  const W = 260, H = 50;
  const min = Math.min(-20, ...points.map((p) => p.score));
  const max = Math.max(20, ...points.map((p) => p.score));
  const range = max - min || 1;
  const xOf = (i: number) => (points.length === 1 ? W / 2 : (i / (points.length - 1)) * W);
  const yOf = (s: number) => H - ((s - min) / range) * H;
  const zeroY = yOf(0);
  const d = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xOf(i).toFixed(1)},${yOf(p.score).toFixed(1)}`).join(' ');
  const last = points[points.length - 1].score;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-12">
      <line x1={0} y1={zeroY} x2={W} y2={zeroY} stroke="#4b5563" strokeDasharray="3 3" strokeWidth={0.5} />
      <path d={d} fill="none" stroke={last >= 0 ? '#2ea043' : '#f85149'} strokeWidth={1.5} />
      {points.map((p, i) => (
        <circle key={i} cx={xOf(i)} cy={yOf(p.score)} r={1.5} fill={p.score >= 0 ? '#2ea043' : '#f85149'} />
      ))}
    </svg>
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

      {/* 30d trend */}
      <div>
        <div className="text-[10px] text-text-secondary uppercase mb-1">30-day conviction trend</div>
        <TrendSparkline
          points={detail.history.map((h) => ({
            date: h.rec_date,
            score: Number(h.conviction_score),
          }))}
        />
      </div>

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
