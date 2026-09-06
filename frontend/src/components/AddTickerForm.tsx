import { ReactNode, useState } from 'react';
import { getApiErrorMessage } from '../utils/api';
import { ErrorBox } from './ui/feedback';

const INPUT_SIZE = {
  xs: 'w-28 px-2 py-1 text-xs',
  sm: 'w-36 px-3 py-1.5 text-sm',
} as const;

/**
 * Self-contained "add ticker" form: owns input/busy/error state so keystrokes
 * do not re-render the parent page. `onAdd` receives the trimmed, uppercased
 * ticker and should throw on failure (the backend message is shown inline).
 */
export function AddTickerForm({
  onAdd,
  placeholder = 'Add ticker...',
  disabled = false,
  extra,
  buttonLabel = 'Add',
  size = 'xs',
  className = '',
}: {
  onAdd: (ticker: string) => Promise<void>;
  placeholder?: string;
  disabled?: boolean;
  /** Extra controls rendered between the input and the button (e.g. a sector select). */
  extra?: ReactNode;
  buttonLabel?: string;
  size?: keyof typeof INPUT_SIZE;
  className?: string;
}) {
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const ticker = input.trim().toUpperCase();
    if (!ticker || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onAdd(ticker);
      setInput('');
    } catch (e: unknown) {
      setError(getApiErrorMessage(e, 'Failed to add ticker'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={className}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
        className="flex items-center gap-2"
      >
        <input
          type="text"
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setError(null);
          }}
          placeholder={placeholder}
          disabled={disabled}
          className={`${INPUT_SIZE[size]} bg-card border border-border rounded text-text-primary placeholder-text-secondary focus:border-new-entrant disabled:opacity-40`}
        />
        {extra}
        <button
          type="submit"
          disabled={disabled || busy || !input.trim()}
          className="btn-primary px-2 py-1"
        >
          {busy ? '...' : buttonLabel}
        </button>
      </form>
      {error && <ErrorBox message={error} size="xs" className="mt-2" />}
    </div>
  );
}

export default AddTickerForm;
