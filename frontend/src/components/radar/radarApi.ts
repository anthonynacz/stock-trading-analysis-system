// Momentum Radar API wrapper. Kept beside the radar components (not in
// utils/api.ts) so the radar port stays self-contained; it uses the same
// axios client, so auth headers and the 401 interceptor apply.

import api from '../../utils/api';
import type { RadarResponse } from '../../types';

/** Latest radar snapshot plus `stale`; `{ status: 'no_data' }` before the first scan. */
export const getRadar = () => api.get<RadarResponse>('/radar').then((r) => r.data);
