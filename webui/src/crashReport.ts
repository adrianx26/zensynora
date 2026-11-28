// Reports Web UI crashes to the server log so Doctor → Errors shows them.
// A render error unmounts the whole app (black screen); the server is the
// only place the user can see what happened afterwards.
import { getApiBase } from './api';

const reported = new Set<string>();

export function reportCrash(error: unknown, componentStack = ''): void {
  const err = error instanceof Error ? error : new Error(String(error));
  let view = '';
  try { view = localStorage.getItem('zensynora_view') || ''; } catch { /* storage may be blocked */ }
  const message = `${err.name}: ${err.message}`.slice(0, 1000);
  const signature = `${view}|${message}`;
  if (reported.has(signature)) return;
  reported.add(signature);
  fetch(`${getApiBase()}/api/doctor/ui-crash`, {
    method: 'POST',
    keepalive: true,
    headers: { 'Content-Type': 'application/json', 'X-ZenSynora-UI': '1' },
    body: JSON.stringify({
      message,
      stack: (err.stack || '').slice(0, 8000),
      component_stack: componentStack.slice(0, 8000),
      view: view.slice(0, 40),
    }),
  }).catch(() => { /* the server is unreachable; nothing else to do */ });
}
