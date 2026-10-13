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
