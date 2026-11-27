export interface ProcessEvent {
  step: string;
  status: string;
  text: string;
  at: number;
  seconds?: number;
  index?: number;
  total?: number;
  request?: string;
}

export interface MaintenanceProcess {
  id?: string;
  action?: string;
  status?: string;
  error?: string;
  steps?: Record<string, ProcessEvent>;
  events?: ProcessEvent[];
}

const COLORS: Record<string, string> = {
  ok: 'var(--success-color)', failed: 'var(--danger-color)', skipped: '#d29922',
  waiting: '#d29922', running: 'var(--accent-color)', detail: 'var(--text-secondary)',
  'rollback-running': '#d29922', 'rollback-failed': 'var(--danger-color)',
  'rolled-back': 'var(--success-color)', interrupted: 'var(--danger-color)',
};

export default function ProcessProgress({ steps = {}, events = [], error }: {
  steps?: Record<string, ProcessEvent>; events?: ProcessEvent[]; error?: string;
}) {
  if (!events.length && !Object.keys(steps).length && !error) return null;
  const rows = Object.values(steps);
  const finished = rows.filter(row => ['ok', 'skipped', 'rolled-back'].includes(row.status)).length;
  return (
    <div className="settings-card" aria-label="Detailed process progress">
      <h3>Process steps <span className="muted small">{finished} completed or skipped / {rows.length} reported</span></h3>
      <p className="muted small">Green: OK · Red: failed · Yellow: skipped, waiting or rollback · Blue: running</p>
      <ol>
        {rows.map(row => (
          <li key={row.step} style={{ marginBottom: 8 }}>
            <b style={{ color: COLORS[row.status] }}>{row.status.toUpperCase()}</b>{' '}
            {row.index ? `${row.index}/${row.total} ` : ''}{row.step}: {row.text}
            {row.seconds != null && <span className="muted small"> ({row.seconds.toFixed(2)} s)</span>}
          </li>
        ))}
      </ol>
      {error && <div className="notice error">{error}</div>}
      <details open>
        <summary>Detailed event log ({events.length} recent events)</summary>
        <div className="log-tail" role="log" style={{ maxHeight: 300, overflow: 'auto', fontFamily: 'monospace', fontSize: 12 }}>
          {events.map((event, index) => (
            <div key={index} style={{ color: COLORS[event.status], whiteSpace: 'pre-wrap', padding: '2px 0' }}>
              {new Date(event.at * 1000).toLocaleTimeString()} [{event.status.toUpperCase()}] {event.step}: {event.text}
            </div>
          ))}
        </div>
      </details>
    </div>
  );
}
