import { useState, useCallback, useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import type {
  HealthSeverity,
  Position,
  PositionCreateRequest,
  PositionHealthFlag,
  PositionType,
} from '../types';
import { usePositions } from '../hooks/useEdgeFlow';
import { usePolling } from '../hooks/usePolling';
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
import {
  ACTION_COLORS,
  BRAND,
  PALETTE,
  PNL_COLOR,
  POSITION_TYPE_COLORS,
  getActionLabel,
} from '../utils/theme';
import { fmtCurrency, fmtSigned } from '../utils/format';
import { SignalBullet } from '../components/SignalBullet';
import { EmptyCard, ErrorBox, LoadingRow } from '../components/ui/feedback';
import { SegmentedControl, type SegmentOption } from '../components/ui/SegmentedControl';
import { TabBar, type TabItem } from '../components/ui/TabBar';

/* ── Helpers ────────────────────────────────────────────────────────────── */

function formatDate(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/** `+$1,234.56` — sign prefix ahead of the currency symbol (zero renders `+$0.00`). */
function fmtSignedCurrency(v: number): string {
  return `${v >= 0 ? '+' : ''}${fmtCurrency(v)}`;
}

const SEVERITY_RANK: Record<HealthSeverity, number> = { info: 0, warn: 1, critical: 2 };
const SEVERITY_COLOR: Record<HealthSeverity, string> = {
  info: PALETTE.blue,
  warn: PALETTE.amber,
  critical: PALETTE.red,
};

/** Highest-severity flag, or null. `health_flags` is always [] for CLOSED positions. */
function topHealthFlag(flags: PositionHealthFlag[]): PositionHealthFlag | null {
  return flags.reduce<PositionHealthFlag | null>(
    (best, f) => (best == null || SEVERITY_RANK[f.severity] > SEVERITY_RANK[best.severity] ? f : best),
    null,
  );
}

function hasFlag(flags: PositionHealthFlag[], code: PositionHealthFlag['code']): boolean {
  return flags.some((f) => f.code === code);
}

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

const PNL_VIEWS: SegmentOption<'day' | 'month'>[] = [
  { key: 'day', label: 'Daily' },
  { key: 'month', label: 'Monthly' },
];

function PnlHistorySection() {
  const [data, setData] = useState<PnlHistory | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<'day' | 'month'>('day');

  const load = useCallback(async () => {
    try {
      setData(await getPnlHistory(90));
    } catch {
      setData({ daily: [], monthly: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);
  usePolling(load, 300_000);

  if (loading || !data) return null;
  if (data.daily.length === 0 && data.monthly.length === 0) {
    return (
      <EmptyCard>
        <div className="font-bold text-text-primary mb-1">P&L over time</div>
        No snapshots yet. The first row is written tonight at 16:45 ET after market close,
        and one row per weekday thereafter.
      </EmptyCard>
    );
  }

  const rows = view === 'day' ? data.daily : data.monthly;

  return (
    <div className="bg-card border border-border rounded-lg p-3 sm:p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-bold text-text-primary">P&L over time</h3>
        <SegmentedControl options={PNL_VIEWS} value={view} onChange={setView} />
      </div>

      <ul className="divide-y divide-border max-h-72 overflow-y-auto">
        {rows.length === 0 && (
          <li className="py-3 text-xs text-text-secondary text-center">No {view} data.</li>
        )}
        {view === 'day' && (data.daily ?? []).map((d) => {
          const color = PNL_COLOR(d.realized_pnl_today);
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
                {fmtSignedCurrency(d.realized_pnl_today)}
              </span>
              <span className="text-[10px] text-text-secondary flex-1 min-w-0 truncate">
                {d.closed_count_today > 0 && `${d.closed_count_today} closed · `}
                {d.open_count} open
              </span>
              <span
                className="text-[10px] font-mono tabular-nums text-text-secondary w-20 shrink-0 text-right"
                title="Unrealized P&L at snapshot time"
              >
                {fmtSignedCurrency(d.unrealized_pnl)} U
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
              style={{ color: PNL_COLOR(m.realized_pnl) }}
              title="Total realized P&L closed in this month"
            >
              {fmtSignedCurrency(m.realized_pnl)}
            </span>
            <span className="text-[10px] text-text-secondary flex-1 min-w-0 truncate">
              {m.closed_count > 0 ? `${m.closed_count} closed` : 'no closures'}
            </span>
            <span
              className="text-[10px] font-mono tabular-nums text-text-secondary w-20 shrink-0 text-right"
              title="Unrealized at the latest snapshot in this month"
            >
              {fmtSignedCurrency(m.latest_unrealized)} U
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
        <div className="text-xl font-bold" style={{ color: PNL_COLOR(totalPnl) }}>
          {fmtSignedCurrency(totalPnl)}
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
        <button type="button" onClick={onCancel} className="btn-secondary">
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
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary placeholder-text-secondary focus:border-accent-500"
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
                style={posType === t ? { backgroundColor: POSITION_TYPE_COLORS[t] + 'cc' } : undefined}
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
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
          />
        </div>
        <div>
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Entry Price</label>
          <input
            type="number"
            step="0.01"
            value={entryPrice}
            onChange={(e) => setEntryPrice(e.target.value)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
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
              className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
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
              className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
              placeholder="Per contract"
            />
          </div>
          <div>
            <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Expiry</label>
            <input
              type="date"
              value={expiry}
              onChange={(e) => setExpiry(e.target.value)}
              className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
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
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
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
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
            placeholder="Optional"
          />
        </div>
        <div className="col-span-2 md:col-span-1">
          <label className="block text-[10px] text-text-secondary mb-1 uppercase tracking-wider">Notes</label>
          <input
            type="text"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
            placeholder="Optional"
          />
        </div>
      </div>

      <div className="flex justify-end">
        <button
          type="submit"
          disabled={submitting || !ticker.trim() || !entryPrice}
          className="btn-primary px-4"
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
  const flags = position.health_flags ?? [];
  const topFlag = topHealthFlag(flags);
  const borderColor = topFlag?.severity === 'critical' ? PALETTE.red : PNL_COLOR(pnl);
  const expired = hasFlag(flags, 'EXPIRED');
  const dteWarning = hasFlag(flags, 'DTE_WARNING');

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onClick();
        }
      }}
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
            style={{ backgroundColor: POSITION_TYPE_COLORS[position.position_type] + '22', color: POSITION_TYPE_COLORS[position.position_type] }}
          >
            {position.position_type}
          </span>
          <span className="text-[10px] text-text-secondary">
            x{position.quantity}
          </span>
        </div>
        <div className="flex items-center gap-1.5">
          {topFlag && (
            <span
              className="flex items-center gap-1 text-[9px] px-1.5 py-0.5 rounded font-semibold cursor-help"
              style={{ backgroundColor: SEVERITY_COLOR[topFlag.severity] + '22', color: SEVERITY_COLOR[topFlag.severity] }}
              title={topFlag.message}
            >
              <span
                className="w-1.5 h-1.5 rounded-full shrink-0"
                style={{ backgroundColor: SEVERITY_COLOR[topFlag.severity] }}
              />
              {topFlag.code.replace('_', ' ')}
              {flags.length > 1 && <span className="font-normal opacity-80">+{flags.length - 1}</span>}
            </span>
          )}
          {position.is_on_watchlist && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-blue-900/30 text-blue-400 font-medium">
              Watchlist
            </span>
          )}
        </div>
      </div>

      <div className="flex items-center justify-between text-xs">
        <div className="text-text-secondary">
          Entry: <span className="text-text-primary font-mono">{fmtCurrency(position.entry_price)}</span>
          {isOption && position.strike_price != null && (
            <span className="ml-2">
              Strike: <span className="text-text-primary font-mono">{fmtCurrency(position.strike_price)}</span>
            </span>
          )}
        </div>
        <div className="text-right">
          {pnl != null && (
            <span className="font-mono font-semibold" style={{ color: PNL_COLOR(pnl) }}>
              {fmtSignedCurrency(pnl)}
              {pnlPct != null && (
                <span className="text-[10px] ml-1">({fmtSigned(pnlPct, 1, '%')})</span>
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
                expired
                  ? 'bg-red-900/60 text-red-400'
                  : position.days_to_expiry <= 3
                    ? 'bg-amber-900/60 text-amber-400'
                    : dteWarning
                      ? 'bg-amber-900/30 text-amber-400'
                      : 'bg-border text-text-secondary'
              }`}
            >
              {expired ? 'Expired' : `${position.days_to_expiry}d left`}
            </span>
          )}
          {position.premium_paid != null && (
            <span>Premium: {fmtCurrency(position.premium_paid)}</span>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Position Detail Panel ──────────────────────────────────────────────── */

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
  const flags = position.health_flags ?? [];
  const expired = hasFlag(flags, 'EXPIRED');
  const dteWarning = hasFlag(flags, 'DTE_WARNING');

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
    <div className="detail-panel overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-border">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-lg font-bold text-text-primary">{position.ticker}</span>
            <span
              className="px-1.5 py-0.5 rounded text-[10px] font-bold"
              style={{ backgroundColor: POSITION_TYPE_COLORS[position.position_type] + '22', color: POSITION_TYPE_COLORS[position.position_type] }}
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
            className="btn-danger px-1.5"
            title="Delete position"
            aria-label="Delete position"
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
            <span className="text-text-primary font-mono">{fmtCurrency(position.entry_price)}</span>
          </div>
          {isOption && position.strike_price != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Strike</span>
              <span className="text-text-primary font-mono">{fmtCurrency(position.strike_price)}</span>
            </div>
          )}
          {isOption && position.premium_paid != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Premium</span>
              <span className="text-text-primary font-mono">{fmtCurrency(position.premium_paid)}</span>
            </div>
          )}
          {position.current_price != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Current</span>
              <span className="text-text-primary font-mono">{fmtCurrency(position.current_price)}</span>
            </div>
          )}
          {position.stop_loss != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Stop Loss</span>
              <span className="text-red-400 font-mono">{fmtCurrency(position.stop_loss)}</span>
            </div>
          )}
          {position.target_price != null && (
            <div className="flex justify-between">
              <span className="text-text-secondary">Target</span>
              <span className="text-green-400 font-mono">{fmtCurrency(position.target_price)}</span>
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
              <span
                className={
                  expired
                    ? 'text-red-400 font-semibold'
                    : dteWarning || position.days_to_expiry <= 3
                      ? 'text-amber-400 font-semibold'
                      : 'text-text-primary'
                }
              >
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
            <div className="text-lg font-bold font-mono" style={{ color: PNL_COLOR(position.unrealized_pnl) }}>
              {fmtSignedCurrency(position.unrealized_pnl)}
              {position.unrealized_pnl_pct != null && (
                <span className="text-sm ml-2">
                  ({fmtSigned(position.unrealized_pnl_pct, 1, '%')})
                </span>
              )}
            </div>
          </div>
        )}

        {position.status === 'CLOSED' && position.realized_pnl != null && (
          <div className="bg-page border border-border rounded-lg p-3 text-center">
            <div className="text-[10px] text-text-secondary mb-1">Realized P&L</div>
            <div className="text-lg font-bold font-mono" style={{ color: PNL_COLOR(position.realized_pnl) }}>
              {fmtSignedCurrency(position.realized_pnl)}
            </div>
            {position.close_price != null && (
              <div className="text-[10px] text-text-secondary mt-1">
                Closed at {fmtCurrency(position.close_price)} on {formatDate(position.closed_at)}
              </div>
            )}
          </div>
        )}

        {/* Health flags (position-aware overlay; server-computed, [] when closed) */}
        {flags.length > 0 && (
          <ul className="space-y-1">
            {flags.map((f) => {
              const color = SEVERITY_COLOR[f.severity];
              return (
                <li
                  key={f.code}
                  className="flex items-start gap-2 text-[11px] leading-snug rounded px-2 py-1.5"
                  style={{ backgroundColor: color + '14', border: `1px solid ${color}40` }}
                >
                  <span className="w-1.5 h-1.5 rounded-full shrink-0 mt-1" style={{ backgroundColor: color }} />
                  <span className="text-text-primary">
                    <span className="font-semibold mr-1" style={{ color }}>{f.code.replace('_', ' ')}</span>
                    {f.message}
                  </span>
                </li>
              );
            })}
          </ul>
        )}

        {/* Refresh button (STOCK only, since option premium needs manual update) */}
        {position.status === 'OPEN' && position.position_type === 'STOCK' && (
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className="btn-secondary w-full justify-center"
          >
            {refreshing ? 'Refreshing...' : 'Refresh Stock Price'}
          </button>
        )}

        {/* Recommendation overlay */}
        {rec && (
          <div className="border-t border-border/40 pt-3">
            <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider mb-2">
              {BRAND} Recommendation
              <span className="text-[9px] font-normal ml-1 normal-case">({rec.recommendation_date})</span>
            </h4>
            <div className="flex items-center gap-2 mb-2">
              <span
                className="px-2 py-0.5 rounded text-xs font-bold"
                style={{ backgroundColor: (ACTION_COLORS[rec.action] ?? PALETTE.border) + '22', color: ACTION_COLORS[rec.action] }}
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
                  <SignalBullet key={i} signal={s} compact />
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
                  <span className="text-green-400 font-mono">{fmtCurrency(rec.target_price)}</span>
                </div>
              )}
              {rec.stop_loss_price != null && (
                <div className="flex justify-between">
                  <span className="text-text-secondary">Stop</span>
                  <span className="text-red-400 font-mono">{fmtCurrency(rec.stop_loss_price)}</span>
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
              className="btn-primary w-full justify-center"
            >
              {analyzing ? 'Analyzing (30-60s)...' : `Run ${BRAND} Analysis`}
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
            className="w-full px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary placeholder-text-secondary focus:border-accent-500 resize-none"
            rows={2}
            placeholder="Add notes..."
          />
          {notesChanged && (
            <button onClick={handleSaveNotes} className="btn-primary mt-1">
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
                className="flex-1 px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
                placeholder={isOption ? 'Close premium' : 'Close price'}
              />
              <button
                onClick={handleClose}
                disabled={closing || !closePrice}
                className="btn-danger"
              >
                {closing ? '...' : 'Close'}
              </button>
            </div>
            <input
              type="text"
              value={closeNotes}
              onChange={(e) => setCloseNotes(e.target.value)}
              className="w-full mt-2 px-2 py-1.5 text-xs bg-page border border-border rounded text-text-primary focus:border-accent-500"
              placeholder="Close notes (optional)"
            />
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Main Page ──────────────────────────────────────────────────────────── */

const POSITION_TABS: TabItem<'OPEN' | 'CLOSED'>[] = [
  { key: 'OPEN', label: 'Open' },
  { key: 'CLOSED', label: 'Closed' },
];

export default function PositionsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [tab, setTab] = useState<'OPEN' | 'CLOSED'>('OPEN');
  const [showForm, setShowForm] = useState(false);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [initialForm, setInitialForm] = useState<Partial<PositionCreateRequest> | undefined>();

  const positions = usePositions(tab);
  const [refreshing, setRefreshing] = useState(false);

  // `positions.refetch` is re-created when `tab` changes; the stable handlers
  // below call through this ref so they always refetch the tab on screen.
  const refetchRef = useRef(positions.refetch);
  useEffect(() => {
    refetchRef.current = positions.refetch;
  }, [positions.refetch]);

  // Bulk-refresh prices on the FIRST mount so the P&L the user sees is fresh.
  // The hook's 60s poll keeps it warm after that.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      setRefreshing(true);
      try {
        await refreshAllPositions();
        if (!cancelled) refetchRef.current();
      } catch {
        // best-effort; existing prices remain
      } finally {
        if (!cancelled) setRefreshing(false);
      }
    })();
    return () => {
      cancelled = true;
    };
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
    refetchRef.current();
  }, []);

  const handleClosePosition = useCallback(async (id: number, price: number, notes?: string) => {
    await closePosition(id, { close_price: price, notes });
    refetchRef.current();
    setSelectedId(null);
  }, []);

  const handleDelete = useCallback(async (id: number) => {
    await deletePosition(id);
    refetchRef.current();
    setSelectedId(null);
  }, []);

  const handleRefresh = useCallback(async (id: number) => {
    await refreshPositionPrice(id);
    refetchRef.current();
  }, []);

  const handleUpdate = useCallback(async (id: number, data: Record<string, unknown>) => {
    await updatePosition(id, data);
    refetchRef.current();
  }, []);

  const handleRefreshAll = useCallback(async () => {
    setRefreshing(true);
    try {
      await refreshAllPositions();
      refetchRef.current();
    } finally {
      setRefreshing(false);
    }
  }, []);

  const selected = positions.data?.find((p) => p.id === selectedId) ?? null;
  const allPositions = positions.data ?? [];

  return (
    <div className="min-h-screen bg-page text-text-primary">
      <div className="max-w-7xl mx-auto px-3 py-4 sm:px-4 sm:py-6 space-y-4">
        {/* Header */}
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2">
            <h1 className="text-2xl font-bold">My Positions</h1>
            {refreshing && (
              <span className="flex items-center gap-1 text-[11px] text-text-secondary" title="Pulling latest prices">
                <span className="w-3 h-3 border-2 border-text-secondary border-t-transparent rounded-full animate-spin inline-block" />
                refreshing
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2 sm:ml-auto w-full sm:w-auto">
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
              onClick={() => setShowForm(true)}
              disabled={showForm}
              className="btn-primary"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
              </svg>
              Add Position
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

        {/* Tabs */}
        <TabBar
          tabs={POSITION_TABS}
          active={tab}
          size="md"
          onChange={(t) => { setTab(t); setSelectedId(null); }}
        />

        {/* Content — gate on first load only so the 60s poll never unmounts the detail panel */}
        {positions.loading && !positions.data ? (
          <LoadingRow py="py-12" />
        ) : positions.error ? (
          <ErrorBox message={positions.error} />
        ) : allPositions.length === 0 ? (
          <EmptyCard>
            {tab === 'OPEN' ? 'No open positions. Click "Add Position" to get started.' : 'No closed positions yet.'}
          </EmptyCard>
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
                <EmptyCard>Click a position to view details</EmptyCard>
              )}
            </div>
          </div>
        )}

        {/* P&L history rollup (day / month) */}
        <PnlHistorySection />
      </div>
    </div>
  );
}
