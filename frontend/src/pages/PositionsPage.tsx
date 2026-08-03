import { useState, useCallback, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import type { Position, PositionCreateRequest, PositionType, SignalDetail } from '../types';
import { usePositions } from '../hooks/useEdgeFlow';
import {
  createPosition,
  closePosition,
  deletePosition,
  getPnlHistory,
  refreshAllPositions,
  refreshPositionPrice,
  updatePosition,
  analyzeResearch,
  type PnlHistory,
} from '../utils/api';
import { ACTION_COLORS, getActionLabel } from '../utils/theme';

/* ── Helpers ────────────────────────────────────────────────────────────── */

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function formatCurrency(n: number | null | undefined): string {
  if (n == null) return '—';
  return `$${n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function pnlColor(pnl: number | null): string {
  if (pnl == null || pnl === 0) return '#8b949e';
  return pnl > 0 ? '#2ea043' : '#f85149';
}

const TYPE_COLORS: Record<string, string> = {
  CALL: '#2ea043',
  PUT: '#f85149',
  STOCK: '#58a6ff',
};

/* ── P&L History (day / month rollup) ────────────────────────────────── */

function formatMonth(yyyymm: string): string {
  // yyyymm like "2026-05"
  const [y, m] = yyyymm.split('-').map(Number);
  const d = new Date(y, (m ?? 1) - 1, 1);
  return d.toLocaleString('en-US', { month: 'long', year: 'numeric' });
}

function formatDay(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
}

function PnlHistorySection() {
  const [data, setData] = useState<PnlHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<'day' | 'month'>('day');

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const h = await getPnlHistory(90);
        if (!cancelled) {
          setData(h);
        }
      } catch {
        if (!cancelled) setData({ daily: [], monthly: [] });
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const interval = setInterval(load, 5 * 60_000); // 5 min
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (loading) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <div className="text-sm text-text-secondary">Loading P&L history…</div>
      </div>
    );
  }
  if (!data || (data.daily.length === 0 && data.monthly.length === 0)) {
    return (
      <div className="bg-card border border-border rounded-lg p-4">
        <div className="text-sm font-bold text-text-primary mb-1">P&L over time</div>
        <p className="text-xs text-text-secondary">
          No snapshots yet. The first row is written tonight at 16:45 ET after market close,
          and one row per weekday thereafter.
        </p>
      </div>
    );
  }

  const rows = view === 'day' ? data.daily : data.monthly;

  return (
    <div className="bg-card border border-border rounded-lg p-3 sm:p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-text-primary">P&L over time</h3>
        <div className="flex gap-1">
          {(['day', 'month'] as const).map((v) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={`px-3 py-1.5 sm:px-2.5 sm:py-1 text-[11px] font-semibold rounded transition-colors ${
                view === v
                  ? 'bg-accent-900/60 text-accent-300 border border-accent-600/60'
                  : 'bg-page/60 border border-border text-text-secondary hover:text-text-primary'
              }`}
            >
              {v === 'day' ? 'Daily' : 'Monthly'}
            </button>
          ))}
        </div>
      </div>

      <ul className="divide-y divide-border max-h-72 overflow-y-auto">
        {rows.length === 0 && (
          <li className="py-3 text-xs text-text-secondary text-center">No {view} data.</li>
        )}
        {view === 'day' && (data.daily ?? []).map((d) => {
          const color = pnlColor(d.realized_pnl_today);
          return (
            <li key={d.date} className="py-2 flex items-center gap-3">
              <span className="text-xs font-mono tabular-nums text-text-primary w-24 shrink-0">
                {formatDay(d.date)}
              </span>
              <span
                className="text-xs font-mono tabular-nums font-semibold w-24 shrink-0 text-right"
                style={{ color }}
                title="Realized P&L from positions closed this day"
              >
                {d.realized_pnl_today >= 0 ? '+' : ''}{formatCurrency(d.realized_pnl_today)}
              </span>
              <span className="text-[10px] text-text-secondary flex-1 min-w-0 truncate">
                {d.closed_count_today > 0 && `${d.closed_count_today} closed · `}
                {d.open_count} open
              </span>
              <span
                className="text-[10px] font-mono tabular-nums text-text-secondary w-20 shrink-0 text-right"
                title="Unrealized P&L at snapshot time"
              >
                {d.unrealized_pnl >= 0 ? '+' : ''}{formatCurrency(d.unrealized_pnl)} U
              </span>
            </li>
          );
        })}
        {view === 'month' && (data.monthly ?? []).map((m) => (
          <li key={m.month} className="py-2 flex items-center gap-3">
            <span className="text-xs font-medium text-text-primary w-28 shrink-0">
              {formatMonth(m.month)}
            </span>
            <span
              className="text-xs font-mono tabular-nums font-semibold w-24 shrink-0 text-right"
              style={{ color: pnlColor(m.realized_pnl) }}
              title="Total realized P&L closed in this month"
            >
              {m.realized_pnl >= 0 ? '+' : ''}{formatCurrency(m.realized_pnl)}
            </span>
            <span className="text-[10px] text-text-secondary flex-1 min-w-0 truncate">
              {m.closed_count > 0 ? `${m.closed_count} closed` : 'no closures'}
            </span>
            <span
              className="text-[10px] font-mono tabular-nums text-text-secondary w-20 shrink-0 text-right"
              title="Unrealized at the latest snapshot in this month"
            >
              {m.latest_unrealized >= 0 ? '+' : ''}{formatCurrency(m.latest_unrealized)} U
            </span>
          </li>
        ))}
      </ul>
      <p className="text-[10px] text-text-secondary mt-3">
        Snapshots written nightly at 16:45 ET. <strong>U</strong> = unrealized at snapshot time;
        the bold number is realized P&L closed in the period.
      </p>
    </div>
  );
}


/* ── Summary Bar ────────────────────────────────────────────────────────── */

function SummaryBar({ positions }: { positions: Position[] }) {
  const open = positions.filter((p) => p.status === 'OPEN');
  const totalPnl = open.reduce((s, p) => s + (p.unrealized_pnl ?? 0), 0);
  const winners = open.filter((p) => (p.unrealized_pnl ?? 0) > 0).length;
  const losers = open.filter((p) => (p.unrealized_pnl ?? 0) < 0).length;

  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
      <div className="bg-card border border-border rounded-lg p-3 text-center">
        <div className="text-xs text-text-secondary mb-1">Open Positions</div>
        <div className="text-xl font-bold text-text-primary">{open.length}</div>
      </div>
      <div className="bg-card border border-border rounded-lg p-3 text-center">
        <div className="text-xs text-text-secondary mb-1">Unrealized P&L</div>
        <div className="text-xl font-bold" style={{ color: pnlColor(totalPnl) }}>
          {totalPnl >= 0 ? '+' : ''}{formatCurrency(totalPnl)}
        </div>
      </div>
      <div className="bg-card border border-border rounded-lg p-3 text-center">
        <div className="text-xs text-text-secondary mb-1">Winning</div>
        <div className="text-xl font-bold text-green-400">{winners}</div>
      </div>
      <div className="bg-card border border-border rounded-lg p-3 text-center">
        <div className="text-xs text-text-secondary mb-1">Losing</div>
        <div className="text-xl font-bold text-red-400">{losers}</div>
      </div>
    </div>
  );
}

/* ── Add Position Form ──────────────────────────────────────────────────── */

interface AddFormProps {
  initial?: Partial<PositionCreateRequest>;
  onSubmit: (data: PositionCreateRequest) => Promise<void>;
  onCancel: () => void;
}

function AddPositionForm({ initial, onSubmit, onCancel }: AddFormProps) {
  const [ticker, setTicker] = useState(initial?.ticker ?? '');
  const [posType, setPosType] = useState<PositionType>(initial?.position_type ?? 'CALL');
  const [quantity, setQuantity] = useState(initial?.quantity ?? 1);
  const [entryPrice, setEntryPrice] = useState(initial?.entry_price?.toString() ?? '');
  const [strikePrice, setStrikePrice] = useState(initial?.strike_price?.toString() ?? '');
  const [premiumPaid, setPremiumPaid] = useState(initial?.premium_paid?.toString() ?? '');
  const [expiry, setExpiry] = useState(initial?.expiry ?? '');
  const [stopLoss, setStopLoss] = useState(initial?.stop_loss?.toString() ?? '');
  const [targetPrice, setTargetPrice] = useState(initial?.target_price?.toString() ?? '');
  const [notes, setNotes] = useState(initial?.notes ?? '');
  const [submitting, setSubmitting] = useState(false);

  const isOption = posType === 'CALL' || posType === 'PUT';

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ticker.trim() || !entryPrice) return;
    setSubmitting(true);
    try {
      const data: PositionCreateRequest = {
        ticker: ticker.trim().toUpperCase(),
        position_type: posType,
        quantity,
        entry_price: parseFloat(entryPrice),
      };
      if (isOption && strikePrice) data.strike_price = parseFloat(strikePrice);
      if (isOption && premiumPaid) data.premium_paid = parseFloat(premiumPaid);
      if (isOption && expiry) data.expiry = expiry;
      if (stopLoss) data.stop_loss = parseFloat(stopLoss);
      if (targetPrice) data.target_price = parseFloat(targetPrice);
      if (notes.trim()) data.notes = notes.trim();
      // Carried from the "Open Position" deep-link — links this position to
      // the recommendation that prompted it (outcome attribution). Dropped
      // if the user typed a different ticker than the rec's.
      if (
        initial?.recommendation_id != null &&
        ticker.trim().toUpperCase() === (initial.ticker ?? '').toUpperCase()
      ) {
        data.recommendation_id = initial.recommendation_id;
      }
      await onSubmit(data);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="bg-card border border-border rounded-lg p-4 mb-6 space-y-3">
      <div className="flex items-center justify-between mb-2">
        <h3 className="text-sm font-semibold text-text-primary">New Position</h3>
        <button type="button" onClick={onCancel} className="text-xs text-text-secondary hover:text-text-primary">
          Cancel
        </button>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div>
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Ticker</label>
          <input
            type="text"
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary placeholder-text-secondary focus:outline-none focus:border-accent-500"
            placeholder="NVDA"
            required
          />
        </div>
        <div>
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Type</label>
          <div className="flex gap-1">
            {(['CALL', 'PUT', 'STOCK'] as const).map((t) => (
              <button
                key={t}
                type="button"
                onClick={() => setPosType(t)}
                className={`flex-1 px-2 py-1.5 text-xs font-semibold rounded transition-colors ${
                  posType === t
                    ? 'text-white'
                    : 'bg-page border border-border text-text-secondary hover:text-text-primary'
                }`}
                style={posType === t ? { backgroundColor: TYPE_COLORS[t] + 'cc' } : undefined}
              >
                {t}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Quantity</label>
          <input
            type="number"
            min={1}
            value={quantity}
            onChange={(e) => setQuantity(parseInt(e.target.value) || 1)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
          />
        </div>
        <div>
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Entry Price</label>
          <input
            type="number"
            step="0.01"
            value={entryPrice}
            onChange={(e) => setEntryPrice(e.target.value)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
            placeholder="0.00"
            required
          />
        </div>
      </div>

      {isOption && (
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          <div>
            <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Strike Price</label>
            <input
              type="number"
              step="0.01"
              value={strikePrice}
              onChange={(e) => setStrikePrice(e.target.value)}
              className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
              placeholder="0.00"
            />
          </div>
          <div>
            <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Premium Paid</label>
            <input
              type="number"
              step="0.01"
              value={premiumPaid}
              onChange={(e) => setPremiumPaid(e.target.value)}
              className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
              placeholder="Per contract"
            />
          </div>
          <div>
            <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Expiry</label>
            <input
              type="date"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
            />
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
        <div>
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Stop Loss</label>
          <input
            type="number"
            step="0.01"
            value={stopLoss}
            onChange={(e) => setStopLoss(e.target.value)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
            placeholder="Optional"
          />
        </div>
        <div>
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Target Price</label>
          <input
            type="number"
            step="0.01"
            value={targetPrice}
            onChange={(e) => setTargetPrice(e.target.value)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
            placeholder="Optional"
          />
        </div>
        <div className="col-span-2 md:col-span-1">
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Notes</label>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
            placeholder="Optional"
          />
        </div>
      </div>

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting || !ticker.trim() || !entryPrice}
          className="px-4 py-1.5 text-xs font-semibold rounded bg-accent-900/60 text-accent-400 hover:bg-accent-800/60 disabled:opacity-40 transition-colors"
        >
          {submitting ? 'Adding...' : 'Add Position'}
        </button>
      </div>
    </form>
  );
}

/* ── Position Card ──────────────────────────────────────────────────────── */

function PositionCard({
  position,
  selected,
  onClick,
}: {
  position: Position;
  selected: boolean;
  onClick: () => void;
}) {
  const pnl = position.status === 'OPEN' ? position.unrealized_pnl : position.realized_pnl;
  const pnlPct = position.status === 'OPEN' ? position.unrealized_pnl_pct : null;
  const isOption = position.position_type !== 'STOCK';
  const borderColor = pnlColor(pnl ?? null);

  return (
    <div
      onClick={onClick}
      className={`bg-card border rounded-lg px-3 py-2.5 cursor-pointer transition-colors hover:border-text-secondary/40 ${
        selected ? 'ring-1 ring-accent-500/60' : ''
      }`}
      style={{ borderLeftWidth: 3, borderLeftColor: borderColor }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-text-primary">{position.ticker}</span>
          <span
            className="px-1.5 py-px rounded text-[9px] font-bold"
            style={{ backgroundColor: TYPE_COLORS[position.position_type] + '22', color: TYPE_COLORS[position.position_type] }}
          >
            {position.position_type}
          </span>
          <span className="text-[10px] text-text-secondary">
            x{position.quantity}
          </span>
        </div>
        {position.is_on_watchlist && (
          <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400 font-medium">
            Watchlist
          </span>
        )}
      </div>

      <div className="flex items-center justify-between text-xs">
        <div className="text-text-secondary">
          Entry: <span className="text-text-primary font-mono">{formatCurrency(position.entry_price)}</span>
          {isOption && position.strike_price != null && (
            <span className="ml-2">
              Strike: <span className="text-text-primary font-mono">{formatCurrency(position.strike_price)}</span>
            </span>
          )}
        </div>
        <div className="text-right">
          {pnl != null && (
            <span className="font-mono font-semibold" style={{ color: pnlColor(pnl) }}>
              {pnl >= 0 ? '+' : ''}{formatCurrency(pnl)}
              {pnlPct != null && (
                <span className="text-[10px] ml-1">({pnlPct >= 0 ? '+' : ''}{pnlPct.toFixed(1)}%)</span>
              )}
            </span>
          )}
        </div>
      </div>

      {isOption && (
        <div className="flex items-center gap-3 mt-1 text-[10px] text-text-secondary">
          {position.expiry && (
            <span>
              Exp: {formatDate(position.expiry)}
            </span>
          )}
          {position.days_to_expiry != null && position.status === 'OPEN' && (
            <span
              className={`px-1 py-px rounded font-medium ${
                position.days_to_expiry <= 3
                  ? 'bg-amber-900/60 text-amber-400'
                  : position.days_to_expiry <= 7
                    ? 'bg-amber-900/30 text-amber-400'
                    : 'bg-border text-text-secondary'
              }`}
            >
              {position.days_to_expiry}d left
            </span>
          )}
          {position.premium_paid != null && (
            <span>Premium: {formatCurrency(position.premium_paid)}</span>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Position Detail Panel ──────────────────────────────────────────────── */

function SignalBullet({ signal }: { signal: SignalDetail }) {
  const isPositive = signal.points > 0;
  return (
    <li className="text-[10px] leading-tight">
      <div className="flex items-start gap-1">
        <span className={`font-mono font-bold shrink-0 w-6 text-right ${isPositive ? 'text-green-400' : 'text-red-400'}`}>
          {isPositive ? '+' : ''}{signal.points}
        </span>
        <span className="text-text-primary">{signal.signal}</span>
      </div>
    </li>
  );
}

function PositionDetail({
  position,
  onClose,
  onDelete,
  onRefresh,
  onUpdate,
  onAnalyzeDone,
}: {
  position: Position;
  onClose: (id: number, closePrice: number, notes?: string) => Promise<void>;
  onDelete: (id: number) => Promise<void>;
  onRefresh: (id: number) => Promise<void>;
  onUpdate: (id: number, data: Record<string, unknown>) => Promise<void>;
  onAnalyzeDone: () => void;
}) {
  const [closePrice, setClosePrice] = useState('');
  const [closeNotes, setCloseNotes] = useState('');
  const [closing, setClosing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisError, setAnalysisError] = useState<string | null>(null);
  const [editNotes, setEditNotes] = useState(position.notes ?? '');
  const [notesChanged, setNotesChanged] = useState(false);

  useEffect(() => {
    setEditNotes(position.notes ?? '');
    setNotesChanged(false);
    setClosePrice('');
    setCloseNotes('');
  }, [position.id, position.notes]);

  const rec = position.recommendation;
  const isOption = position.position_type !== 'STOCK';

  const handleClose = async () => {
    if (!closePrice) return;
    setClosing(true);
    try {
      await onClose(position.id, parseFloat(closePrice), closeNotes || undefined);
    } finally {
      setClosing(false);
    }
  };

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      await onRefresh(position.id);
    } finally {
      setRefreshing(false);
    }
  };

  const handleAnalyze = async () => {
    setAnalyzing(true);
    setAnalysisError(null);
    try {
      await analyzeResearch(position.ticker);
      onAnalyzeDone();
    } catch {
      setAnalysisError('Analysis failed. Try again.');
    } finally {
      setAnalyzing(false);
    }
  };

  const handleSaveNotes = async () => {
    await onUpdate(position.id, { notes: editNotes });
    setNotesChanged(false);
  };

  return (
    <div className="bg-card border border-border rounded-lg overflow-hidden sticky top-24 max-h-[calc(100vh-7rem)] overflow-y-auto">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-text-primary">{position.ticker}</span>
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-bold"
              style={{ backgroundColor: TYPE_COLORS[position.position_type] + '22', color: TYPE_COLORS[position.position_type] }}
            >
              {position.position_type}
            </span>
            {position.status === 'CLOSED' && (
              <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-border text-text-secondary">
                CLOSED
              </span>
            )}
          </div>
          <button
            onClick={() => onDelete(position.id)}
            className="text-xs text-text-secondary hover:text-red-400 transition-colors"
            title="Delete position"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        </div>
        {position.company_name && (
          <p className="text-xs text-text-secondary mt-0.5">{position.company_name}</p>
        )}
      </div>

      <div className="px-4 py-3 space-y-4">
        {/* Position details */}
        <div className="grid grid-cols-2 gap-x-4 gap-y-1.5 text-xs">
          <div className="flex justify-between">
            <span className="text-text-secondary">Quantity</span>
            <span className="text-text-primary font-mono">{position.quantity}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-text-secondary">Entry Price</span>
            <span className="text-text-primary font-mono">{formatCurrency(position.entry_price)}</span>
          </div>
          {isOption && position.strike_price != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Strike</span>
              <span className="text-text-primary font-mono">{formatCurrency(position.strike_price)}</span>
            </div>
          )}
          {isOption && position.premium_paid != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Premium</span>
              <span className="text-text-primary font-mono">{formatCurrency(position.premium_paid)}</span>
            </div>
          )}
          {position.current_price != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Current</span>
              <span className="text-text-primary font-mono">{formatCurrency(position.current_price)}</span>
            </div>
          )}
          {position.stop_loss != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Stop Loss</span>
              <span className="text-red-400 font-mono">{formatCurrency(position.stop_loss)}</span>
            </div>
          )}
          {position.target_price != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Target</span>
              <span className="text-green-400 font-mono">{formatCurrency(position.target_price)}</span>
            </div>
          )}
          {isOption && position.expiry && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Expiry</span>
              <span className="text-text-primary">{formatDate(position.expiry)}</span>
            </div>
          )}
          {position.days_to_expiry != null && position.status === 'OPEN' && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Days Left</span>
              <span className={position.days_to_expiry <= 3 ? 'text-amber-400 font-semibold' : 'text-text-primary'}>
                {position.days_to_expiry}
              </span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-text-secondary">Opened</span>
            <span className="text-text-primary">{formatDate(position.opened_at)}</span>
          </div>
        </div>

        {/* P&L */}
        {position.status === 'OPEN' && position.unrealized_pnl != null && (
          <div className="bg-page border border-border rounded-lg p-3 text-center">
            <div className="text-[10px] text-text-secondary mb-1">Unrealized P&L</div>
            <div className="text-lg font-bold font-mono" style={{ color: pnlColor(position.unrealized_pnl) }}>
              {position.unrealized_pnl >= 0 ? '+' : ''}{formatCurrency(position.unrealized_pnl)}
              {position.unrealized_pnl_pct != null && (
                <span className="text-sm ml-2">
                  ({position.unrealized_pnl_pct >= 0 ? '+' : ''}{position.unrealized_pnl_pct.toFixed(1)}%)
                </span>
              )}
            </div>
          </div>
        )}

        {position.status === 'CLOSED' && position.realized_pnl != null && (
          <div className="bg-page border border-border rounded-lg p-3 text-center">
            <div className="text-[10px] text-text-secondary mb-1">Realized P&L</div>
            <div className="text-lg font-bold font-mono" style={{ color: pnlColor(position.realized_pnl) }}>
              {position.realized_pnl >= 0 ? '+' : ''}{formatCurrency(position.realized_pnl)}
            </div>
            {position.close_price != null && (
              <div className="text-[10px] text-text-secondary mt-1">
                Closed at {formatCurrency(position.close_price)} on {formatDate(position.closed_at)}
              </div>
            )}
          </div>
        )}

        {/* Refresh button (STOCK only, since option premium needs manual update) */}
        {position.status === 'OPEN' && position.position_type === 'STOCK' && (
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="w-full py-1.5 text-xs rounded bg-border text-text-primary hover:bg-text-secondary/20 disabled:opacity-40 transition-colors"
          >
            {refreshing ? 'Refreshing...' : 'Refresh Stock Price'}
          </button>
        )}

        {/* Recommendation overlay */}
        {rec && (
          <div className="border-t border-border/40 pt-3">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-2">
              EdgeFlow Recommendation
              <span className="text-[9px] font-normal ml-1 normal-case">({rec.recommendation_date})</span>
            </h4>
            <div className="flex items-center gap-2 mb-2">
              <span
                className="px-2 py-0.5 rounded text-xs font-bold"
                style={{ backgroundColor: (ACTION_COLORS[rec.action] ?? '#21262d') + '22', color: ACTION_COLORS[rec.action] }}
              >
                {getActionLabel(rec.action)}
              </span>
              {rec.conviction_score != null && (
                <span className={`text-xs font-mono font-semibold ${rec.conviction_score >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                  {rec.conviction_score > 0 ? '+' : ''}{rec.conviction_score}
                </span>
              )}
              {rec.risk_level && (
                <span className={`text-[9px] px-1 py-px rounded ${
                  rec.risk_level === 'LOW' ? 'bg-green-900/40 text-green-400' :
                  rec.risk_level === 'HIGH' ? 'bg-red-900/40 text-red-400' :
                  'bg-amber-900/40 text-amber-400'
                }`}>
                  {rec.risk_level}
                </span>
              )}
            </div>
            {rec.rationale && (
              <p className="text-[10px] text-text-secondary mb-2">{rec.rationale}</p>
            )}
            {rec.signals && rec.signals.length > 0 && (
              <ul className="space-y-0.5 mb-2">
                {rec.signals.slice(0, 8).map((s, i) => (
                  <SignalBullet key={i} signal={s} />
                ))}
                {rec.signals.length > 8 && (
                  <li className="text-[10px] text-text-secondary ml-7">+{rec.signals.length - 8} more</li>
                )}
              </ul>
            )}
            <div className="grid grid-cols-2 gap-2 text-[10px]">
              {rec.target_price != null && (
                <div className="flex justify-between">
                  <span className="text-text-secondary">Target</span>
                  <span className="text-green-400 font-mono">{formatCurrency(rec.target_price)}</span>
                </div>
              )}
              {rec.stop_loss_price != null && (
                <div className="flex justify-between">
                  <span className="text-text-secondary">Stop</span>
                  <span className="text-red-400 font-mono">{formatCurrency(rec.stop_loss_price)}</span>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Analyze button — always available for open positions without a recommendation */}
        {!rec && position.status === 'OPEN' && (
          <div className="border-t border-border/40 pt-3">
            <button
              onClick={handleAnalyze}
              disabled={analyzing}
              className="w-full py-1.5 text-xs rounded bg-accent-900/60 text-accent-400 hover:bg-accent-800/60 disabled:opacity-40 transition-colors"
            >
              {analyzing ? 'Analyzing (30-60s)...' : 'Run EdgeFlow Analysis'}
            </button>
            {analysisError && <p className="text-xs text-red-400 mt-1">{analysisError}</p>}
            <p className="text-[9px] text-text-secondary mt-1">
              {position.is_on_watchlist
                ? 'No recommendation yet. Run on-demand analysis.'
                : 'Not on watchlist. Run on-demand analysis via Research pipeline.'}
            </p>
          </div>
        )}

        {/* Notes */}
        <div className="border-t border-border/40 pt-3">
          <label className="block text-[10px] text-text-secondary uppercase tracking-wider mb-1">Notes</label>
          <textarea
            value={editNotes}
            onChange={(e) => { setEditNotes(e.target.value); setNotesChanged(true); }}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary placeholder-text-secondary focus:outline-none focus:border-accent-500 resize-none"
            rows={2}
            placeholder="Add notes..."
          />
          {notesChanged && (
            <button
              onClick={handleSaveNotes}
              className="mt-1 px-3 py-1 text-[10px] rounded bg-accent-900/60 text-accent-400 hover:bg-accent-800/60 transition-colors"
            >
              Save Notes
            </button>
          )}
        </div>

        {/* Close position */}
        {position.status === 'OPEN' && (
          <div className="border-t border-border/40 pt-3">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-2">Close Position</h4>
            <div className="flex items-center gap-2">
              <input
                type="number"
                step="0.01"
                value={closePrice}
                onChange={(e) => setClosePrice(e.target.value)}
                className="flex-1 px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
                placeholder={isOption ? 'Close premium' : 'Close price'}
              />
              <button
                onClick={handleClose}
                disabled={closing || !closePrice}
                className="px-3 py-1.5 text-xs font-semibold rounded bg-red-900/40 text-red-400 hover:bg-red-900/60 disabled:opacity-40 transition-colors"
              >
                {closing ? '...' : 'Close'}
              </button>
            </div>
            <input
              type="text"
              value={closeNotes}
              onChange={(e) => setCloseNotes(e.target.value)}
              className="w-full mt-2 px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:outline-none focus:border-accent-500"
              placeholder="Close notes (optional)"
            />
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Main Page ──────────────────────────────────────────────────────────── */

export default function PositionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<'OPEN' | 'CLOSED'>('OPEN');
  const [showForm, setShowForm] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [initialForm, setInitialForm] = useState<Partial<PositionCreateRequest> | undefined>();

  const positions = usePositions(tab);
  const [refreshing, setRefreshing] = useState(false);

  // Bulk-refresh prices on the FIRST mount so the P&L the user sees is fresh.
  // The hook's 60s poll keeps it warm after that.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setRefreshing(true);
      try {
        await refreshAllPositions();
        if (!cancelled) positions.refetch();
      } catch {
        // best-effort; existing prices remain
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Handle pre-fill from Dashboard "Open Position" link
  useEffect(() => {
    if (searchParams.get('open') === 'true') {
      const prefill: Partial<PositionCreateRequest> = {};
      const ticker = searchParams.get('ticker');
      const price = searchParams.get('price');
      const type = searchParams.get('type');
      const strike = searchParams.get('strike');
      const expiry = searchParams.get('expiry');
      const premium = searchParams.get('premium');
      const recId = searchParams.get('rec');

      if (ticker) prefill.ticker = ticker;
      if (recId) prefill.recommendation_id = parseInt(recId, 10);
      if (price) prefill.entry_price = parseFloat(price);
      if (type && (type === 'CALL' || type === 'PUT' || type === 'STOCK')) prefill.position_type = type;
      if (strike) prefill.strike_price = parseFloat(strike);
      if (expiry) prefill.expiry = expiry;
      if (premium) prefill.premium_paid = parseFloat(premium);

      setInitialForm(Object.keys(prefill).length > 0 ? prefill : undefined);
      setShowForm(true);
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleCreate = useCallback(async (data: PositionCreateRequest) => {
    await createPosition(data);
    setShowForm(false);
    setInitialForm(undefined);
    positions.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleClosePosition = useCallback(async (id: number, price: number, notes?: string) => {
    await closePosition(id, { close_price: price, notes });
    positions.refetch();
    setSelectedId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleDelete = useCallback(async (id: number) => {
    await deletePosition(id);
    positions.refetch();
    setSelectedId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRefresh = useCallback(async (id: number) => {
    await refreshPositionPrice(id);
    positions.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleUpdate = useCallback(async (id: number, data: Record<string, unknown>) => {
    await updatePosition(id, data);
    positions.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleRefreshAll = useCallback(async () => {
    setRefreshing(true);
    try {
      await refreshAllPositions();
      positions.refetch();
    } finally {
      setRefreshing(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = positions.data?.find((p) => p.id === selectedId) ?? null;
  const allPositions = positions.data ?? [];

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-7xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">My Positions</h1>
            {refreshing && (
              <span className="flex items-center gap-1 text-[11px] text-text-secondary" title="Pulling latest prices">
                <span className="w-3 h-3 border-2 border-text-secondary border-t-transparent rounded-full animate-spin inline-block" />
                refreshing
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleRefreshAll}
              disabled={refreshing}
              title="Refresh prices for all open positions"
              aria-label="Refresh all prices"
              className="p-2 sm:p-1.5 rounded hover:bg-border/60 disabled:opacity-40 transition-colors text-text-secondary"
            >
              <svg className={`w-4 h-4 ${refreshing ? 'animate-spin' : ''}`} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4 4v5h.582M4.582 9A8.001 8.001 0 0119.418 9M20 20v-5h-.581m0 0a8.003 8.003 0 01-15.357-2" />
              </svg>
            </button>
            <button
              onClick={() => { setShowForm((v) => !v); if (showForm) setInitialForm(undefined); }}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded bg-accent-900/60 text-accent-400 hover:bg-accent-800/60 transition-colors"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
              {showForm ? 'Cancel' : 'Add Position'}
            </button>
          </div>
        </div>

        {/* Add form */}
        {showForm && (
          <AddPositionForm
            initial={initialForm}
            onSubmit={handleCreate}
            onCancel={() => { setShowForm(false); setInitialForm(undefined); }}
          />
        )}

        {/* Summary (open positions only) */}
        {tab === 'OPEN' && allPositions.length > 0 && <SummaryBar positions={allPositions} />}

        {/* P&L history rollup (day / month) */}
        <PnlHistorySection />

        {/* Tabs */}
        <div className="flex gap-1 border-b border-border">
          {(['OPEN', 'CLOSED'] as const).map((t) => (
            <button
              key={t}
              onClick={() => { setTab(t); setSelectedId(null); }}
              className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
                tab === t
                  ? 'border-accent-500 text-text-primary'
                  : 'border-transparent text-text-secondary hover:text-text-primary'
              }`}
            >
              {t === 'OPEN' ? 'Open' : 'Closed'}
            </button>
          ))}
        </div>

        {/* Content */}
        {positions.loading ? (
          <div className="flex items-center justify-center py-12">
            <div className="w-5 h-5 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
            <span className="ml-2 text-sm text-text-secondary">Loading...</span>
          </div>
        ) : positions.error ? (
          <div className="py-4 px-3 bg-red-900/20 border border-red-900/40 rounded text-sm text-red-400">
            {positions.error}
          </div>
        ) : allPositions.length === 0 ? (
          <div className="py-12 text-center text-text-secondary text-sm">
            {tab === 'OPEN' ? 'No open positions. Click "Add Position" to get started.' : 'No closed positions yet.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
            {/* Position cards */}
            <div className="lg:col-span-3 space-y-2">
              {allPositions.map((pos) => (
                <PositionCard
                  key={pos.id}
                  position={pos}
                  selected={pos.id === selectedId}
                  onClick={() => setSelectedId(pos.id === selectedId ? null : pos.id)}
                />
              ))}
            </div>

            {/* Detail panel */}
            <div className="lg:col-span-2">
              {selected ? (
                <PositionDetail
                  position={selected}
                  onClose={handleClosePosition}
                  onDelete={handleDelete}
                  onRefresh={handleRefresh}
                  onUpdate={handleUpdate}
                  onAnalyzeDone={() => positions.refetch()}
                />
              ) : (
                <div className="bg-card border border-border rounded-lg p-8 text-center text-text-secondary text-sm">
                  Click a position to view details
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
