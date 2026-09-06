import { ReactNode } from 'react';

// Literal class maps (not template strings) so Tailwind's content scan keeps them.
const SPINNER_SIZE = {
  3: 'w-3 h-3',
  3.5: 'w-3.5 h-3.5',
  4: 'w-4 h-4',
  5: 'w-5 h-5',
} as const;

const SPINNER_TONE = {
  muted: 'border-text-secondary',
  accent: 'border-accent-400',
  amber: 'border-amber-400',
} as const;

export type SpinnerSize = keyof typeof SPINNER_SIZE;
export type SpinnerTone = keyof typeof SPINNER_TONE;

export function Spinner({
  size = 4,
  tone = 'muted',
  className = '',
}: {
  size?: SpinnerSize;
  tone?: SpinnerTone;
  className?: string;
}) {
  return (
    <div
      className={`${SPINNER_SIZE[size]} border-2 ${SPINNER_TONE[tone]} border-t-transparent rounded-full animate-spin shrink-0 ${className}`}
      aria-hidden="true"
    />
  );
}

/** Centred spinner with an optional label. Pass `label={null}` for a bare spinner. */
export function LoadingRow({
  label = 'Loading…',
  py = 'py-8',
  size = 5,
  tone = 'muted',
  className = '',
}: {
  label?: string | null;
  py?: string;
  size?: SpinnerSize;
  tone?: SpinnerTone;
  className?: string;
}) {
  return (
    <div className={`flex items-center justify-center ${py} ${className}`} role="status">
      <Spinner size={size} tone={tone} />
      {label !== null && <span className="ml-2 text-sm text-text-secondary">{label}</span>}
    </div>
  );
}

const ERROR_SIZE = {
  xs: 'px-2 py-1.5 text-xs',
  sm: 'px-3 py-3 text-sm',
} as const;

export function ErrorBox({
  message,
  size = 'sm',
  className = '',
}: {
  message: string;
  size?: keyof typeof ERROR_SIZE;
  className?: string;
}) {
  return (
    <div
      className={`bg-red-900/20 border border-red-900/40 rounded text-red-400 ${ERROR_SIZE[size]} ${className}`}
      role="alert"
    >
      {message}
    </div>
  );
}

export function EmptyCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`bg-card border border-border rounded-lg p-8 text-center text-sm text-text-secondary ${className}`}>
      {children}
    </div>
  );
}

/** Full-viewport loading state (route guards, app boot). */
export function PageSpinner({ label = 'Loading…' }: { label?: string | null }) {
  return (
    <div className="min-h-screen bg-page flex items-center justify-center text-text-secondary text-sm">
      <Spinner size={4} className="mr-2" />
      {label}
    </div>
  );
}
