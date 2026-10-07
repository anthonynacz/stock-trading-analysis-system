// Momentum Radar API wrapper. Kept beside the radar components (not in
// utils/api.ts) so the radar port stays self-contained; it uses the same
// axios client, so auth headers and the 401 interceptor apply.

import api from '../../utils/api';
import type { RadarHistory, RadarResponse, RadarScanRequest, RadarTickRow } from '../../types';

/** Latest radar snapshot plus `stale`; `{ status: 'no_data' }` before the first scan. */
export const getRadar = () => api.get<RadarResponse>('/radar').then((r) => r.data);

/** Ask the worker for a scan now; 409/429 carry `{ error, message }` in `detail`. */
export const requestScan = () => api.post<RadarScanRequest>('/radar/scan').then((r) => r.data);

/** Stays on the radar that ended in the last `days` days, newest first. */
export const getRadarHistory = (days: number, ticker?: string) =>
  api
    .get<RadarHistory>('/radar/history', { params: { days, ...(ticker ? { ticker } : {}) } })
    .then((r) => r.data);

/** One ticker's member / heating rows of a session (oldest first). */
export const getRadarTickerTicks = (ticker: string, session: string) =>
  api
    .get<{ ticker: string; session: string; ticks: RadarTickRow[] }>(`/radar/ticker/${encodeURIComponent(ticker)}`, {
      params: { session },
    })
    .then((r) => r.data);
