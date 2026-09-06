import { useState, useMemo, useRef } from 'react';

/* ── Strategies section ─────────────────────────────────────────────────── */

interface Leg {
  action: 'BUY' | 'SELL';
  side: 'CALL' | 'PUT';
  strike: number;
  premium: number;
}

interface Strategy {
  name: string;
  verdict: string;
  trigger: string;
  purpose: string;
  legs: Leg[];
  maxProfit: string;
  maxLoss: string;
  breakeven: string;
  priceMin: number;
  priceMax: number;
}

function legPnL(leg: Leg, S: number): number {
  const intrinsic = leg.side === 'CALL' ? Math.max(S - leg.strike, 0) : Math.max(leg.strike - S, 0);
  return leg.action === 'BUY' ? intrinsic - leg.premium : leg.premium - intrinsic;
}

interface PayoffMetrics {
  profitStr: string;
  lossStr: string;
  beStr: string;
  yLo: number;
  yHi: number;
}

const isLegActive = (leg: Leg): boolean => leg.strike > 0 && leg.premium > 0;

function computePayoff(
  legs: Leg[],
  priceMin: number,
  priceMax: number,
  N = 401,
): { prices: number[]; pnl: number[]; legPnls: number[][]; metrics: PayoffMetrics } {
  const prices: number[] = new Array(N);
  const pnl: number[] = new Array(N);
  const legPnls: number[][] = legs.map(() => new Array(N));
  for (let i = 0; i < N; i++) {
    const S = priceMin + ((priceMax - priceMin) * i) / (N - 1);
    prices[i] = S;
    let total = 0;
    for (let l = 0; l < legs.length; l++) {
      const v = isLegActive(legs[l]) ? legPnL(legs[l], S) : 0;
      legPnls[l][i] = v;
      total += v;
    }
    pnl[i] = total;
  }
  const maxPnl = Math.max(...pnl);
  const minPnl = Math.min(...pnl);
  const pad = (maxPnl - minPnl) * 0.15 || 1;
  const yLo = Math.min(0, minPnl) - pad;
  const yHi = Math.max(0, maxPnl) + pad;

  // Edge slopes — detect unbounded profit/loss regions.
  const rSlope = pnl[N - 1] - pnl[N - 2];
  const lSlope = pnl[0] - pnl[1];
  const unboundedUp = rSlope > 0.01 || lSlope > 0.01;
  const unboundedDown = rSlope < -0.01 || lSlope < -0.01;
  const profitStr = unboundedUp ? 'Unlimited' : `$${maxPnl.toFixed(2)}`;
  const lossStr = unboundedDown ? 'Unlimited' : `$${(-minPnl).toFixed(2)}`;

  const crossings: number[] = [];
  for (let i = 1; i < N; i++) {
    const a = pnl[i - 1];
    const b = pnl[i];
    if ((a < 0 && b >= 0) || (a > 0 && b <= 0)) {
      const t = a / (a - b);
      crossings.push(prices[i - 1] + t * (prices[i] - prices[i - 1]));
    }
  }
  const beStr =
    crossings.length === 0
      ? '—'
      : crossings.map((x) => `$${x.toFixed(2)}`).join(' / ');

  return { prices, pnl, legPnls, metrics: { profitStr, lossStr, beStr, yLo, yHi } };
}

type Payoff = ReturnType<typeof computePayoff>;

const LEG_COLOR = (leg: Leg) => (leg.side === 'CALL' ? '#2dd4bf' : '#f59e0b');

interface DragState {
  kind: 'strike' | 'premium';
  legIdx: number;
  startS: number;
  startP: number;
  anchorX: number;
  anchorY: number;
  yLo: number;
  yHi: number;
}

function InteractivePayoff({
  legs,
  payoff,
  priceMin,
  priceMax,
  onLegChange,
  spot = 100,
}: {
  legs: Leg[];
  payoff: Payoff;
  priceMin: number;
  priceMax: number;
  onLegChange: (i: number, patch: Partial<Leg>) => void;
  spot?: number;
}) {
  const W = 380;
  const H = 210;
  const M = { l: 38, r: 12, t: 16, b: 30 };
  const plotW = W - M.l - M.r;
  const plotH = H - M.t - M.b;

  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<DragState | null>(null);

  const { prices, pnl, legPnls, metrics } = payoff;
  const { yLo, yHi } = metrics;

  const xOf = (S: number) =>
    M.l + ((S - priceMin) / (priceMax - priceMin)) * plotW;
  const yOf = (v: number) =>
    M.t + (1 - (v - yLo) / (yHi - yLo)) * plotH;
  const zeroY = yOf(0);

  const N = pnl.length;
  const xs: number[] = prices.map(xOf);
  const combinedPath = pnl
    .map((v, i) => `${i === 0 ? 'M' : 'L'} ${xs[i].toFixed(2)},${yOf(v).toFixed(2)}`)
    .join(' ');

  // Fill profit/loss regions (tint between combined curve and y=0)
  const profitPaths: string[] = [];
  const lossPaths: string[] = [];
  {
    let bucket: 'profit' | 'loss' | null = null;
    let segStart = 0;
    const ys: number[] = pnl.map((v) => yOf(v));
    const closeSeg = (endIdx: number, endX: number) => {
      if (bucket === null) return;
      let d = `M ${xs[segStart].toFixed(2)},${zeroY.toFixed(2)} `;
      for (let i = segStart; i <= endIdx; i++)
        d += `L ${xs[i].toFixed(2)},${ys[i].toFixed(2)} `;
      d += `L ${endX.toFixed(2)},${zeroY.toFixed(2)} Z`;
      (bucket === 'profit' ? profitPaths : lossPaths).push(d);
    };
    for (let i = 0; i < N; i++) {
      const side =
        ys[i] < zeroY - 0.01 ? 'profit' : ys[i] > zeroY + 0.01 ? 'loss' : null;
      if (side === null) continue;
      if (bucket === null) {
        bucket = side;
        segStart = i;
        continue;
      }
      if (side !== bucket) {
        const t = (zeroY - ys[i - 1]) / (ys[i] - ys[i - 1]);
        const crossX = xs[i - 1] + t * (xs[i] - xs[i - 1]);
        closeSeg(i - 1, crossX);
        bucket = side;
        segStart = i;
      }
    }
    if (bucket !== null) closeSeg(N - 1, xs[N - 1]);
  }

  // Leg curves and handle positions
  const legCurves = legs.map((leg, li) => {
    const d = legPnls[li]
      .map((v, i) => `${i === 0 ? 'M' : 'L'} ${xs[i].toFixed(2)},${yOf(v).toFixed(2)}`)
      .join(' ');
    // Premium handle sits on the OTM flat segment
    const span = priceMax - priceMin;
    const handleS =
      leg.side === 'CALL'
        ? Math.max(priceMin + span * 0.05, leg.strike - span * 0.15)
        : Math.min(priceMax - span * 0.05, leg.strike + span * 0.15);
    const flatPnl = leg.action === 'BUY' ? -leg.premium : leg.premium;
    return {
      leg,
      d,
      handle: { x: xOf(handleS), y: yOf(flatPnl) },
    };
  });

  const viewBoxPoint = (e: React.PointerEvent): { x: number; y: number } => {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const pt = svg.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const m = svg.getScreenCTM();
    if (!m) return { x: 0, y: 0 };
    const r = pt.matrixTransform(m.inverse());
    return { x: r.x, y: r.y };
  };

  const beginDrag =
    (kind: 'strike' | 'premium', legIdx: number) =>
    (e: React.PointerEvent) => {
      e.preventDefault();
      (e.currentTarget as Element).setPointerCapture(e.pointerId);
      const p = viewBoxPoint(e);
      setDrag({
        kind,
        legIdx,
        startS: legs[legIdx].strike,
        startP: legs[legIdx].premium,
        anchorX: p.x,
        anchorY: p.y,
        yLo,
        yHi,
      });
    };

  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag) return;
    const p = viewBoxPoint(e);
    const leg = legs[drag.legIdx];
    if (drag.kind === 'strike') {
      const dX = p.x - drag.anchorX;
      const dS = (dX / plotW) * (priceMax - priceMin);
      const raw = drag.startS + dS;
      const snapped = Math.round(raw * 2) / 2; // 0.5 increments
      const clamped = Math.max(priceMin + 0.5, Math.min(priceMax - 0.5, snapped));
      if (clamped !== leg.strike) onLegChange(drag.legIdx, { strike: clamped });
    } else {
      // Premium drag — freeze y-scale at drag start to prevent jitter
      const dY = p.y - drag.anchorY;
      const dPnl = -(dY / plotH) * (drag.yHi - drag.yLo);
      const sign = leg.action === 'BUY' ? -1 : 1;
      const newP = drag.startP + sign * dPnl;
      const snapped = Math.round(newP * 20) / 20; // $0.05 increments
      const clamped = Math.max(0.05, Math.min(99, snapped));
      if (Math.abs(clamped - leg.premium) > 0.001)
        onLegChange(drag.legIdx, { premium: clamped });
    }
  };

  const endDrag = (e: React.PointerEvent) => {
    if (!drag) return;
    (e.currentTarget as Element).releasePointerCapture(e.pointerId);
    setDrag(null);
  };

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      className="w-full h-auto select-none touch-none"
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
    >
      <rect
        x={M.l}
        y={M.t}
        width={plotW}
        height={plotH}
        fill="#0d1117"
        stroke="#21262d"
      />
      {profitPaths.map((d, i) => (
        <path key={`p${i}`} d={d} fill="#2ea04322" />
      ))}
      {lossPaths.map((d, i) => (
        <path key={`l${i}`} d={d} fill="#da363322" />
      ))}
      <line
        x1={M.l}
        y1={zeroY}
        x2={W - M.r}
        y2={zeroY}
        stroke="#4b5563"
        strokeDasharray="3 3"
        strokeWidth={1}
      />
      <line
        x1={xOf(spot)}
        y1={M.t}
        x2={xOf(spot)}
        y2={H - M.b}
        stroke="#58a6ff"
        strokeDasharray="2 2"
        strokeWidth={1}
        opacity={0.7}
      />

      {/* Leg curves — only for active legs */}
      {legs.length > 1 &&
        legCurves.map((lc, i) =>
          isLegActive(lc.leg) ? (
            <path
              key={`leg${i}`}
              d={lc.d}
              fill="none"
              stroke={LEG_COLOR(lc.leg)}
              strokeWidth={1.2}
              strokeDasharray={lc.leg.action === 'SELL' ? '4 3' : undefined}
              opacity={0.85}
            />
          ) : null,
        )}

      {/* Combined P&L */}
      <path d={combinedPath} fill="none" stroke="#e6edf3" strokeWidth={1.75} />

      {/* Strike lines with drag handles — one per active leg */}
      {legs.map((leg, i) => {
        if (!isLegActive(leg)) return null;
        const x = xOf(leg.strike);
        const color = LEG_COLOR(leg);
        const active = drag?.kind === 'strike' && drag.legIdx === i;
        return (
          <g key={`strike-${i}`}>
            <line
              x1={x}
              y1={M.t}
              x2={x}
              y2={H - M.b}
              stroke={color}
              strokeDasharray="2 3"
              strokeWidth={active ? 1.5 : 0.9}
              opacity={0.7}
            />
            {/* Wide invisible hit area over the line for easy grabbing */}
            <rect
              x={x - 5}
              y={M.t}
              width={10}
              height={plotH}
              fill="transparent"
              style={{ cursor: 'ew-resize' }}
              onPointerDown={beginDrag('strike', i)}
            />
            {/* Visible handle at top */}
            <circle
              cx={x}
              cy={M.t + 4}
              r={active ? 4.5 : 3.5}
              fill={color}
              stroke="#0d1117"
              strokeWidth={1}
              style={{ cursor: 'ew-resize' }}
              onPointerDown={beginDrag('strike', i)}
            />
            <text
              x={x}
              y={M.t - 4}
              textAnchor="middle"
              fontSize={9}
              fill={color}
              fontWeight={active ? 600 : 400}
            >
              K{leg.strike}
            </text>
          </g>
        );
      })}

      {/* Premium drag handles on each active leg's OTM flat segment */}
      {legCurves.map((lc, i) => {
        if (!isLegActive(lc.leg)) return null;
        const active = drag?.kind === 'premium' && drag.legIdx === i;
        return (
          <g key={`prem-${i}`}>
            <circle
              cx={lc.handle.x}
              cy={lc.handle.y}
              r={active ? 5 : 3.5}
              fill="#0d1117"
              stroke={LEG_COLOR(lc.leg)}
              strokeWidth={1.5}
              style={{ cursor: 'ns-resize' }}
              onPointerDown={beginDrag('premium', i)}
            />
          </g>
        );
      })}

      {/* Axes labels */}
      <text x={M.l - 4} y={zeroY + 3} textAnchor="end" fontSize={9} fill="#8b949e">0</text>
      <text x={M.l - 4} y={M.t + 8} textAnchor="end" fontSize={9} fill="#8b949e">+${yHi.toFixed(0)}</text>
      <text x={M.l - 4} y={H - M.b} textAnchor="end" fontSize={9} fill="#8b949e">${yLo.toFixed(0)}</text>
      <text x={M.l} y={H - M.b + 14} fontSize={9} fill="#8b949e">${priceMin}</text>
      <text x={xOf(spot)} y={H - M.b + 14} textAnchor="middle" fontSize={9} fill="#58a6ff">spot ${spot}</text>
      <text x={W - M.r} y={H - M.b + 14} textAnchor="end" fontSize={9} fill="#8b949e">${priceMax}</text>
    </svg>
  );
}

function LegLegend({ legs }: { legs: Leg[] }) {
  if (legs.length < 2) return null;
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 px-1 pt-1.5 text-[10px] text-text-secondary">
      <span className="flex items-center gap-1">
        <span className="inline-block w-4 h-0.5 bg-[#e6edf3]" /> combined
      </span>
      {legs.map((leg, i) => {
        const active = isLegActive(leg);
        return (
          <span
            key={i}
            className={`flex items-center gap-1 ${active ? '' : 'opacity-40 line-through'}`}
          >
            <svg width="18" height="4" className="inline-block">
              <line
                x1="0" y1="2" x2="18" y2="2"
                stroke={active ? LEG_COLOR(leg) : '#6b7280'}
                strokeWidth="1.5"
                strokeDasharray={leg.action === 'SELL' ? '4 3' : undefined}
              />
            </svg>
            <span>
              {leg.action === 'BUY' ? '+' : '−'}
              {leg.strike} {leg.side[0]}{leg.side.slice(1).toLowerCase()} @ ${leg.premium.toFixed(2)}
              {!active && ' (off)'}
            </span>
          </span>
        );
      })}
    </div>
  );
}

function LegEditor({
  legs,
  onChange,
}: {
  legs: Leg[];
  onChange: (i: number, patch: Partial<Leg>) => void;
}) {
  return (
    <div className="space-y-1">
      {legs.map((leg, i) => {
        const active = isLegActive(leg);
        const color = active ? LEG_COLOR(leg) : '#6b7280';
        return (
          <div
            key={i}
            className={`flex items-center gap-1.5 text-[11px] font-mono bg-page rounded px-1.5 py-1 ${
              active ? '' : 'opacity-50'
            }`}
            title={active ? undefined : 'Disabled — set strike and premium > 0 to re-enable'}
          >
            <span
              className="inline-block w-1.5 h-4 rounded-sm shrink-0"
              style={{ background: color, opacity: leg.action === 'SELL' ? 0.55 : 1 }}
            />
            <select
              value={leg.action}
              onChange={(e) =>
                onChange(i, { action: e.target.value as Leg['action'] })
              }
              className="bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            >
              <option value="BUY">BUY</option>
              <option value="SELL">SELL</option>
            </select>
            <select
              value={leg.side}
              onChange={(e) =>
                onChange(i, { side: e.target.value as Leg['side'] })
              }
              className="bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            >
              <option value="CALL">CALL</option>
              <option value="PUT">PUT</option>
            </select>
            <label className="text-text-secondary ml-1">K</label>
            <input
              type="number"
              step={0.5}
              min={0}
              value={leg.strike}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) onChange(i, { strike: v });
              }}
              className="w-16 bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            />
            <label className="text-text-secondary ml-1">$</label>
            <input
              type="number"
              step={0.05}
              min={0}
              value={leg.premium}
              onChange={(e) => {
                const v = Number(e.target.value);
                if (Number.isFinite(v) && v >= 0) onChange(i, { premium: v });
              }}
              className="w-16 bg-card border border-border rounded px-1 py-0.5 text-text-primary"
            />
            {!active && (
              <span className="text-[10px] text-text-secondary uppercase tracking-wider ml-1">
                off
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}

const STRATEGIES: Strategy[] = [
  {
    name: 'Long Call',
    verdict: 'BUY_CALL',
    trigger: 'BULLISH • LOW IV (any earnings) or MID IV not near earnings',
    purpose: 'Cheap premium + directional conviction. Unlimited upside, fixed downside.',
    legs: [{ action: 'BUY', side: 'CALL', strike: 100, premium: 3 }],
    maxProfit: 'Unlimited',
    maxLoss: '$3.00 (debit paid)',
    breakeven: '$103',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Long Put',
    verdict: 'BUY_PUT',
    trigger: 'BEARISH • LOW IV (any earnings) or MID IV not near earnings',
    purpose: 'Cheap premium + directional bearish conviction. Profits on a drop.',
    legs: [{ action: 'BUY', side: 'PUT', strike: 100, premium: 3 }],
    maxProfit: '$97 (strike − debit, at $0)',
    maxLoss: '$3.00 (debit paid)',
    breakeven: '$97',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Bull Call Spread',
    verdict: 'BUY_CALL_SPREAD',
    trigger: 'BULLISH • MID IV • near earnings',
    purpose: 'Debit spread caps IV-crush exposure into an event while keeping bullish P&L.',
    legs: [
      { action: 'BUY', side: 'CALL', strike: 100, premium: 3 },
      { action: 'SELL', side: 'CALL', strike: 105, premium: 1 },
    ],
    maxProfit: '$3.00 (width − debit)',
    maxLoss: '$2.00 (net debit)',
    breakeven: '$102',
    priceMin: 90, priceMax: 115,
  },
  {
    name: 'Bear Put Spread',
    verdict: 'BUY_PUT_SPREAD',
    trigger: 'BEARISH • MID IV • near earnings',
    purpose: 'Debit put spread — defined-risk bearish play that blunts IV crush.',
    legs: [
      { action: 'BUY', side: 'PUT', strike: 100, premium: 3 },
      { action: 'SELL', side: 'PUT', strike: 95, premium: 1 },
    ],
    maxProfit: '$3.00 (width − debit)',
    maxLoss: '$2.00 (net debit)',
    breakeven: '$98',
    priceMin: 85, priceMax: 110,
  },
  {
    name: 'Bull Put Spread',
    verdict: 'SELL_PUT_SPREAD',
    trigger: 'BULLISH • HIGH IV • any earnings',
    purpose: 'Credit spread — sell expensive premium while staying bullish. IV crush helps.',
    legs: [
      { action: 'SELL', side: 'PUT', strike: 100, premium: 3 },
      { action: 'BUY', side: 'PUT', strike: 95, premium: 1 },
    ],
    maxProfit: '$2.00 (net credit)',
    maxLoss: '$3.00 (width − credit)',
    breakeven: '$98',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Bear Call Spread',
    verdict: 'SELL_CALL_SPREAD',
    trigger: 'BEARISH • HIGH IV • any earnings',
    purpose: 'Credit spread — collect rich premium while bearish. Profits from sideways/down.',
    legs: [
      { action: 'SELL', side: 'CALL', strike: 100, premium: 3 },
      { action: 'BUY', side: 'CALL', strike: 105, premium: 1 },
    ],
    maxProfit: '$2.00 (net credit)',
    maxLoss: '$3.00 (width − credit)',
    breakeven: '$102',
    priceMin: 85, priceMax: 115,
  },
  {
    name: 'Iron Condor',
    verdict: 'SELL_IRON_CONDOR',
    trigger: 'NEUTRAL • HIGH IV',
    purpose: 'No directional edge + rich premium → harvest theta in a range, defined-risk wings.',
    legs: [
      { action: 'SELL', side: 'PUT', strike: 95, premium: 1.5 },
      { action: 'BUY', side: 'PUT', strike: 90, premium: 0.5 },
      { action: 'SELL', side: 'CALL', strike: 105, premium: 1.5 },
      { action: 'BUY', side: 'CALL', strike: 110, premium: 0.5 },
    ],
    maxProfit: '$2.00 (net credit)',
    maxLoss: '$3.00 (wing width − credit)',
    breakeven: '$93 and $107',
    priceMin: 80, priceMax: 120,
  },
  {
    name: 'Long Straddle',
    verdict: 'BUY_STRADDLE',
    trigger: 'NEUTRAL • LOW IV • near earnings',
    purpose: 'Bet on volatility expansion into a catalyst. Profits on a big move either direction.',
    legs: [
      { action: 'BUY', side: 'CALL', strike: 100, premium: 3 },
      { action: 'BUY', side: 'PUT', strike: 100, premium: 3 },
    ],
    maxProfit: 'Unlimited (above); $94 (below, to $0)',
    maxLoss: '$6.00 (combined debit, at strike)',
    breakeven: '$94 and $106',
    priceMin: 80, priceMax: 120,
  },
];

function StrategyCard({ strategy }: { strategy: Strategy }) {
  const [legs, setLegs] = useState<Leg[]>(() =>
    strategy.legs.map((l) => ({ ...l })),
  );
  const [dirty, setDirty] = useState(false);

  const updateLeg = (i: number, patch: Partial<Leg>) => {
    setLegs((ls) => ls.map((l, j) => (j === i ? { ...l, ...patch } : l)));
    setDirty(true);
  };

  const reset = () => {
    setLegs(strategy.legs.map((l) => ({ ...l })));
    setDirty(false);
  };

  const payoff = useMemo(
    () => computePayoff(legs, strategy.priceMin, strategy.priceMax),
    [legs, strategy.priceMin, strategy.priceMax],
  );
  const { metrics } = payoff;

  return (
    <div className="bg-card border border-border rounded-lg p-4">
      <div className="flex items-baseline justify-between gap-2 mb-1 flex-wrap">
        <h3 className="text-base font-semibold text-text-primary">{strategy.name}</h3>
        <span className="text-[10px] font-mono text-accent-300 bg-accent-900/30 px-1.5 py-px rounded">
          {strategy.verdict}
        </span>
      </div>
      <p className="text-[11px] text-text-secondary mb-2">
        <span className="font-semibold">Fires when:</span> {strategy.trigger}
      </p>
      <p className="text-xs text-text-primary mb-3 leading-snug">{strategy.purpose}</p>
      <div className="bg-page rounded border border-border mb-3 p-1">
        <InteractivePayoff
          legs={legs}
          payoff={payoff}
          priceMin={strategy.priceMin}
          priceMax={strategy.priceMax}
          onLegChange={updateLeg}
        />
        <LegLegend legs={legs} />
      </div>
      <div className="space-y-2 text-xs">
        <div className="flex items-center justify-between">
          <div className="text-text-secondary font-semibold">Legs</div>
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-text-secondary italic">
              drag handles or edit below
            </span>
            {dirty && (
              <button
                type="button"
                onClick={reset}
                className="text-[10px] px-1.5 py-0.5 rounded bg-border/60 text-text-primary hover:bg-border transition-colors"
              >
                Reset
              </button>
            )}
          </div>
        </div>
        <LegEditor legs={legs} onChange={updateLeg} />
        <div className="grid grid-cols-3 gap-2 pt-2 border-t border-border">
          <div>
            <div className="text-[10px] text-text-secondary uppercase">Max profit</div>
            <div className="text-green-400 font-semibold">{metrics.profitStr}</div>
          </div>
          <div>
            <div className="text-[10px] text-text-secondary uppercase">Max loss</div>
            <div className="text-red-400 font-semibold">{metrics.lossStr}</div>
          </div>
          <div>
            <div className="text-[10px] text-text-secondary uppercase">Breakeven</div>
            <div className="text-text-primary font-semibold">{metrics.beStr}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

export default function StrategiesTab() {
  return (
    <div>
      <p className="text-sm text-text-secondary mb-4">
        The deep options analyzer picks one of eight strategies using a lookup of{' '}
        <span className="text-text-primary font-semibold">directional bias</span> ×{' '}
        <span className="text-text-primary font-semibold">IV bucket</span> ×{' '}
        <span className="text-text-primary font-semibold">near-earnings flag</span>.
        Each card below shows the P&amp;L at expiration for a canonical $100 underlying. Strike lines
        are colored per leg (<span className="text-[#2dd4bf]">calls teal</span>, <span className="text-[#f59e0b]">puts amber</span>); current spot is the blue dashed line.
        <br />
        <span className="text-text-primary font-semibold">Interactive:</span> drag a strike handle left/right or a round premium dot up/down, or edit the leg values below each chart — Max P/L and breakeven recompute live.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {STRATEGIES.map((s) => <StrategyCard key={s.verdict} strategy={s} />)}
      </div>
    </div>
  );
}
