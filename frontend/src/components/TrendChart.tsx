import {
  ComposedChart,
  Line,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from 'recharts';
import type { TrendData } from '../types';

interface TrendChartProps {
  data: TrendData | null;
  loading: boolean;
  error: string | null;
  sma: number;
  onSmaChange: (window: number) => void;
}

const SMA_OPTIONS = [3, 5, 10, 20] as const;

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

export default function TrendChart({ data, loading, error, sma, onSmaChange }: TrendChartProps) {
  if (loading) {
    return (
      <div className="flex items-center justify-center py-6">
        <div className="w-4 h-4 border-2 border-text-secondary border-t-transparent rounded-full animate-spin" />
      </div>
    );
  }

  if (error || !data || data.data.length === 0) {
    return null;
  }

  const chartData = data.data.map((d) => ({
    ...d,
    date: formatDate(d.date),
  }));

  const hasMultiplePoints = chartData.length > 1;

  return (
    <div className="space-y-2">
      {/* SMA selector */}
      <div className="flex items-center justify-between">
        <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">
          Trends
        </h4>
        <div className="flex items-center gap-1">
          <span className="text-[10px] text-text-secondary mr-1">SMA</span>
          {SMA_OPTIONS.map((w) => (
            <button
              key={w}
              onClick={() => onSmaChange(w)}
              className={`px-1.5 py-0.5 text-[10px] rounded transition-colors ${
                sma === w
                  ? 'bg-accent-900/60 text-accent-300 border border-accent-600/60'
                  : 'bg-card border border-border text-text-secondary hover:text-text-primary'
              }`}
            >
              {w}d
            </button>
          ))}
        </div>
      </div>

      {/* Chart 1: Price & Conviction */}
      {hasMultiplePoints && (
        <>
          <div className="text-[9px] text-text-secondary mb-0.5">Price & Conviction</div>
          <ResponsiveContainer width="100%" height={170}>
            <ComposedChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 9, fill: '#8b949e' }}
                tickLine={false}
                axisLine={{ stroke: '#21262d' }}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="price"
                tick={{ fontSize: 9, fill: '#8b949e' }}
                tickLine={false}
                axisLine={false}
                tickFormatter={(v: number) => `$${v}`}
                width={45}
              />
              <YAxis
                yAxisId="conviction"
                orientation="right"
                domain={[-100, 100]}
                tick={{ fontSize: 9, fill: '#8b949e' }}
                tickLine={false}
                axisLine={false}
                width={30}
              />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine yAxisId="conviction" y={0} stroke="#21262d" strokeDasharray="3 3" />

              <Line
                yAxisId="price" type="monotone" dataKey="price" name="Price"
                stroke="#e6edf3" strokeWidth={1.5} dot={false} connectNulls
              />
              <Line
                yAxisId="price" type="monotone" dataKey="price_sma" name={`Price SMA(${sma})`}
                stroke="#58a6ff" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls
              />
              <Line
                yAxisId="price" type="monotone" dataKey="target_price" name="Target"
                stroke="#8b949e" strokeWidth={1} strokeDasharray="2 2" dot={false} connectNulls
              />

              <Line
                yAxisId="conviction" type="monotone" dataKey="conviction" name="Conviction"
                stroke="#56d364" strokeWidth={1.5} dot={false} connectNulls
              />
              <Line
                yAxisId="conviction" type="monotone" dataKey="conviction_sma" name={`Conv SMA(${sma})`}
                stroke="#2ea043" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls
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
            <ComposedChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -10 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#21262d" vertical={false} />
              <XAxis
                dataKey="date"
                tick={{ fontSize: 9, fill: '#8b949e' }}
                tickLine={false}
                axisLine={{ stroke: '#21262d' }}
                interval="preserveStartEnd"
              />
              <YAxis
                yAxisId="left"
                tick={{ fontSize: 9, fill: '#8b949e' }}
                tickLine={false}
                axisLine={false}
                width={25}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                domain={[0, 100]}
                tick={{ fontSize: 9, fill: '#8b949e' }}
                tickLine={false}
                axisLine={false}
                width={25}
              />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine yAxisId="right" y={50} stroke="#21262d" strokeDasharray="3 3" />

              {/* Article count as subtle bars */}
              <Bar
                yAxisId="left" dataKey="article_count" name="Articles"
                fill="#30363d" barSize={12} radius={[2, 2, 0, 0]}
              />

              {/* Signal count */}
              <Line
                yAxisId="left" type="monotone" dataKey="signal_count" name="Signals"
                stroke="#d29922" strokeWidth={1.5} dot={false} connectNulls
              />
              <Line
                yAxisId="left" type="monotone" dataKey="signal_count_sma" name={`Sig SMA(${sma})`}
                stroke="#e3b341" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls
              />

              {/* Sentiment (0-100 scale on right axis) */}
              <Line
                yAxisId="right" type="monotone" dataKey="sentiment" name="Sentiment"
                stroke="#58a6ff" strokeWidth={1.5} dot={false} connectNulls
              />
              <Line
                yAxisId="right" type="monotone" dataKey="sentiment_sma" name={`Sent SMA(${sma})`}
                stroke="#388bfd" strokeWidth={1} strokeDasharray="4 2" dot={false} connectNulls
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
