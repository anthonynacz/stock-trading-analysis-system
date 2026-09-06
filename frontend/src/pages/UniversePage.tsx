import { useState, useCallback } from 'react';
import type { UniverseStock, DiscoveryCandidate } from '../types';
import { useUniverse, useDiscoveryCandidates } from '../hooks/useEdgeFlow';
import {
  addToUniverse,
  removeFromUniverse,
  approveCandidate,
  dismissCandidate,
  triggerDiscovery,
  getApiErrorMessage,
} from '../utils/api';
import { PALETTE, SECTORS } from '../utils/theme';
import { fmtMarketCap, fmtPrice, fmtSigned } from '../utils/format';
import { SegmentedControl } from '../components/ui/SegmentedControl';
import { EmptyCard, ErrorBox, LoadingRow } from '../components/ui/feedback';

const SOURCE_COLORS: Record<string, string> = {
  SEED: PALETTE.gray,
  MANUAL: PALETTE.purple,
  DISCOVERED: PALETTE.blue,
};

const DISCOVERY_SOURCE_COLORS: Record<string, string> = {
  MOST_ACTIVE: PALETTE.blue,
  GAINER: PALETTE.greenLight,
  LOSER: PALETTE.red,
  NEWS_TRENDING: PALETTE.amber,
};

type Tab = 'universe' | 'candidates';

// ── Stock card in the universe tab ─────────────────────────────────────────

function StockCard({
  stock,
  onRemove,
}: {
  stock: UniverseStock;
  onRemove: (ticker: string) => void;
}) {
  const sourceColor = SOURCE_COLORS[stock.source] ?? PALETTE.gray;
  return (
    <div className="bg-card border border-border rounded-lg px-3 py-2 flex items-center justify-between group hover:border-text-secondary transition-colors">
      <div className="flex items-center gap-2 min-w-0">
        <span className="text-sm font-bold text-text-primary">{stock.ticker}</span>
        {stock.company_name && (
          <span className="text-xs text-text-secondary truncate">
            {stock.company_name}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        <span
          className="text-[10px] font-medium px-1.5 py-0.5 rounded"
          style={{ color: sourceColor, backgroundColor: `${sourceColor}18` }}
        >
          {stock.source}
        </span>
        <button
          onClick={() => onRemove(stock.ticker)}
          className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-red-900/40 transition-all text-text-secondary hover:text-red-400"
          title="Remove from universe"
        >
          <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>
    </div>
  );
}

// ── Candidate card ─────────────────────────────────────────────────────────

function CandidateCard({
  candidate,
  sectors,
  onApprove,
  onDismiss,
}: {
  candidate: DiscoveryCandidate;
  sectors: string[];
  onApprove: (id: number, sector: string) => void;
  onDismiss: (id: number) => void;
}) {
  const [sector, setSector] = useState(candidate.suggested_sector ?? sectors[0]);
  const sourceColor = DISCOVERY_SOURCE_COLORS[candidate.source] ?? PALETTE.gray;
  const changePct = candidate.change_pct;
  const changeColor =
    changePct != null ? (changePct >= 0 ? PALETTE.greenLight : PALETTE.red) : PALETTE.gray;

  return (
    <div className="bg-card border border-border rounded-lg p-3 hover:border-text-secondary transition-colors">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-bold text-text-primary">{candidate.ticker}</span>
          <span
            className="text-[10px] font-medium px-1.5 py-0.5 rounded"
            style={{ color: sourceColor, backgroundColor: `${sourceColor}18` }}
          >
            {candidate.source.replace('_', ' ')}
          </span>
        </div>
        {candidate.price != null && (
          <span className="text-sm text-text-primary font-medium">
            {fmtPrice(candidate.price)}
          </span>
        )}
      </div>

      {/* Company name + change */}
      <div className="flex items-center justify-between mb-1">
        {candidate.company_name && (
          <span className="text-xs text-text-secondary truncate mr-2">
            {candidate.company_name}
          </span>
        )}
        {changePct != null && (
          <span className="text-xs font-medium" style={{ color: changeColor }}>
            {fmtSigned(changePct, 1, '%')}
          </span>
        )}
      </div>

      {/* Market cap */}
      {candidate.market_cap != null && (
        <div className="text-xs text-text-secondary mb-2">
          Mkt Cap: {fmtMarketCap(candidate.market_cap)}
        </div>
      )}

      {/* Rationale */}
      {candidate.rationale && (
        <p className="text-xs text-text-secondary mb-3 line-clamp-2">
          {candidate.rationale}
        </p>
      )}

      {/* Actions */}
      <div className="flex items-center gap-2">
        <select
          value={sector}
          onChange={(e) => setSector(e.target.value)}
          className="flex-1 text-xs bg-page border border-border rounded px-2 py-1 text-text-primary"
        >
          {sectors.map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>
        <button
          onClick={() => onApprove(candidate.id, sector)}
          className="text-xs px-2.5 py-1 rounded bg-green-900/40 text-green-400 hover:bg-green-900/60 transition-colors font-medium"
        >
          Approve
        </button>
        <button onClick={() => onDismiss(candidate.id)} className="btn-danger">
          Dismiss
        </button>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function UniversePage() {
  const [tab, setTab] = useState<Tab>('universe');
  const [addTicker, setAddTicker] = useState('');
  const [addSector, setAddSector] = useState(SECTORS[0]);
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [discovering, setDiscovering] = useState(false);

  const universe = useUniverse();
  const candidates = useDiscoveryCandidates();

  const sectorNames = universe.data?.sectors.map((s) => s.name) ?? SECTORS;
  const pendingCount = candidates.data?.length ?? universe.data?.pending_candidates ?? 0;

  const handleAdd = useCallback(async () => {
    const ticker = addTicker.trim().toUpperCase();
    if (!ticker) return;
    setAdding(true);
    setAddError(null);
    try {
      await addToUniverse(ticker, addSector);
      setAddTicker('');
      universe.refetch();
    } catch (e: unknown) {
      setAddError(getApiErrorMessage(e, 'Failed to add ticker'));
    } finally {
      setAdding(false);
    }
  }, [addTicker, addSector, universe]);

  const handleRemove = useCallback(async (ticker: string) => {
    try {
      await removeFromUniverse(ticker);
      universe.refetch();
    } catch {
      // ignore
    }
  }, [universe]);

  const handleApprove = useCallback(async (id: number, sector: string) => {
    try {
      await approveCandidate(id, sector);
      candidates.refetch();
      universe.refetch();
    } catch {
      // ignore
    }
  }, [candidates, universe]);

  const handleDismiss = useCallback(async (id: number) => {
    try {
      await dismissCandidate(id);
      candidates.refetch();
    } catch {
      // ignore
    }
  }, [candidates]);

  const handleDiscover = useCallback(async () => {
    setDiscovering(true);
    try {
      await triggerDiscovery();
      // Poll for results after a short delay
      setTimeout(() => {
        candidates.refetch();
        setDiscovering(false);
      }, 3000);
    } catch {
      setDiscovering(false);
    }
  }, [candidates]);

  return (
    <main className="max-w-7xl mx-auto px-3 py-3 sm:px-4 sm:py-4 space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-text-primary">
          Stock Universe
          {universe.data && (
            <span className="text-text-secondary font-normal text-sm ml-2">
              {universe.data.total_stocks} stocks
            </span>
          )}
        </h1>

        <SegmentedControl<Tab>
          value={tab}
          onChange={setTab}
          options={[
            { key: 'universe', label: 'Universe' },
            {
              key: 'candidates',
              label: 'Candidates',
              badge: () =>
                pendingCount > 0 ? (
                  <span className="absolute -top-1 -right-1 bg-blue-500 text-white text-[9px] font-bold rounded-full w-4 h-4 flex items-center justify-center">
                    {pendingCount > 99 ? '99+' : pendingCount}
                  </span>
                ) : null,
            },
          ]}
        />
      </div>

      {/* Universe tab */}
      {tab === 'universe' && (
        <>
          {/* Add ticker input */}
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={addTicker}
              onChange={(e) => setAddTicker(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleAdd()}
              placeholder="Add ticker..."
              className="text-sm bg-card border border-border rounded-lg px-3 py-1.5 text-text-primary placeholder:text-text-secondary/50 w-32 focus:border-accent-500"
            />
            <select
              value={addSector}
              onChange={(e) => setAddSector(e.target.value)}
              className="text-sm bg-card border border-border rounded-lg px-3 py-1.5 text-text-primary"
            >
              {sectorNames.map((s) => (
                <option key={s} value={s}>{s}</option>
              ))}
            </select>
            <button
              onClick={handleAdd}
              disabled={adding || !addTicker.trim()}
              className="btn-primary"
            >
              {adding ? 'Adding...' : 'Add'}
            </button>
          </div>
          {addError && <ErrorBox message={addError} size="xs" />}

          {universe.loading && !universe.data ? (
            <LoadingRow label="Loading universe…" py="py-12" />
          ) : universe.data ? (
            <div className="space-y-4">
              {universe.data.sectors.map((sector) => (
                <section key={sector.name}>
                  <div className="flex items-center gap-2 mb-2">
                    <h2 className="text-sm font-semibold text-text-primary">
                      {sector.name}
                    </h2>
                    <span className="text-xs text-text-secondary">
                      {sector.stock_count}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-2">
                    {sector.stocks.map((stock) => (
                      <StockCard
                        key={stock.id}
                        stock={stock}
                        onRemove={handleRemove}
                      />
                    ))}
                  </div>
                </section>
              ))}
            </div>
          ) : null}
        </>
      )}

      {/* Candidates tab */}
      {tab === 'candidates' && (
        <>
          {/* Discovery button */}
          <div className="flex items-center gap-3">
            <button onClick={handleDiscover} disabled={discovering} className="btn-primary">
              {discovering ? 'Discovering...' : 'Run Discovery'}
            </button>
            <span className="text-xs text-text-secondary">
              Scans most active, gainers, losers, and news-trending stocks
            </span>
          </div>

          {candidates.loading && !candidates.data ? (
            <LoadingRow label="Loading candidates…" py="py-12" />
          ) : candidates.data && candidates.data.length > 0 ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {candidates.data.map((c) => (
                <CandidateCard
                  key={c.id}
                  candidate={c}
                  sectors={sectorNames}
                  onApprove={handleApprove}
                  onDismiss={handleDismiss}
                />
              ))}
            </div>
          ) : (
            <EmptyCard>
              <p>No pending candidates</p>
              <p className="text-text-secondary/60 text-xs mt-1">
                Run discovery or wait for the daily scan to surface new stocks
              </p>
            </EmptyCard>
          )}
        </>
      )}
    </main>
  );
}
