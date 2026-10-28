import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api';

interface Tool {
  name: string;
  description: string;
  sandboxed?: boolean;
}

interface Skipped { name: string; reason: string }

const ToolsView: React.FC = () => {
  const [tools, setTools] = useState<Tool[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Skipped[]>([]);

  useEffect(() => {
    api<{ tools: Tool[]; skipped?: Skipped[] }>('/api/tools')
      .then(r => { setTools(r.tools); setSkipped(r.skipped || []); })
      .catch(e => setError((e as Error).message));
  }, []);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return tools;
    return tools.filter(t => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q));
  }, [tools, query]);

  return (
    <div className="settings-view">
      <div className="settings-body">
        <div className="settings-card">
          <h3>Agent tools <span className="badge">{tools.length}</span></h3>
          <p className="muted">Everything the agents can call while answering. Ask for it in chat, e.g. “search the web for…”.</p>
          <input className="field-input" placeholder="Filter tools…" value={query} onChange={e => setQuery(e.target.value)} />
          {error && <div className="notice error">{error}</div>}
          {skipped.length > 0 && (
            <div className="notice">
              <b>Own tools not loaded ({skipped.length}):</b>
              <ul className="skipped-tools">{skipped.map(s => <li key={s.name}><code>{s.name}</code> — {s.reason}</li>)}</ul>
              Ask an agent to rewrite them with <code>register_tool</code>, or disable them.
            </div>
          )}
          <div className="tool-list">
            {filtered.map(t => (
              <div className="tool-item" key={t.name}>
                <code>{t.name}</code>{t.sandboxed && <span className="badge" title="Made by an agent: runs in a separate, locked-down process">sandboxed</span>}
                <span className="muted">{t.description || '—'}</span>
              </div>
            ))}
            {!error && filtered.length === 0 && <div className="muted">No tools match “{query}”.</div>}
          </div>
        </div>
      </div>
    </div>
  );
};

export default ToolsView;
