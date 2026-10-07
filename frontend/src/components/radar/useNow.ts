import { useEffect, useState } from 'react';

/**
 * Wall-clock time that advances every `intervalMs` while the tab is visible,
 * so "updated N min ago", freshness and the session state stay honest between
 * radar polls. It also jumps to the current time when `resetKey` changes (a
 * new snapshot arrived) and when the tab becomes visible again.
 */
export function useNow(intervalMs: number, resetKey?: unknown): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    setNow(Date.now());
    const tick = () => {
      if (!document.hidden) setNow(Date.now());
    };
    const id = setInterval(tick, intervalMs);
    document.addEventListener('visibilitychange', tick);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [intervalMs, resetKey]);
  return now;
}
