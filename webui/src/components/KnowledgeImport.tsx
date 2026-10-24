// Knowledge base import dialog (KB phase 5, 2026-10-24): read a web page, a
// GitHub repository or a local file/folder into linked notes ("assimilate"),
// or upload an export (ChatGPT, Claude.ai, MCP memory, Markdown/Obsidian zip,
// a document). Work runs as a background job on the server; this dialog polls
// /api/knowledge/import/{id} and shows the notes it made.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';

export interface KnowledgeImportProps {
  base: string;
  open: boolean;
  onClose: () => void;
  /** Called after a run that saved notes (refresh the graph / list). */
  onDone: () => void;
  /** Optional: open a note in the Knowledge page's side panel. */
  onOpenNote?: (permalink: string) => void;
}

interface ImportNote { permalink: string; title: string; action: 'created' | 'updated' | 'unchanged' }
interface ImportResult {
  base: string;
  source: string;
  kind: string;
  created: number;
  updated: number;
  unchanged: number;
  notes: ImportNote[];
  overview: { permalink: string; title: string } | null;
  diagram: string;
  errors: string[];
  skipped: string[];
  stopped: string;
  duration: number;
}
interface ImportJob {
  id: string;
  status: 'running' | 'done' | 'error';
  progress: string;
  error: string;
  elapsed: number;
  result: ImportResult | null;
}

type Tab = 'link' | 'upload';
const KINDS: { value: string; label: string; hint: string }[] = [
  { value: 'auto', label: 'Detect automatically', hint: 'Recognises the exports below and documents.' },
  { value: 'chatgpt', label: 'ChatGPT export', hint: 'The .zip from ChatGPT → Settings → Data controls → Export (or its conversations.json).' },
  { value: 'claude', label: 'Claude.ai export', hint: 'The .zip from Claude.ai → Settings → Privacy → Export data (conversations and projects).' },
  { value: 'mcp_memory', label: 'MCP memory file', hint: 'memory.json / .jsonl of the MCP memory server: entities become notes, relations links.' },
  { value: 'markdown', label: 'Markdown folder (zip)', hint: 'A zipped folder of .md notes, e.g. an Obsidian vault. Copied into imported/<name>/; nothing is overwritten.' },
  { value: 'document', label: 'Document', hint: 'PDF, Word, Excel, text, Markdown, HTML, CSV, JSON or an image.' },
];
const MAX_UPLOAD_MB = 200;
const LISTED = 30;

const overlay: React.CSSProperties = {
  position: 'fixed', inset: 0, background: 'rgba(0, 0, 0, 0.45)', zIndex: 1000,
  display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
};
const dialog: React.CSSProperties = {
  width: 'min(640px, 100%)', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto',
  background: 'var(--bg-color)', border: '1px solid var(--panel-border)', borderRadius: 12,
  padding: 20, display: 'flex', flexDirection: 'column', gap: 12, boxShadow: '0 12px 40px rgba(0,0,0,0.35)',
};
const row: React.CSSProperties = { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' };
const pre: React.CSSProperties = {
  whiteSpace: 'pre-wrap', fontSize: '0.78rem', maxHeight: 220, overflow: 'auto', margin: 0,
  padding: 10, borderRadius: 8, border: '1px solid var(--panel-border)', background: 'rgba(128,128,128,0.06)',
};

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

const KnowledgeImport: React.FC<KnowledgeImportProps> = ({ base, open, onClose, onDone, onOpenNote }) => {
  const [tab, setTab] = useState<Tab>('link');
  const [source, setSource] = useState('');
  const [maxNotes, setMaxNotes] = useState(40);
  const [kind, setKind] = useState('auto');
  const [file, setFile] = useState<File | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<ImportJob | null>(null);
  const [error, setError] = useState('');
  const [starting, setStarting] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const doneRef = useRef<string | null>(null);

  const running = starting || job?.status === 'running';

  // Poll the job while it runs.
  useEffect(() => {
    if (!jobId) return;
    let stopped = false;
    const poll = async () => {
      try {
        const j = await api<ImportJob>(`/api/knowledge/import/${encodeURIComponent(jobId)}`);
        if (stopped) return;
        setJob(j);
        if (j.status !== 'running') {
          stopped = true;
          clearInterval(iv);
          if (j.status === 'done' && doneRef.current !== j.id) {
            doneRef.current = j.id;
            onDone();
          }
        }
      } catch (e) {
        if (!stopped) setError(errorText(e));
      }
    };
    const iv = setInterval(poll, 1000);
    poll();
    return () => { stopped = true; clearInterval(iv); };
  }, [jobId, onDone]);

  const close = useCallback(() => {
    if (running) return; // the job keeps running on the server; keep the dialog until it ends
    setJob(null); setJobId(null); setError(''); setFile(null);
    if (fileRef.current) fileRef.current.value = '';
    onClose();
  }, [running, onClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, close]);

  const startAssimilate = async () => {
    const s = source.trim();
    if (!s) { setError('Enter a web address, a GitHub repository URL or a file/folder path.'); return; }
    setError(''); setJob(null); setStarting(true);
    try {
      const r = await api<{ job_id: string }>('/api/knowledge/assimilate', {
        method: 'POST', body: JSON.stringify({ base, source: s, max_notes: maxNotes }),
      });
      setJobId(r.job_id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setStarting(false);
    }
  };

  const startUpload = async () => {
    if (!file) { setError('Choose a file first.'); return; }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(`The file is larger than ${MAX_UPLOAD_MB} MB. Import it with: zensynora knowledge import FILE`);
      return;
    }
    setError(''); setJob(null); setStarting(true);
    try {
      const q = new URLSearchParams({ base, kind, filename: file.name });
      const r = await api<{ job_id: string }>(`/api/knowledge/import?${q.toString()}`, {
        method: 'POST', body: file, headers: { 'Content-Type': 'application/octet-stream' },
      });
      setJobId(r.job_id);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setStarting(false);
    }
  };

  const cancel = async () => {
    if (!jobId) return;
    try { await api(`/api/knowledge/import/${encodeURIComponent(jobId)}`, { method: 'DELETE' }); }
    catch (e) { setError(errorText(e)); }
  };

  if (!open) return null;
  const result = job?.result ?? null;
  const kindHint = KINDS.find(k => k.value === kind)?.hint ?? '';

  const noteLink = (n: { permalink: string; title: string }) => (
    onOpenNote
      ? <button type="button" className="chip small-chip" onClick={() => { onOpenNote(n.permalink); close(); }}
          title={n.permalink}>{n.title}</button>
      : <span title={n.permalink}>{n.title} <code className="muted small">{n.permalink}</code></span>
  );

  return (
    <div style={overlay} onMouseDown={e => { if (e.target === e.currentTarget) close(); }}>
      <div style={dialog} role="dialog" aria-modal="true" aria-label="Import into the knowledge base">
        <div style={{ ...row, justifyContent: 'space-between' }}>
          <h3 style={{ margin: 0 }}>Import into “{base}”</h3>
          <button type="button" className="ghost-btn" onClick={close} disabled={running} aria-label="Close">✕</button>
        </div>

        <div className="kb-tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === 'link'} disabled={running}
            className={`settings-tab${tab === 'link' ? ' active' : ''}`} onClick={() => setTab('link')}>From a link or folder</button>
          <button type="button" role="tab" aria-selected={tab === 'upload'} disabled={running}
            className={`settings-tab${tab === 'upload' ? ' active' : ''}`} onClick={() => setTab('upload')}>Upload a file</button>
        </div>

        {tab === 'link' ? (
          <>
            <p className="muted small" style={{ margin: 0 }}>
              A web page, a GitHub repository (<code>https://github.com/owner/repo</code>) or a file/folder on this
              computer (<code>~/Documents/report.pdf</code>). You get an overview note with a diagram and one note per
              part, linked together. Running it again updates the same notes.
            </p>
            <input className="field-input" placeholder="https://… or ~/folder" value={source} disabled={running}
              onChange={e => setSource(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !running) startAssimilate(); }} />
            <div style={row}>
              <label className="muted small" htmlFor="kb-import-max">At most</label>
              <input id="kb-import-max" className="field-input" type="number" min={1} max={500} style={{ width: 90 }}
                value={maxNotes} disabled={running}
                onChange={e => setMaxNotes(Math.max(1, Math.min(500, Number(e.target.value) || 40)))} />
              <span className="muted small">notes</span>
              <span style={{ flex: 1 }} />
              <button type="button" className="primary-btn" disabled={running || !source.trim()} onClick={startAssimilate}>
                {starting ? 'Starting…' : 'Read into notes'}
              </button>
            </div>
          </>
        ) : (
          <>
            <div style={row}>
              <select className="field-input" style={{ width: 'auto' }} value={kind} disabled={running}
                onChange={e => setKind(e.target.value)} aria-label="What the file is">
                {KINDS.map(k => <option key={k.value} value={k.value}>{k.label}</option>)}
              </select>
              <input ref={fileRef} type="file" disabled={running}
                accept=".zip,.json,.jsonl,.md,.markdown,.txt,.pdf,.docx,.xlsx,.csv,.tsv,.html,.htm,.png,.jpg,.jpeg,.gif,.webp,.svg"
                onChange={e => setFile(e.target.files?.[0] ?? null)} />
            </div>
            <p className="muted small" style={{ margin: 0 }}>{kindHint} Up to {MAX_UPLOAD_MB} MB; importing the same export
              again updates the notes instead of duplicating them.</p>
            <div style={{ ...row, justifyContent: 'flex-end' }}>
              <button type="button" className="primary-btn" disabled={running || !file} onClick={startUpload}>
                {starting ? 'Uploading…' : 'Import'}
              </button>
            </div>
          </>
        )}

        {error && <div className="notice error">{error}</div>}

        {job?.status === 'running' && (
          <div className="notice" style={{ ...row, justifyContent: 'space-between' }}>
            <span>⏳ {job.progress || 'working'} · {Math.round(job.elapsed)} s</span>
            <button type="button" className="ghost-btn" onClick={cancel} title="Stop; notes saved so far are kept">Stop</button>
          </div>
        )}

        {job?.status === 'error' && <div className="notice error">{job.error}</div>}

        {job?.status === 'done' && result && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <div className="notice">
              ✅ {result.created} created · {result.updated} updated · {result.unchanged} unchanged
              <span className="muted small"> ({result.kind}, {Math.round(result.duration)} s)</span>
              {result.stopped && <div className="small">Stopped early: {result.stopped}. Run it again to continue.</div>}
            </div>
            {result.overview && <div><b>Overview:</b> {noteLink(result.overview)}</div>}
            {result.notes.length > 0 && (
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {result.notes.filter(n => n.permalink !== result.overview?.permalink).slice(0, LISTED).map(n => (
                  <span key={n.permalink} className="small">{noteLink(n)}</span>
                ))}
                {result.notes.length > LISTED + 1 && <span className="muted small">… and {result.notes.length - LISTED - 1} more</span>}
              </div>
            )}
            {result.diagram && (
              <details>
                <summary className="small">Diagram (Mermaid, also in the overview note)</summary>
                <pre style={pre}>{result.diagram}</pre>
              </details>
            )}
            {result.skipped.length > 0 && (
              <details>
                <summary className="small">Left out ({result.skipped.length})</summary>
                <ul className="small" style={{ margin: 0 }}>{result.skipped.slice(0, 40).map((s, i) => <li key={i}>{s}</li>)}</ul>
              </details>
            )}
            {result.errors.length > 0 && (
              <details open>
                <summary className="small">Problems ({result.errors.length})</summary>
                <ul className="small" style={{ margin: 0 }}>{result.errors.slice(0, 40).map((s, i) => <li key={i}>{s}</li>)}</ul>
              </details>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default KnowledgeImport;
