// Backups page (v0.7.1): manual and automatic backups of settings, API keys,
// the knowledge base, conversations and jobs — create, download, check,
// restore (a safety backup is made first), import and delete.
import React, { useCallback, useEffect, useState } from 'react';
import { ago, api, fmtBytes, getAdminKey, getApiBase, type BackupInfo } from '../api';

interface BackupSettings {
  auto_enabled: boolean; interval_hours: number; keep: number;
  include_knowledge: boolean; include_memory: boolean; include_jobs: boolean; before_update: boolean; dir: string;
}

interface ListResponse {
  backups: BackupInfo[];
  folder: string;
  parts: string[];
  default_parts: string[];
  settings: BackupSettings;
}

const PART_LABELS: Record<string, string> = {
  settings: 'Settings & API keys',
  knowledge: 'Knowledge base',
  memory: 'Conversations & chat channels',
  jobs: 'Jobs, own tools, skills & rules',
};

interface Props { onRestored?: () => void }

const BackupsView: React.FC<Props> = ({ onRestored }) => {
  const [data, setData] = useState<ListResponse | null>(null);
  const [parts, setParts] = useState<string[]>([]);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [restoreParts, setRestoreParts] = useState<Record<string, string[]>>({});

  const load = useCallback(async () => {
    try {
      const res = await api<ListResponse>('/api/backups');
      setData(res);
      setParts(p => (p.length ? p : res.default_parts));
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const act = async (label: string, fn: () => Promise<string | void>) => {
    setBusy(label);
    setNotice(null);
    try {
      const msg = await fn();
      if (msg) setNotice({ kind: 'ok', text: msg });
      await load();
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    } finally {
      setBusy(null);
    }
  };

  const create = () => act('create', async () => {
    const info = await api<BackupInfo>('/api/backups', { method: 'POST', body: JSON.stringify({ parts, note }) });
    setNote('');
    return `Backup ${info.name} created (${fmtBytes(info.bytes)}).`;
  });

  const verify = (b: BackupInfo) => act(`verify-${b.name}`, async () => {
    const r = await api<{ ok: boolean; problems: string[] }>(`/api/backups/${encodeURIComponent(b.name)}/verify`, { method: 'POST' });
    if (!r.ok) throw new Error(`Backup is damaged: ${r.problems.slice(0, 3).join('; ')}`);
    return `${b.name}: every file matches its checksum.`;
  });

  const restore = (b: BackupInfo) => {
    const chosen = restoreParts[b.name] || b.parts || [];
    if (!window.confirm(`Restore ${chosen.map(p => PART_LABELS[p] || p).join(', ')} from ${b.name}?\n\nYour current files are backed up first, so you can undo this.`)) return;
    act(`restore-${b.name}`, async () => {
      const r = await api<{ restored: number; safety_backup: string }>(`/api/backups/${encodeURIComponent(b.name)}/restore`, {
        method: 'POST', body: JSON.stringify({ parts: chosen }),
      });
      onRestored?.();
      return `Restored ${r.restored} files. To undo, restore ${r.safety_backup}.`;
    });
  };

  const remove = (b: BackupInfo) => {
    if (!window.confirm(`Delete ${b.name}? This cannot be undone.`)) return;
    act(`delete-${b.name}`, async () => {
      await api(`/api/backups/${encodeURIComponent(b.name)}`, { method: 'DELETE' });
      return 'Backup deleted.';
    });
  };

  // Downloads need the admin key header when used from another computer, so fetch → blob.
  const download = (b: BackupInfo) => act(`dl-${b.name}`, async () => {
    const headers: Record<string, string> = {};
    const key = getAdminKey();
    if (key) headers['X-API-Key'] = key;
    const res = await fetch(`${getApiBase()}/api/backups/${encodeURIComponent(b.name)}/download`, { headers });
    if (!res.ok) throw new Error(`Download failed (HTTP ${res.status})`);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = b.name;
    a.click();
    URL.revokeObjectURL(url);
  });

  const importFile = (file: File) => act('import', async () => {
    const headers: Record<string, string> = { 'Content-Type': 'application/gzip' };
    const key = getAdminKey();
    if (key) headers['X-API-Key'] = key;
    const res = await fetch(`${getApiBase()}/api/backups/import`, { method: 'POST', headers, body: file });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `Import failed (HTTP ${res.status})`);
    return `Imported ${body.name}. Use Restore to apply it.`;
  });

  const saveSetting = (path: string, value: unknown) => act('settings', async () => {
    await api('/api/settings', { method: 'PUT', body: JSON.stringify({ changes: { [`backup.${path}`]: value } }) });
    return 'Saved.';
  });

  if (!data) {
    return <div className="settings-view"><div className="settings-body">{notice ? <div className="notice error">{notice.text}</div> : <div className="muted">Loading…</div>}</div></div>;
  }
  const s = data.settings;

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <h3>Back up now</h3>
          <p className="muted">Saved in <code>{data.folder}</code>. API keys stay encrypted with your config key, which is included so the backup can be restored on a new machine — keep backups private.</p>
          <div className="chip-row">
            {data.parts.map(p => (
              <button key={p} className={`chip ${parts.includes(p) ? 'active' : ''}`} disabled={p === 'settings'}
                onClick={() => setParts(cur => (cur.includes(p) ? cur.filter(x => x !== p) : [...cur, p]))}>
                {parts.includes(p) ? '✓ ' : ''}{PART_LABELS[p] || p}
              </button>
            ))}
          </div>
          <div className="field-row">
            <input className="field-input" placeholder="Note (optional), e.g. before trying a new model" value={note} onChange={e => setNote(e.target.value)} />
            <button className="primary-btn" onClick={create} disabled={busy === 'create'}>{busy === 'create' ? 'Backing up…' : 'Create backup'}</button>
          </div>
        </div>

        <div className="settings-card">
          <h3>Automatic backups</h3>
          <div className="field field-inline"><label className="field-label">Back up automatically while the Web UI runs</label>
            <label className="switch"><input type="checkbox" checked={s.auto_enabled} onChange={e => saveSetting('auto_enabled', e.target.checked)} />
              <span className="switch-track"><span className="switch-thumb" /></span></label></div>
          <div className="agent-editor-grid">
            <div className="field"><label className="field-label">Every (hours)</label>
              <input className="field-input narrow" type="number" min={1} defaultValue={s.interval_hours} onBlur={e => Number(e.target.value) !== s.interval_hours && saveSetting('interval_hours', Number(e.target.value))} /></div>
            <div className="field"><label className="field-label">Keep the last</label>
              <input className="field-input narrow" type="number" min={1} defaultValue={s.keep} onBlur={e => Number(e.target.value) !== s.keep && saveSetting('keep', Number(e.target.value))} /></div>
          </div>
          {(['include_knowledge', 'include_memory', 'include_jobs'] as const).map(k => (
            <div className="field field-inline" key={k}><label className="field-label">Include {PART_LABELS[k.replace('include_', '')].toLowerCase()}</label>
              <label className="switch"><input type="checkbox" checked={s[k]} onChange={e => saveSetting(k, e.target.checked)} />
                <span className="switch-track"><span className="switch-thumb" /></span></label></div>
          ))}
          <p className="muted small">Manual backups are never deleted automatically. Updates (<code>zensynora update</code>) always back up your data and the whole program folder first, and restore it if the update fails.</p>
        </div>

        <div className="settings-card">
          <div className="card-head">
            <h3>Backups ({data.backups.length})</h3>
            <label className="ghost-btn file-btn">
              Import…
              <input type="file" accept=".tar.gz,.tgz,application/gzip" onChange={e => { const f = e.target.files?.[0]; if (f) importFile(f); e.target.value = ''; }} />
            </label>
          </div>
          {data.backups.length === 0 && <div className="muted">No backups yet.</div>}
          {data.backups.length > 0 && (
            <table className="data-table">
              <thead><tr><th>Backup</th><th>When</th><th>Contains</th><th>Size</th><th /></tr></thead>
              <tbody>
                {data.backups.map(b => {
                  const chosen = restoreParts[b.name] || b.parts || [];
                  return (
                    <tr key={b.name}>
                      <td>
                        <code className="small">{b.name}</code>
                        {b.label && b.label !== 'manual' && <span className="badge">{b.label}</span>}
                        {b.valid === false && <span className="badge err">unreadable</span>}
                        {b.note && <div className="muted small">{b.note}</div>}
                      </td>
                      <td title={b.created || ''}>{ago(b.mtime)}{b.version && <div className="muted small">v{b.version}</div>}</td>
                      <td>
                        <div className="chip-row tight">
                          {(b.parts || []).map(p => (
                            <button key={p} title="Include in restore" className={`chip small ${chosen.includes(p) ? 'active' : ''}`}
                              onClick={() => setRestoreParts(r => ({ ...r, [b.name]: chosen.includes(p) ? chosen.filter(x => x !== p) : [...chosen, p] }))}>
                              {PART_LABELS[p]?.split(' ')[0] || p}{b.files?.[p] ? ` (${b.files[p]})` : ''}
                            </button>
                          ))}
                        </div>
                      </td>
                      <td>{fmtBytes(b.bytes)}</td>
                      <td className="row-actions">
                        <button className="ghost-btn" disabled={!!busy || b.valid === false || !chosen.length} onClick={() => restore(b)}>Restore</button>
                        <button className="ghost-btn" disabled={!!busy} onClick={() => verify(b)}>Check</button>
                        <button className="ghost-btn" disabled={!!busy} onClick={() => download(b)}>⬇</button>
                        <button className="ghost-btn danger" disabled={!!busy} onClick={() => remove(b)}>×</button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};

export default BackupsView;
