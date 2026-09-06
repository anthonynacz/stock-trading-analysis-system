import { ActionBadge } from '../../components/ui/badges';
import { DocTable } from './shared';

const THRESHOLDS = [
  { action: 'STRONG_BUY', range: '60 to 100', desc: 'High conviction bullish — multiple strong signals aligned' },
  { action: 'BUY', range: '30 to 59', desc: 'Moderate bullish conviction' },
  { action: 'HOLD', range: '-15 to 29', desc: 'Insufficient directional conviction' },
  { action: 'SELL', range: '-30 to -16', desc: 'Moderate bearish conviction' },
  { action: 'STRONG_SELL', range: '-100 to -31', desc: 'High conviction bearish — multiple strong signals aligned' },
];
type Threshold = (typeof THRESHOLDS)[number];

export default function ClassificationTab() {
  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-base font-semibold text-text-primary mb-2">Conviction Score Range</h3>
        <p className="text-xs text-text-secondary mb-4">
          Conviction scores range from -100 to +100. They are computed by stacking all applicable signals
          from the categories listed in the Signal Stacking tab. The final score determines the recommendation action.
          Repeated SELL/STRONG_SELL actions can remove a ticker from the watchlist — see Watchlist Scoring › Toxic Removal.
        </p>
        <DocTable<Threshold>
          rows={THRESHOLDS}
          rowKey={(t) => t.action}
          columns={[
            {
              key: 'action',
              header: 'Action',
              headerClass: 'w-40',
              render: (t) => <ActionBadge action={t.action} size="sm" className="inline-block" />,
            },
            { key: 'range', header: 'Score Range', align: 'center', headerClass: 'w-32', className: 'font-mono text-text-primary' },
            { key: 'desc', header: 'Description', className: 'text-text-secondary' },
          ]}
        />
      </div>
    </div>
  );
}
