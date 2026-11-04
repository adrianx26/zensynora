// Doctor AI helpers (2026-11-04): cached explanations and their keys.
import { useCallback, useEffect, useState } from 'react';
import { api } from './api';

export interface Explanation { text: string; at: string; how: 'asked' | 'automatic' }
export type Explanations = Record<string, Explanation>;

export const explainKey = (kind: 'error' | 'check', key: string, level = '') =>
  kind === 'error' ? `error:${level}:${key}` : `check:${key}`;

/** Cached explanations (also the ones written automatically in the background). */
export function useExplanations(): [Explanations, () => void] {
  const [ex, setEx] = useState<Explanations>({});
  const load = useCallback(() => { api<Explanations>('/api/doctor/ai/explanations').then(setEx).catch(() => setEx({})); }, []);
  useEffect(() => { load(); }, [load]);
  return [ex, load];
}
