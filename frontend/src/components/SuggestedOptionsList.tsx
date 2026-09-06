import type { SuggestedOption } from '../types';
import { fmtPrice, fmtStrike } from '../utils/format';

/** Compact one-line-per-contract list: type · strike · expiry · premium. */
export function SuggestedOptionsList({ options }: { options: SuggestedOption[] }) {
  return (
    <div className="space-y-1">
      {options.map((opt, i) => (
        <div key={opt.id ?? i} className="text-xs flex items-center gap-2">
          <span className={opt.contract_type === 'CALL' ? 'text-green-400 font-semibold' : 'text-red-400 font-semibold'}>
            {opt.contract_type ?? '—'}
          </span>
          <span className="font-mono text-text-primary">{fmtStrike(opt.strike)}</span>
          <span className="text-text-secondary">{opt.expiry ?? '—'}</span>
          {opt.premium_estimate != null && (
            <span className="font-mono text-text-secondary ml-auto">{fmtPrice(opt.premium_estimate)}</span>
          )}
        </div>
      ))}
    </div>
  );
}
