interface SignalBadgeProps {
  signal: string;
  points: number;
}

function getSignalColor(signal: string): string {
  const s = signal.toLowerCase();
  if (s.includes('upgrade') || s.includes('beat')) return 'bg-green-900/60 text-green-400';
  if (s.includes('downgrade') || s.includes('miss')) return 'bg-red-900/60 text-red-400';
  if (s.includes('volume')) return 'bg-blue-900/60 text-blue-400';
  if (s.includes('theta') || s.includes('decay')) return 'bg-amber-900/60 text-amber-400';
  if (s.includes('crush') || s.includes('vega')) return 'bg-orange-900/60 text-orange-400';
  if (s.includes('iv') || s.includes('implied')) return 'bg-amber-900/60 text-amber-400';
  if (s.includes('news')) return 'bg-accent-900/60 text-accent-400';
  if (s.includes('wick') || s.includes('gap-down') || s.includes('near low') || s.includes('near high'))
    return 'bg-orange-900/60 text-orange-400';
  return 'bg-gray-800 text-text-secondary';
}

export default function SignalBadge({ signal, points }: SignalBadgeProps) {
  const colorClasses = getSignalColor(signal);
  const prefix = points > 0 ? '+' : '';

  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium ${colorClasses}`}>
      {signal}
      <span className="opacity-80">{prefix}{points}</span>
    </span>
  );
}
