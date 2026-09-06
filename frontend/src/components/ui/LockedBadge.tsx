/** Amber tier-gate chip; `uppercase` renders e.g. "🔒 PRO+". */
export function LockedBadge({ minTier, className = '' }: { minTier: string; className?: string }) {
  return (
    <span
      className={`text-[9px] font-bold uppercase tracking-wider px-1.5 py-px rounded bg-amber-900/40 text-amber-300 border border-amber-500/30 ${className}`}
    >
      🔒 {minTier}+
    </span>
  );
}

export default LockedBadge;
