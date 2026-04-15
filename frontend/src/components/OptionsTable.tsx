import type { SuggestedOption } from '../types';

interface OptionsTableProps {
  options: SuggestedOption[];
}

export default function OptionsTable({ options }: OptionsTableProps) {
  if (options.length === 0) {
    return (
      <p className="text-text-secondary text-xs py-2">No options suggested</p>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary border-b border-border">
            <th className="text-left py-1.5 pr-3 font-medium">Type</th>
            <th className="text-right py-1.5 px-3 font-medium">Strike</th>
            <th className="text-left py-1.5 px-3 font-medium">Expiry</th>
            <th className="text-right py-1.5 px-3 font-medium">Est. Premium</th>
            <th className="text-right py-1.5 px-3 font-medium">Delta</th>
            <th className="text-right py-1.5 px-3 font-medium">Theta</th>
            <th className="text-right py-1.5 px-3 font-medium">Vega</th>
            <th className="text-right py-1.5 px-3 font-medium">Breakeven</th>
            <th className="text-left py-1.5 pl-3 font-medium">Strategy</th>
          </tr>
        </thead>
        <tbody>
          {options.map((opt) => (
            <tr key={opt.id} className="border-b border-border/50 hover:bg-border/20">
              <td className={`py-1.5 pr-3 font-semibold ${opt.contract_type === 'CALL' ? 'text-green-400' : 'text-red-400'}`}>
                {opt.contract_type}
              </td>
              <td className="py-1.5 px-3 text-right font-mono text-text-primary">
                ${opt.strike.toFixed(2)}
              </td>
              <td className="py-1.5 px-3 text-text-primary">
                {opt.expiry}
              </td>
              <td className="py-1.5 px-3 text-right font-mono text-text-primary">
                {opt.premium_estimate != null ? `$${opt.premium_estimate.toFixed(2)}` : '-'}
              </td>
              <td className="py-1.5 px-3 text-right font-mono text-text-primary">
                {opt.delta_estimate != null ? opt.delta_estimate.toFixed(2) : '-'}
              </td>
              <td className={`py-1.5 px-3 text-right font-mono ${opt.theta_estimate != null && opt.premium_estimate != null && opt.premium_estimate > 0 && Math.abs(opt.theta_estimate) / opt.premium_estimate > 0.02 ? 'text-amber-400' : 'text-text-primary'}`}>
                {opt.theta_estimate != null ? opt.theta_estimate.toFixed(4) : '-'}
              </td>
              <td className="py-1.5 px-3 text-right font-mono text-text-primary">
                {opt.vega_estimate != null ? opt.vega_estimate.toFixed(4) : '-'}
              </td>
              <td className="py-1.5 px-3 text-right font-mono text-text-primary">
                {opt.breakeven_price != null ? `$${opt.breakeven_price.toFixed(2)}` : '-'}
              </td>
              <td className="py-1.5 pl-3 text-text-secondary">
                {opt.strategy ?? '-'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
