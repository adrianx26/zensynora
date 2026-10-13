import React, { useEffect, useMemo, useState } from 'react';
import { api } from '../api';

interface Tool {
  name: string;
  description: string;
}

const ToolsView: React.FC = () => {
  const [tools, setTools] = useState<Tool[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ tools: Tool[] }>('/api/tools')
      .then(r => setTools(r.tools))
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
          <div className="tool-list">
            {filtered.map(t => (
              <div className="tool-item" key={t.name}>
                <code>{t.name}</code>
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
