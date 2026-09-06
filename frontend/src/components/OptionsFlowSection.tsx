import type { OptionsSnapshot } from '../types';

export type Sentiment = 'bullish' | 'bearish' | 'neutral' | 'elevated';

export const SENTIMENT_STYLES: Record<Sentiment, { bg: string; text: string; label: string }> = {
  bullish:  { bg: 'bg-green-900/40', text: 'text-green-400', label: 'Bullish' },
  bearish:  { bg: 'bg-red-900/40',   text: 'text-red-400',   label: 'Bearish' },
  neutral:  { bg: 'bg-gray-800/60',  text: 'text-text-secondary', label: 'Neutral' },
  elevated: { bg: 'bg-amber-900/40', text: 'text-amber-400', label: 'Elevated' },
};

export function SentimentTag({ sentiment }: { sentiment: Sentiment }) {
  const s = SENTIMENT_STYLES[sentiment];
  return (
    <span className={`px-1.5 py-px rounded text-[9px] font-semibold uppercase ${s.bg} ${s.text}`}>
      {s.label}
    </span>
  );
}

export function rateIvRank(v: number): Sentiment {
  if (v < 25) return 'bullish';   // cheap options — good for buyers
  if (v < 50) return 'neutral';
  if (v < 75) return 'elevated';  // expensive
  return 'bearish';               // very expensive — sellers' market
}

export function ratePutCallRatio(v: number): Sentiment {
  if (v < 0.7) return 'bullish';  // call-heavy flow
  if (v <= 1.0) return 'neutral';
  if (v <= 1.5) return 'elevated'; // moderately put-heavy
  return 'bearish';                // strongly put-heavy
}

export function rateVolumeDominance(calls: number, puts: number): Sentiment {
  if (calls + puts === 0) return 'neutral';
  const ratio = calls / (calls + puts);
  if (ratio > 0.6) return 'bullish';
  if (ratio < 0.4) return 'bearish';
  return 'neutral';
}

export function StatRow({ label, value, mono, sentiment }: {
  label: string;
  value: string;
  mono?: boolean;
  sentiment?: Sentiment;
}) {
  return (
    <div className="flex justify-between items-center text-xs gap-2">
      <span className="text-text-secondary">{label}</span>
      <div className="flex items-center gap-1.5">
        {sentiment && <SentimentTag sentiment={sentiment} />}
        <span className={`${sentiment ? SENTIMENT_STYLES[sentiment].text : 'text-text-primary'} ${mono ? 'font-mono' : ''}`}>
          {value}
        </span>
      </div>
    </div>
  );
}

export type OptionsFlowData = Pick<
  OptionsSnapshot,
  'iv_rank' | 'put_call_ratio' | 'total_call_volume' | 'total_put_volume' | 'unusual_activity' | 'unusual_activity_detail'
>;

/** Options Flow block shared by TickerDetail (OptionsSnapshot) and ResearchDetail (options_data). */
export function OptionsFlowSection({ data }: { data: OptionsFlowData }) {
  const flowSentiment = rateVolumeDominance(data.total_call_volume ?? 0, data.total_put_volume ?? 0);
  return (
    <div className="space-y-1.5">
      <h4 className="text-[10px] font-semibold text-text-secondary uppercase tracking-wider">Options Flow</h4>
      {data.iv_rank != null && (
        <StatRow label="IV Rank" value={`${data.iv_rank.toFixed(1)}%`} mono sentiment={rateIvRank(data.iv_rank)} />
      )}
      {data.put_call_ratio != null && (
        <StatRow
          label="Put/Call Ratio"
          value={data.put_call_ratio.toFixed(2)}
          mono
          sentiment={ratePutCallRatio(data.put_call_ratio)}
        />
      )}
      {data.total_call_volume != null && (
        <StatRow
          label="Call Volume"
          value={data.total_call_volume.toLocaleString()}
          mono
          sentiment={flowSentiment === 'bullish' ? 'bullish' : 'neutral'}
        />
      )}
      {data.total_put_volume != null && (
        <StatRow
          label="Put Volume"
          value={data.total_put_volume.toLocaleString()}
          mono
          sentiment={flowSentiment === 'bearish' ? 'bearish' : 'neutral'}
        />
      )}
      {data.unusual_activity && (
        <div className="mt-1">
          <span
            className="px-2 py-0.5 rounded text-[10px] font-semibold bg-amber-900/60 text-amber-400"
            title={data.unusual_activity_detail ?? undefined}
          >
            UNUSUAL ACTIVITY
          </span>
          {data.unusual_activity_detail && (
            <p className="text-[10px] text-text-secondary mt-0.5">{data.unusual_activity_detail}</p>
          )}
        </div>
      )}
    </div>
  );
}
