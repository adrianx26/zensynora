// Latency levels for the model test (Settings → Agents / Model).

// Round-trip time of one short answer (includes connecting and, for local
// servers, loading the model the first time — test twice to see the warm time).
export const LATENCY_LOW_MS = 1500;
export const LATENCY_HIGH_MS = 5000;

export type LatencyLevel = 'low' | 'med' | 'high' | 'fail';

export function latencyLevel(ok: boolean, ms: number): LatencyLevel {
  if (!ok) return 'fail';
  if (ms < LATENCY_LOW_MS) return 'low';
  if (ms < LATENCY_HIGH_MS) return 'med';
  return 'high';
}

export const LEVEL_TEXT: Record<LatencyLevel, string> = { low: 'Low', med: 'Medium', high: 'High', fail: 'Failed' };

export function formatMs(ms: number): string {
  return ms >= 10000 ? `${(ms / 1000).toFixed(1)} s` : `${ms.toLocaleString()} ms`;
}
