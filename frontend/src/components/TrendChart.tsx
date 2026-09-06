import { memo, useMemo } from 'react';
import {
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import type { TrendData } from '../types';
import { SegmentedControl } from './ui/SegmentedControl';
import { Spinner } from './ui/feedback';

interface TrendChartProps {
  data: TrendData | null;
  loading: boolean;
  error: string | null;
  sma: number;
  onSmaChange: (window: number) => void;
}

const SMA_OPTIONS = [3, 5, 10, 20] as const;
type SmaKey = `${(typeof SMA_OPTIONS)[number]}`;
const SMA_SEGMENTS = SMA_OPTIONS.map((w) => ({ key: `${w}` as SmaKey, label: `${w}d` }));

const TICK = { fontSize: 9, fill: '#8b949e' };
const AXIS_LINE = { stroke: '#21262d' };
const MARGIN = { top: 4, right: 4, bottom: 0, left: -10 };
const LEGEND_STYLE = { fontSize: 9, color: '#8b949e' };

function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00');
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

function formatValue(name: string, value: number | null): string {
  if (value == null) return '-';
  if (name.includes('Price') || name.includes('Target')) return `$${value.toFixed(2)}`;
  if (name.includes('Articles')) return String(Math.round(value));
  if (name.includes('Signals')) return String(Math.round(value));
  return value.toFixed(1);
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function CustomTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-[#1c2128] border border-border rounded px-2.5 py-1.5 text-[10px] shadow-lg">
      <p className="text-text-secondary mb-1 font-medium">{label}</p>
      {payload
        .filter((e: { value: number | null }) => e.value != null)
        .map((entry: { name: string; value: number; color: string }, i: number) => (
        <div key={i} className="flex justify-between gap-3">
          <span style={{ color: entry.color }}>{entry.name}</span>
          <span className="font-mono text-text-primary">
            {formatValue(entry.name, entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function TrendChart({ data, loading, error, sma, onSmaChange }: TrendChartProps) {
  const chartData = useMemo(
    () => (data ? data.data.map((d) => ({ ...d, date: formatDate(d.date) })) : []),
    [data],
  );

  if (!data) {
    if (loading) {
      return (
        <div className="flex items-center justify-center py-6">
          <Spinner />
        </div>
      );
    }
    return null;
  }

  if (error || data.data.length === 0) {
    return null;
  }

  const hasMultiplePoints = chartData.length > 1;

  return (
    <div className="space-y-2 relative">
      {/* Overlay spinner keeps the charts mounted (no remount/re-animation) during SMA refetches */}
      {loading && (
        <div className="absolute inset-0 z-10 flex items-center justify-center bg-card/50 rounded">
          <Spinner />
        </div>
      )}

      {/* SMA selector */}
      <div className="flex items-center justify-between">
        <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
          Trends
        </h4>
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-text-secondary mr-1">SMA</span>
          <SegmentedControl
            size="xs"
            options={SMA_SEGMENTS}
            value={`${sma}` as SmaKey}
            onChange={(k) => onSmaChange(Number(k))}
          />
        </div>
      </div>

      {/* Chart 1: Price & Conviction */}
      {hasMultiplePoints && (
        <>
          <div className="text-[9px] text-text-secondary mb-0.5">Price & Conviction</div>
          <ResponsiveContainer width="100%" height={170}>
            <ComposedChart data={chartData} margin={MARGIN}>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis
                dataKey="date"
                tick={TICK}
                tickLine={false}
                axisLine={AXIS_LINE}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="price"
                tick={TICK}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `$${v}`}
                width={45}
              />
              <YAxis
                yAxisId="conviction"
                orientation="right"
                domain={[-100, 100]}
                tick={TICK}
                tickLine={false}
                axisLine={false}
                width={30}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={LEGEND_STYLE} iconSize={8} verticalAlign="top" height={16} />
              <ReferenceLine yAxisId="conviction" y={0} stroke="#21262d" strokeDasharray="3 3" />

              <Line
                yAxisId="price" type="monotone" dataKey="price" name="Price"
                stroke="#e6edf3" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false}
              />
              <Line
                yAxisId="price" type="monotone" dataKey="price_sma" name={`Price SMA(${sma})`}
                stroke="#58a6ff" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls isAnimationActive={false}
              />
              <Line
                yAxisId="price" type="monotone" dataKey="target_price" name="Target"
                stroke="#8b949e" strokeWidth={1} strokeDasharray="2 2" dot={false} connectNulls isAnimationActive={false}
              />

              <Line
                yAxisId="conviction" type="monotone" dataKey="conviction" name="Conviction"
                stroke="#56d364" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false}
              />
              <Line
                yAxisId="conviction" type="monotone" dataKey="conviction_sma" name={`Conv SMA(${sma})`}
                stroke="#2ea043" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </>
      )}

      {/* Chart 2: Signals, Sentiment & Article Count */}
      {hasMultiplePoints && (
        <>
          <div className="text-[9px] text-text-secondary mb-0.5">Signals, Sentiment & News Volume</div>
          <ResponsiveContainer width="100%" height={150}>
            <ComposedChart data={chartData} margin={MARGIN}>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis
                dataKey="date"
                tick={TICK}
                tickLine={false}
                axisLine={AXIS_LINE}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="left"
                tick={TICK}
                tickLine={false}
                axisLine={false}
                width={25}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                domain={[0, 100]}
                tick={TICK}
                tickLine={false}
                axisLine={false}
                width={25}
              />
              <Tooltip content={<CustomTooltip />} />
              <Legend wrapperStyle={LEGEND_STYLE} iconSize={8} verticalAlign="top" height={16} />
              <ReferenceLine yAxisId="right" y={50} stroke="#21262d" strokeDasharray="3 3" />

              {/* Article count as subtle bars */}
              <Bar
                yAxisId="left" dataKey="article_count" name="Articles"
                fill="#30363d" barSize={12} radius={[2, 2, 0, 0]} isAnimationActive={false}
              />

              {/* Signal count */}
              <Line
                yAxisId="left" type="monotone" dataKey="signal_count" name="Signals"
                stroke="#d29922" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false}
              />
              <Line
                yAxisId="left" type="monotone" dataKey="signal_count_sma" name={`Sig SMA(${sma})`}
                stroke="#e3b341" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls isAnimationActive={false}
              />

              {/* Sentiment (0-100 scale on right axis) */}
              <Line
                yAxisId="right" type="monotone" dataKey="sentiment" name="Sentiment"
                stroke="#58a6ff" strokeWidth={1.5} dot={false} connectNulls isAnimationActive={false}
              />
              <Line
                yAxisId="right" type="monotone" dataKey="sentiment_sma" name={`Sent SMA(${sma})`}
                stroke="#388bfd" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls isAnimationActive={false}
              />
            </ComposedChart>
          </ResponsiveContainer>
        </>
      )}

      {/* Sparse data note */}
      {data.data.length < 5 && (
        <p className="text-[10px] text-text-secondary text-center py-1">
          More history builds better trends
        </p>
      )}
    </div>
  );
}

export default memo(TrendChart);
