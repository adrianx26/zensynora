// Knowledge base health line (KB phase 6, 2026-10-25): live sync (file watcher)
// on/off and its last re-index, index drift, notes waiting for embeddings or
// distillation, open conflicts — with what to do for each warning.
// Data: GET /api/knowledge/health (the same rules as the doctor).
import React, { useEffect, useRef, useState } from 'react';
import { api } from '../api';

interface Warning { id: string; text: string; fix: string }
interface Health {
  base: string;
  ok: boolean;
  warnings: Warning[];
  index: { files: number; indexed: number; missing: number; extra: number; changed: number };
  embeddings: { enabled: boolean; waiting: number; notes: number };
  distill: { enabled: boolean; pending: number; running: boolean };
  conflicts: { open: number };
  links: { pending: number };
  watcher: { enabled: boolean; running: boolean; mode: string | null; last_reindex: number | null; watchfiles_installed: boolean; poll_seconds: number };
}

const POLL_MS = 15000;

function clock(ts: number | null): string {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

const KnowledgeHealth: React.FC<{ base: string; reloadKey: unknown; onReindexed: () => void }> = ({ base, reloadKey, onReindexed }) => {
  const [h, setH] = useState<Health | null>(null);
  const [open, setOpen] = useState(false);
  const lastSeen = useRef<number | null>(null);
  const cb = useRef(onReindexed);
  useEffect(() => { cb.current = onReindexed; }, [onReindexed]);

  useEffect(() => {
    let live = true;
    lastSeen.current = null;
    const get = () => api<Health>(`/api/knowledge/health?base=${encodeURIComponent(base)}`)
      .then(d => {
        if (!live) return;
        setH(d);
        // the watcher re-indexed notes changed on disk: refresh the graph
        const ts = d.watcher?.last_reindex ?? null;
        if (ts && lastSeen.current !== null && ts > lastSeen.current) cb.current();
        lastSeen.current = ts ?? 0;
      })
      .catch(() => { if (live) setH(null); });
    get();
    const t = window.setInterval(() => { if (!document.hidden) get(); }, POLL_MS);
    return () => { live = false; window.clearInterval(t); };
  }, [base, reloadKey]);

  if (!h) return null;
  const w = h.watcher || ({} as Health['watcher']);
  const drift = (h.index?.missing || 0) + (h.index?.extra || 0) + (h.index?.changed || 0);
  const parts: React.ReactNode[] = [];
  parts.push(
    <span key="w" title={w.enabled
      ? `Notes edited by hand are re-indexed automatically (${w.mode === 'watchfiles' ? 'change events' : `checked every ${w.poll_seconds} s`})`
      : 'Turn on Settings → Features → Watch note files to re-index notes edited by hand automatically'}>
      Live sync <b>{w.enabled ? (w.running ? 'on' : 'on (not running)') : 'off'}</b>
      {w.enabled && w.last_reindex ? <span className="muted"> · re-indexed {clock(w.last_reindex)}</span> : null}
    </span>);
  parts.push(drift
    ? <span key="i" style={{ color: '#d29922' }} title="Files and search index differ — press ↻ Re-index">⚠ index: {drift} note(s) out of date</span>
    : <span key="i" className="muted" title={`${h.index?.files ?? 0} files, ${h.index?.indexed ?? 0} indexed`}>index in sync</span>);
  if (h.embeddings?.enabled && h.embeddings.waiting) parts.push(<span key="e">{h.embeddings.waiting} waiting for embeddings</span>);
  if (h.distill?.enabled && h.distill.pending) parts.push(<span key="d">{h.distill.pending} to distil{h.distill.running ? ' (running)' : ''}</span>);

  return (
    <div className="kb-health small" style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 12px', alignItems: 'center', margin: '2px 0 6px' }}>
      {parts.map((p, i) => <React.Fragment key={i}>{i > 0 && <span className="muted">·</span>}{p}</React.Fragment>)}
      {h.warnings.length > 0 && (
        <button type="button" className="chip small-chip" onClick={() => setOpen(o => !o)} aria-expanded={open}
          style={{ color: '#d29922' }}>{h.warnings.length} warning{h.warnings.length === 1 ? '' : 's'} {open ? '▴' : '▾'}</button>
      )}
      {open && h.warnings.length > 0 && (
        <ul style={{ flexBasis: '100%', margin: '4px 0 0', paddingLeft: 18 }}>
          {h.warnings.map(x => <li key={x.id}>{x.text} — <span className="muted">{x.fix}</span></li>)}
        </ul>
      )}
    </div>
  );
};

export default KnowledgeHealth;
