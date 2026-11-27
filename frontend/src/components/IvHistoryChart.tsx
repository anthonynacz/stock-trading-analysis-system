import { useEffect, useState } from 'react';
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import type { IvHistoryPoint } from '../types';
import { getApiErrorMessage, getIvHistory } from '../utils/api';
import { PALETTE } from '../utils/theme';
import { LoadingRow } from './ui/feedback';

const TICK = { fontSize: 9, fill: '#8b949e' };
const AXIS_LINE = { stroke: '#21262d' };
const MIN_POINTS = 20;

const fmtDay = (d: string) => {
  const x = new Date(`${d}T00:00:00`);
  return `${x.getMonth() + 1}/${x.getDate()}`;
};

/** The clean daily 30-day IV (iv_daily) against realised volatility, with the window median. */
export function IvHistoryChart({ ticker, medianPct }: { ticker: string; medianPct?: number | null }) {
  const [points, setPoints] = useState<IvHistoryPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setPoints(null);
    setError(null);
    getIvHistory(ticker)
      .then((p) => live && setPoints(p))
      .catch((e) => live && setError(getApiErrorMessage(e, 'IV history unavailable')));
    return () => {
      live = false;
    };
  }, [ticker]);

  if (error) return <p className="text-[11px] text-text-secondary">{error}</p>;
  if (!points) return <LoadingRow label="Loading IV history" />;
  if (points.length < MIN_POINTS) {
    return (
      <p className="text-[11px] text-text-secondary">
        IV history builds up from one clean reading per trading day (15:00 ET): {points.length} so far. The chart
        appears at {MIN_POINTS}.
      </p>
    );
  }
  const data = points.map((p) => ({ ...p, label: fmtDay(p.day) }));
  return (
    <div className="h-40">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -8 }}>
          <CartesianGrid stroke="#21262d" strokeDasharray="3 3" />
          <XAxis dataKey="label" tick={TICK} axisLine={AXIS_LINE} tickLine={false} minTickGap={24} />
          <YAxis tick={TICK} axisLine={AXIS_LINE} tickLine={false} unit="%" width={44} />
          <Tooltip
            contentStyle={{ background: '#1c2128', border: '1px solid #21262d', fontSize: 10 }}
            formatter={(v: number, name: string) => [`${v.toFixed(1)}%`, name]}
          />
          <Legend wrapperStyle={{ fontSize: 9, color: '#8b949e' }} />
          {medianPct ? <ReferenceLine y={medianPct} stroke={PALETTE.grayMuted} strokeDasharray="4 3" /> : null}
          <Line type="monotone" dataKey="iv30_pct" name="30-day IV" stroke={PALETTE.blue} dot={false} strokeWidth={1.5} />
          <Line
            type="monotone"
            dataKey="hv20_pct"
            name="Realised vol (20d)"
            stroke={PALETTE.amber}
            dot={false}
            strokeWidth={1}
            strokeDasharray="4 2"
            connectNulls
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
