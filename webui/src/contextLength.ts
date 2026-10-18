// Helpers for the per-agent context length (Settings → Agents).

export const CONTEXT_FLOOR = 1024;
// What Ollama uses when no num_ctx is sent (recent versions: 4096).
export const OLLAMA_DEFAULT_CTX = 4096;

export interface ContextEstimate {
  parts: { prompt: number; skills: number; team: number; tools: number };
  tool_count: number;
  fixed: number;
  history: number;
  knowledge: number;
  reply_min: number;
  reply_recommended: number;
  min: number;
  recommended: number;
  model_max: number | null;
  model_max_source: 'provider' | 'library' | 'unknown';
  model_max_error?: string;
  slider_max: number;
  chars_per_token: number;
}

/** 8192 → "8k", 6656 → "6.5k", 131072 → "128k", 1048576 → "1M". */
export function fmtTokens(n: number): string {
  if (n >= 1048576 && n % 1048576 === 0) return `${n / 1048576}M`;
  if (n >= 1024) {
    const k = n / 1024;
    return `${Number.isInteger(k) ? k : k.toFixed(1)}k`;
  }
  return String(n);
}

/** Nice values for the slider: steps of 512 below 16k, 1024 below 64k, 4096 above. */
export function snapTokens(v: number, max: number): number {
  const step = v < 16384 ? 512 : v < 65536 ? 1024 : 4096;
  const s = Math.round(v / step) * step;
  return Math.max(CONTEXT_FLOOR, Math.min(max, s));
}

export function contextLevel(value: number, est: ContextEstimate): { kind: 'low' | 'mid' | 'ok' | 'over'; text: string } {
  if (est.model_max && value > est.model_max) {
    return { kind: 'over', text: `Above this model's maximum (${fmtTokens(est.model_max)}) — the server will cut it down or refuse.` };
  }
  if (value < est.min) {
    return { kind: 'low', text: `Too small: the prompt and tools alone need about ${fmtTokens(est.fixed)} — the agent would forget the conversation or fail.` };
  }
  if (value < est.recommended) {
    return { kind: 'mid', text: 'Works for short exchanges; older messages and knowledge results will be left out sooner.' };
  }
  return { kind: 'ok', text: 'Enough room for the prompt, tools, knowledge, recent chat and the reply.' };
}
