// Shared update helpers (v0.7.4): status type and the restart-and-wait helper.
import { api } from './api';

export interface UpdateStatus {
  running_version: string;
  installed_version: string;
  restart_needed: boolean;
  managed: boolean;
  not_managed_reason?: string | null;
  update_running: boolean;
  action?: 'update' | 'rollback' | null;
  started?: number | null;
  step?: number | null;
  steps?: number | null;
  step_text?: string;
  result?: string | null;
  tail?: string;
  log?: string | null;
  exit_code?: number | null;
  finished_ok?: boolean;
  latest_version?: string | null;
  update_available?: boolean;
  check_error?: string;
  history?: { action?: string; at?: string; version?: string; from_version?: string }[];
  releases?: string[];
  active_release?: string | null;
}

/** Restart the server with the installed version, wait until it answers, reload the page. */
export async function restartServer(onState?: (s: string) => void): Promise<void> {
  await api('/api/update/restart', { method: 'POST' });
  onState?.('Restarting…');
  const deadline = Date.now() + 90_000;
  await new Promise(r => setTimeout(r, 2500));
  while (Date.now() < deadline) {
    try {
      const s = await api<UpdateStatus>('/api/update/status');
      if (!s.restart_needed) {
        window.location.reload();
        return;
      }
    } catch { /* still starting */ }
    await new Promise(r => setTimeout(r, 1500));
  }
  throw new Error('The server did not come back within 90 s. Start it again with `zensynora webui`.');
}
