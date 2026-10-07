import { useEffect, useState } from 'react';
import type { RadarScanRequest } from '../../types';
import { getApiErrorMessage } from '../../utils/api';
import { requestScan } from './radarApi';
import { fmtEt, toMs } from './clock';

const POLL_MS = 3_000;
const SHOW_RESULT_MS = 120_000;
const TTL_MS = 180_000; // = backend _RADAR_SCAN_TTL_S

/** A scan or recompute request is pending or running (and not expired). */
export const requestActive = (r: RadarScanRequest | null | undefined, now: number) =>
  !!r && (r.status === 'pending' || r.status === 'running') && now - (toMs(r.requested_at) ?? 0) <= TTL_MS;

function outcome(r: RadarScanRequest): { text: string; tone: string } {
  if (r.status === 'done' && r.kind === 'rebuild') {
    const res = r.result ?? {};
    const secs = res.duration_ms ? ` in ${(res.duration_ms / 1000).toFixed(1)} s` : '';
    return {
      text: `Recomputed the session with the new sensitivity${secs}: ${res.members ?? '?'} on the radar now${r.message ? ` · ${r.message}` : ''}`,
      tone: 'text-green-300',
    };
  }
  if (r.status === 'done') {
    const res = r.result ?? {};
    const inn = res.entered?.length ?? 0;
    const out = res.exited?.length ?? 0;
    const secs = res.duration_ms ? ` in ${(res.duration_ms / 1000).toFixed(1)} s` : '';
    const moves = inn || out ? ` (+${inn} / −${out})` : ', no change';
    const bar = r.tick_id ? ` · bar to ${fmtEt(toMs(r.tick_id))}` : '';
    return {
      text: `Scanned${secs}: ${res.members ?? '?'} on the radar${moves}${bar}${r.message ? ` · ${r.message}` : ''}`,
      tone: 'text-green-300',
    };
  }
  return { text: r.message || 'The scan did not run.', tone: r.status === 'refused' ? 'text-text-secondary' : 'text-red-300' };
}

/**
 * "Scan now": asks the radar worker for an extra scan of the latest completed
 * 5-minute bar (fresh quotes and option data). While the request is pending or
 * running the page polls every 3 s; the outcome stays visible for 2 minutes.
 */
export default function ScanNowButton({
  request,
  canScan,
  closedHint,
  refetch,
}: {
  request: RadarScanRequest | null | undefined;
  canScan: boolean;
  closedHint: string;
  refetch: () => void;
}) {
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const busy = sending || requestActive(request, now);

  useEffect(() => {
    if (!busy) return;
    const id = setInterval(() => {
      setNow(Date.now());
      refetch();
    }, POLL_MS);
    return () => clearInterval(id);
  }, [busy, refetch]);

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 15_000);
    return () => clearInterval(id);
  }, []);

  const scan = async () => {
    setSending(true);
    setError(null);
    try {
      await requestScan();
      refetch();
    } catch (err) {
      setError(getApiErrorMessage(err, 'Could not request a scan'));
    } finally {
      setSending(false);
      setNow(Date.now());
    }
  };

  const finished = toMs(request?.finished_at);
  const recent = request && !busy && finished !== null && now - finished < SHOW_RESULT_MS ? outcome(request) : null;
  const rebuild = request?.kind === 'rebuild';
  const label = sending
    ? 'Requesting…'
    : request?.status === 'running' && busy
      ? rebuild
        ? 'Recomputing the session…'
        : 'Scanning…'
      : busy
        ? 'Waiting for the scanner…'
        : 'Scan now';

  return (
    <div className="flex items-center gap-2 flex-wrap min-w-0">
      <button
        type="button"
        onClick={scan}
        disabled={busy || !canScan}
        title={
          canScan
            ? 'Run an extra scan now: the latest completed 5-minute bar, fresh quotes and option data. Takes a few seconds.'
            : closedHint
        }
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded border border-accent-500/50 bg-accent-500/10 text-accent-200 text-xs font-semibold hover:bg-accent-500/20 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        <span aria-hidden="true" className={busy ? 'inline-block animate-spin' : ''}>
          ⟳
        </span>
        {label}
      </button>
      <span role="status" aria-live="polite" className="text-xs leading-snug min-w-0">
        {error ? <span className="text-red-300">{error}</span> : recent ? <span className={recent.tone}>{recent.text}</span> : null}
      </span>
    </div>
  );
}
