// Tools page. 2026-10-31: "Your tools" — the Python tools in ~/.myclaw/TOOLBOX
// (made by agents or by you): create, edit, turn on/off, delete (to the trash),
// make a skill of a tool that only holds instructions, repair the registry, and
// export / import tools and skills as a file. Below: every tool agents can call.
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, downloadFile } from '../api';
import { useTransferImport } from './TransferImport';

interface Tool {
  name: string;
  description: string;
  sandboxed?: boolean;
}

interface Skipped { name: string; reason: string }

interface OwnTool {
  name: string;
  description: string;
  version: string;
  author: string;
  enabled: boolean;
  file: boolean;
  loaded: boolean;
  problem: string | null;
  instructions_only: boolean;
  last_modified?: string;
}

interface RepairAction { kind: string; name: string; text: string }

interface Draft { name: string; description: string; code: string; documentation: string; isNew: boolean }

interface SkillPreview { tool: string; name: string; description: string; body: string; keepTool: boolean }

const TEMPLATE = (name: string) => `def ${name || 'my_tool'}(text: str) -> str:
    """What this tool does, in one line (agents see it).

    text: what the argument means
    """
    return text.upper()
`;

const ToolsView: React.FC = () => {
  const [tools, setTools] = useState<Tool[]>([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState<Skipped[]>([]);
  const [own, setOwn] = useState<OwnTool[]>([]);
  const [repairPlan, setRepairPlan] = useState<RepairAction[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [preview, setPreview] = useState<SkillPreview | null>(null);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [all, mine] = await Promise.all([
        api<{ tools: Tool[]; skipped?: Skipped[] }>('/api/tools'),
        api<{ tools: OwnTool[]; repair: RepairAction[] }>('/api/toolbox'),
      ]);
      setTools(all.tools);
      setSkipped(all.skipped || []);
      setOwn(mine.tools);
      setRepairPlan(mine.repair);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const imp = useTransferImport(load);

  const act = async (fn: () => Promise<unknown>, ok?: string) => {
    setNotice(null);
    setBusy(true);
    try {
      await fn();
      if (ok) setNotice({ kind: 'ok', text: ok });
      await load();
      return true;
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
      return false;
    } finally {
      setBusy(false);
    }
  };

  const edit = (t: OwnTool) => act(async () => {
    const full = await api<OwnTool & { code: string; documentation: string }>(`/api/toolbox/${encodeURIComponent(t.name)}`);
    setPreview(null);
    setDraft({ name: full.name, description: full.description, code: full.code, documentation: full.documentation, isNew: false });
  });

  const save = async () => {
    if (!draft) return;
    let problem: string | null = null;
    const ok = await act(async () => {
      const r = await api<{ problem: string | null }>('/api/toolbox', {
        method: 'POST',
        body: JSON.stringify({ name: draft.name, code: draft.code, description: draft.description,
          documentation: draft.documentation, create: draft.isNew }),
      });
      problem = r.problem;
    });
    if (!ok) return;
    setDraft(null);
    setNotice(problem
      ? { kind: 'error', text: `${draft.name} saved but not loaded: ${problem}` }
      : { kind: 'ok', text: `${draft.name} saved${draft.isNew ? '' : ' (previous version kept in TOOLBOX/.trash)'} — agents can use it now.` });
  };

  const remove = (t: OwnTool) => {
    if (!window.confirm(`Delete the tool ${t.name}? Its files move to TOOLBOX/.trash (you can get them back from there).`)) return;
    act(() => api(`/api/toolbox/${encodeURIComponent(t.name)}`, { method: 'DELETE' }), `${t.name} deleted (kept in TOOLBOX/.trash).`);
  };

  const toggle = (t: OwnTool) => act(
    () => api(`/api/toolbox/${encodeURIComponent(t.name)}/enabled`, { method: 'PUT', body: JSON.stringify({ enabled: !t.enabled }) }),
    `${t.name} turned ${t.enabled ? 'off' : 'on'}.`);

  const showSkillPreview = (t: OwnTool) => act(async () => {
    const p = await api<{ name: string; description: string; body: string }>(`/api/toolbox/${encodeURIComponent(t.name)}/skill-preview`);
    setDraft(null);
    setPreview({ tool: t.name, name: p.name, description: p.description, body: p.body, keepTool: false });
  });

  const toSkill = async () => {
    if (!preview) return;
    const ok = await act(() => api(`/api/toolbox/${encodeURIComponent(preview.tool)}/to-skill`, {
      method: 'POST', body: JSON.stringify({ skill_name: preview.name, keep_tool: preview.keepTool }),
    }), `Skill ${preview.name} made from ${preview.tool}${preview.keepTool ? '' : ' (the tool is turned off, not deleted)'}. `
      + 'Give it to agents on the Skills page.');
    if (ok) setPreview(null);
  };

  const repair = () => act(() => api('/api/toolbox/repair', { method: 'POST', body: JSON.stringify({ dry_run: false }) }),
    'Repaired. Nothing was erased — removed files and entries are in TOOLBOX/.trash.');

  const exportTools = (names: string[] | null) => act(() => downloadFile(
    `/api/transfer/export?skills=${names ? `&tools=${encodeURIComponent(names.join(','))}` : ''}`,
    'zensynora-tools.json'));

  const exportAll = () => act(() => downloadFile('/api/transfer/export', 'zensynora-export.json'));

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return tools;
    return tools.filter(t => t.name.toLowerCase().includes(q) || t.description.toLowerCase().includes(q));
  }, [tools, query]);
  const ownNames = new Set(own.map(t => t.name));

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <div className="card-head">
            <h3>Your tools <span className="badge">{own.length}</span></h3>
            <div className="row-actions">
              {imp.button}
              <button className="ghost-btn" disabled={busy || own.length === 0} onClick={() => exportTools(null)}
                title="Download all your tools as one file">Export tools</button>
              <button className="ghost-btn" disabled={busy} onClick={exportAll}
                title="Download all your tools and skills as one file (to move them to another computer)">Export all</button>
              <button className="primary-btn" onClick={() => { setPreview(null); setDraft({ name: '', description: '', code: TEMPLATE(''), documentation: '', isNew: true }); }}>+ New tool</button>
            </div>
          </div>
          <p className="muted">
            Python tools made by agents (<code>register_tool</code>) or by you, in <code>~/.myclaw/TOOLBOX</code>. They run in a
            separate, locked-down process. Deleting or changing one keeps the previous version in <code>TOOLBOX/.trash</code>.
          </p>
          {repairPlan.length > 0 && (
            <div className="notice">
              <b>Things to repair ({repairPlan.length}):</b>
              <ul className="skipped-tools">{repairPlan.map((a, i) => <li key={i}>{a.text}</li>)}</ul>
              <button className="ghost-btn" disabled={busy} onClick={repair}>Repair</button>
            </div>
          )}
        </div>

        {imp.panel}

        {draft && (
          <div className="settings-card">
            <h3>{draft.isNew ? 'New tool' : `Edit ${draft.name}`}</h3>
            <div className="agent-editor-grid">
              <div className="field"><label className="field-label">Name (Python name, e.g. weather_report)</label>
                <input className="field-input mono" value={draft.name} disabled={!draft.isNew} autoFocus placeholder="my_tool"
                  onChange={e => {
                    const name = e.target.value.replace(/[^A-Za-z0-9_]+/g, '_');
                    const untouched = draft.code === TEMPLATE(draft.name);
                    setDraft({ ...draft, name, code: untouched ? TEMPLATE(name) : draft.code });
                  }} /></div>
              <div className="field" style={{ gridColumn: 'span 2' }}><label className="field-label">Description (agents see this)</label>
                <input className="field-input" value={draft.description} placeholder="Leave empty to use the first line of the docstring"
                  onChange={e => setDraft({ ...draft, description: e.target.value })} /></div>
            </div>
            <div className="field"><label className="field-label">Code — must define <code>def {draft.name || 'name'}(...)</code></label>
              <textarea className="field-input mono" rows={18} spellCheck={false} value={draft.code}
                onChange={e => setDraft({ ...draft, code: e.target.value })} /></div>
            <details>
              <summary className="muted">Documentation (README, optional)</summary>
              <textarea className="field-input mono" rows={8} value={draft.documentation}
                onChange={e => setDraft({ ...draft, documentation: e.target.value })} />
            </details>
            <div className="save-actions">
              <button className="ghost-btn" onClick={() => setDraft(null)}>Cancel</button>
              <button className="primary-btn" disabled={busy || !draft.name || !draft.code.trim()} onClick={save}>Save tool</button>
            </div>
          </div>
        )}

        {preview && (
          <div className="settings-card">
            <h3>Make {preview.tool} a skill</h3>
            <p className="muted">
              A skill is instructions an agent reads when a task needs them — better than a tool that only returns a fixed text.
              The tool's code is read, never run. Check the text below; you can change it on the Skills page later.
            </p>
            <div className="field"><label className="field-label">Skill name</label>
              <input className="field-input" value={preview.name}
                onChange={e => setPreview({ ...preview, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]+/g, '-') })} /></div>
            <p className="muted small">{preview.description}</p>
            <pre className="skill-body">{preview.body}</pre>
            <label className="mini-toggle">
              <input type="checkbox" checked={preview.keepTool} onChange={e => setPreview({ ...preview, keepTool: e.target.checked })} />
              Keep the tool turned on as well
            </label>
            <div className="save-actions">
              <button className="ghost-btn" onClick={() => setPreview(null)}>Cancel</button>
              <button className="primary-btn" disabled={busy || !preview.name} onClick={toSkill}>Make skill</button>
            </div>
          </div>
        )}

        {own.length > 0 && (
          <div className="settings-card">
            <div className="tool-list">
              {own.map(t => (
                <div className="tool-item own-tool" key={t.name}>
                  <div>
                    <code>{t.name}</code> <span className="muted small">v{t.version}</span>
                    {!t.enabled && <span className="badge">off</span>}
                    <span className="badge" title={t.author === 'user' ? 'Written or edited by you' : t.author === 'imported' ? 'Imported from a file' : 'Made by an agent'}>{t.author === 'user' ? 'yours' : t.author}</span>
                    {t.instructions_only && <span className="badge" title="Only returns a fixed text — works better as a skill">instructions only</span>}
                  </div>
                  <div>
                    <span className="muted">{t.description || '—'}</span>
                    {t.problem && t.enabled && <div className="notice error small">Not loaded: {t.problem}</div>}
                    <div className="row-actions">
                      <button className="ghost-btn" disabled={busy || !t.file} onClick={() => edit(t)}>Edit</button>
                      <button className="ghost-btn" disabled={busy} onClick={() => toggle(t)}>{t.enabled ? 'Turn off' : 'Turn on'}</button>
                      <button className="ghost-btn" disabled={busy || !t.file} onClick={() => exportTools([t.name])}>Export</button>
                      <button className={`ghost-btn ${t.instructions_only ? 'primary-outline' : ''}`} disabled={busy || !t.file}
                        onClick={() => showSkillPreview(t)} title="Turn this tool into a skill (Markdown instructions)">Make skill</button>
                      <button className="ghost-btn danger" disabled={busy} onClick={() => remove(t)}>Delete</button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="settings-card">
          <h3>All agent tools <span className="badge">{tools.length}</span></h3>
          <p className="muted">Everything the agents can call while answering. Ask for it in chat, e.g. “search the web for…”.</p>
          <input className="field-input" placeholder="Filter tools…" value={query} onChange={e => setQuery(e.target.value)} />
          {error && <div className="notice error">{error}</div>}
          {skipped.length > 0 && (
            <div className="notice">
              <b>Own tools not loaded ({skipped.length}):</b>
              <ul className="skipped-tools">{skipped.map(s => <li key={s.name}><code>{s.name}</code> — {s.reason}</li>)}</ul>
              Edit them above, ask an agent to rewrite them with <code>register_tool</code>, or turn them off.
            </div>
          )}
          <div className="tool-list">
            {filtered.map(t => (
              <div className="tool-item" key={t.name}>
                <code>{t.name}</code>
                {t.sandboxed && <span className="badge" title="Made by an agent or you: runs in a separate, locked-down process">sandboxed</span>}
                {ownNames.has(t.name) && <span className="badge">yours</span>}
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
