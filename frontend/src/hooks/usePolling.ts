import { useEffect } from 'react';

/**
 * Calls `fn({ background: true })` every `ms` while the tab is visible.
 * Ticks are skipped while hidden; on return a single catch-up call fires
 * only if a full interval has elapsed since the last run. `ms === null`
 * disables polling (e.g. historical dates).
 */
export function usePolling(
  fn: (opts: { background: true }) => void,
  ms: number | null,
): void {
  useEffect(() => {
    if (ms == null) return;
    // The owning hook fetches on mount / param change, so start the clock now.
    let lastRun = Date.now();
    const run = () => {
      lastRun = Date.now();
      fn({ background: true });
    };
    const tick = () => {
      if (!document.hidden) run();
    };
    let id = setInterval(tick, ms);
    const onVisibility = () => {
      if (!document.hidden && Date.now() - lastRun >= ms) {
        // Restart the clock so the next scheduled tick doesn't double-fire.
        clearInterval(id);
        id = setInterval(tick, ms);
        run();
      }
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      clearInterval(id);
      document.removeEventListener('visibilitychange', onVisibility);
    };
  }, [fn, ms]);
}
