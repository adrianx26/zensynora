// Semantic search status for the Knowledge page (KB phase 3, 2026-10-24).
import { useCallback, useEffect, useState } from 'react';
import { api } from './api';

export type SearchMode = 'auto' | 'fts' | 'semantic' | 'hybrid';

interface ModelInfo { dim: number; size_mb: number; licence: string; languages: string; known?: boolean }
interface Job { status: 'running' | 'done' | 'error' | 'stopped'; phase: string; done: number; total: number; error: string | null; result: { message?: string } | null }
interface Install { running?: boolean; ok?: boolean | null; error?: string | null; log?: string; command?: string }
export interface SemanticStatus {
  enabled: boolean;
  installed: boolean;
  model: string;
  model_info: ModelInfo;
  install_command: string;
  notes: number;
  embedded: number;
  stale: number;
  chunks: number;
  queued: number;
  embedder: string;
  last_error: string | null;
  ready: boolean;
  reason: string | null;
  in_venv: boolean;
  job: Job | null;
  install: Install;
}

const q = (base: string) => `base=${encodeURIComponent(base)}`;

/** Polls the status (faster while a backfill or an install runs). */
export function useSemanticStatus(base: string, reloadKey: unknown) {
  const [st, setSt] = useState<SemanticStatus | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const refresh = useCallback(() => setTick(t => t + 1), []);
  useEffect(() => {
    let live = true;
    api<SemanticStatus>(`/api/knowledge/semantic/status?${q(base)}`)
      .then(d => { if (live) { setSt(d); setErr(null); } })
      .catch(e => { if (live) setErr((e as Error).message); });
    return () => { live = false; };
  }, [base, reloadKey, tick]);
  const busy = !!(st?.job?.status === 'running' || st?.install?.running || (st && st.queued !== 0));
  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(refresh, 1500);
    return () => window.clearInterval(t);
  }, [busy, refresh]);
  return { st, err, refresh };
}
