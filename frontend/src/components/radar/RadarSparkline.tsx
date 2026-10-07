import { memo } from 'react';
import type { RadarMember } from '../../types';
import { fmtMove, fmtRadarPrice } from './model';

const W = 104;
const H = 32;
const P = 4;

// Literal class maps so Tailwind's content scan keeps the stroke/fill utilities.
const LINE = { up: 'stroke-green-400', down: 'stroke-red-400' } as const;
const DOT = { up: 'fill-green-400', down: 'fill-red-400' } as const;

/**
 * Price path of a member: grey for the half hour before entry, the direction
 * colour since; the dashed line and the ring mark the entry price, the dot the
 * last close. A missing or broken `spark` draws a flat dashed placeholder.
 */
function RadarSparkline({ m }: { m: RadarMember }) {
  const raw = m.spark?.p ?? [];
  const p = raw.slice(0, 80);
  const valid = p.length > 1 && p.every((v) => typeof v === 'number' && Number.isFinite(v));
  const label = `${m.ticker} price path, ${fmtMove(m.move_since_entry_pct)} since entry`;

  if (!valid) {
    return (
      <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block shrink-0">
        <line x1={P} x2={W - P} y1={H / 2} y2={H / 2} className="stroke-border" strokeDasharray="2 3" />
      </svg>
    );
  }

  const last = p.length - 1;
  const ei = Math.max(0, Math.min(last, Math.round(m.spark?.entry_i ?? 0)));
  const entry = m.entry_price ?? p[ei];
  const lo = Math.min(...p, entry);
  let hi = Math.max(...p, entry);
  if (hi === lo) hi = lo + 1e-6;
  const x = (i: number) => P + (i * (W - 2 * P)) / last;
  const y = (v: number) => P + ((hi - v) * (H - 2 * P)) / (hi - lo);
  const pts = (a: number, b: number) =>
    p
      .slice(a, b + 1)
      .map((v, i) => `${x(a + i).toFixed(1)},${y(v).toFixed(1)}`)
      .join(' ');

  return (
    <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="block shrink-0">
      <title>{`Entry ${fmtRadarPrice(entry)}, last ${fmtRadarPrice(m.last_price ?? p[last])}`}</title>
      <line x1={P} x2={W - P} y1={y(entry)} y2={y(entry)} className="stroke-gray-600" strokeDasharray="2 3" />
      {ei > 0 && (
        <polyline
          points={pts(0, ei)}
          fill="none"
          className="stroke-gray-500"
          strokeWidth={1.5}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      {ei < last && (
        <polyline
          points={pts(ei, last)}
          fill="none"
          className={LINE[m.direction]}
          strokeWidth={2}
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      )}
      <circle cx={x(ei)} cy={y(p[ei])} r={2.5} className="fill-card stroke-gray-300" strokeWidth={1.2} />
      <circle cx={x(last)} cy={y(p[last])} r={3} className={`${DOT[m.direction]} stroke-card`} strokeWidth={1.5} />
    </svg>
  );
}

export default memo(RadarSparkline);
