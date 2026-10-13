// Shared helpers for talking to the ZenSynora backend.

export function getApiBase(): string {
  const { protocol, hostname, port } = window.location;
  // Vite dev server (5173) → backend on 8000; otherwise same origin.
  const apiPort = port === '5173' || port === '' ? '8000' : port;
  return `${protocol}//${hostname}:${apiPort}`;
}

export function getWsBase(): string {
  const { protocol, hostname, port } = window.location;
  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
  const apiPort = port === '5173' || port === '' ? '8000' : port;
  return `${wsProtocol}//${hostname}:${apiPort}`;
}

const ADMIN_KEY = 'zensynora_admin_key';

export function getAdminKey(): string {
  try { return sessionStorage.getItem(ADMIN_KEY) || ''; } catch { return ''; }
}

export function setAdminKey(key: string) {
  try {
    if (key) sessionStorage.setItem(ADMIN_KEY, key);
    else sessionStorage.removeItem(ADMIN_KEY);
  } catch { /* ignore */ }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T = unknown>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  const key = getAdminKey();
  if (key) headers['X-API-Key'] = key;
  const res = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail);
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export interface ProbeResult {
  ok: boolean;
  models: string[];
  error?: string;
  latency_ms?: number;
  url?: string;
}

export interface SystemHealth {
  llm: { provider: string; model: string; ok: boolean; error?: string | null; latency_ms?: number | null; model_available?: boolean | null };
  knowledge: { ok: boolean; entities?: number; observations?: number; relations?: number; error?: string };
  mcp: { enabled: boolean; servers: number };
  swarm: { enabled: boolean };
  channels: { telegram: boolean; whatsapp: boolean };
  tools: number;
  config_file_exists: boolean;
  websocket_clients: number;
  version?: string;
}

// ── v0.7.1: channels, jobs, models, backups, medic proposals ─────────────────

export interface Channel {
  id: string;
  name: string;
  kind: 'channel' | 'dm';
  topic: string;
  members: string[];
  reply_mode: 'lead' | 'all' | 'parallel';
  last_text?: string;
  last_id?: number | null;
}

/** A stored chat message. role "event" = agent-to-agent activity (meta.type says which). */
export interface ChatMsg {
  id: number;
  channel_id: string;
  role: 'user' | 'agent' | 'system' | 'event';
  sender: string;
  text: string;
  meta: Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  created: number;
}

export interface AgentInfo {
  name: string;
  model: string;
  provider?: string;
  description?: string;
  system_prompt?: string;
}

export interface Job {
  id: string;
  name: string;
  prompt: string;
  agent: string;
  schedule: 'manual' | 'once' | 'interval' | 'daily' | 'weekly';
  every_minutes: number;
  at_time: string;
  days: string;
  run_at: number | null;
  enabled: boolean;
  channel_id: string;
  success_criteria: string;
  auto_evaluate: boolean;
  fresh_context: boolean;
  timeout_s: number;
  retries: number;
  created_by: string;
  next_run: number | null;
  last_run: number | null;
  last_status: string;
  running?: boolean;
  stats?: { runs: number; success_rate: number | null; avg_score: number | null; avg_ms: number | null };
}

export interface JobRun {
  id: number;
  job_id: string;
  kind: 'run' | 'bench';
  trigger: string;
  status: 'running' | 'ok' | 'error' | 'timeout' | 'cancelled' | 'interrupted';
  started: number;
  finished: number | null;
  ms: number | null;
  provider: string;
  model: string;
  output: string;
  error: string;
  score: number | null;
  evaluation: Record<string, any> | null; // eslint-disable-line @typescript-eslint/no-explicit-any
  attempt: number;
}

export interface ModelChoice { provider: string; model: string }

export interface ModelsOverview {
  default: ModelChoice;
  rotation: { enabled: boolean; strategy: 'fallback' | 'round_robin'; entries: ModelChoice[] };
  providers: { name: string; label: string; local: boolean; custom: boolean; configured: boolean }[];
}

export interface BackupInfo {
  name: string;
  bytes: number;
  mtime: number;
  created?: string;
  label?: string;
  files?: Record<string, number>;
  error?: string;
  note?: string;
  version?: string;
  parts?: string[];
  valid?: boolean;
}

export interface Proposal {
  id: string;
  title: string;
  reason: string;
  author: string;
  status: 'pending' | 'approved' | 'applying' | 'applied' | 'failed' | 'rejected';
  risk: 'low' | 'high';
  created: number;
  updated?: number;
  files?: Record<string, string>;
  diff?: string;
  notes?: string[];
  history?: { at: number; action: string; by: string; note?: string }[];
  result?: { ok: boolean; note: string; log: string; log_tail: string };
}

/** "3 min ago" style, for timestamps in seconds. */
export function ago(ts?: number | null): string {
  if (!ts) return '—';
  const s = Math.round(Date.now() / 1000 - ts);
  const future = s < 0;
  const a = Math.abs(s);
  const txt = a < 60 ? `${a}s` : a < 3600 ? `${Math.round(a / 60)} min` : a < 86400 ? `${Math.round(a / 3600)} h` : `${Math.round(a / 86400)} d`;
  return future ? `in ${txt}` : `${txt} ago`;
}

export function fmtTime(ts?: number | null): string {
  if (!ts) return '—';
  return new Date(ts * 1000).toLocaleString();
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
