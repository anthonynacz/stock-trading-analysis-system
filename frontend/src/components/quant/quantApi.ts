// Quant layer API wrapper (backend/api/quant_routes.py). Same axios client, so auth headers and the 401
// interceptor apply.

import api from '../../utils/api';
import type { QuantJob, QuantStatus, QuantVolHistory, QuantVolResponse } from '../../types';

/** Latest forecast row per ticker. `rows: []` before the forecast job has run. */
export const getQuantVol = () => api.get<QuantVolResponse>('/quant/vol').then((r) => r.data);

/** One ticker's forecast history (current model version). Unknown ticker: `rows: []`. */
export const getQuantVolHistory = (ticker: string, days: number) =>
  api
    .get<QuantVolHistory>(`/quant/vol/${encodeURIComponent(ticker)}`, { params: { days } })
    .then((r) => r.data);

/** Coverage, last run per job, running flags and the latest out-of-sample evaluation (null before one). */
export const getQuantStatus = () => api.get<QuantStatus>('/quant/status').then((r) => r.data);

/** Start one job now (admin only): 202, 403, 409 while it runs. */
export const runQuantJob = (job: QuantJob) =>
  api.post<{ job: QuantJob; status: string }>(`/quant/run/${job}`).then((r) => r.data);
