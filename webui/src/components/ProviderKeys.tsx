// Several API keys per provider (v0.7.5).
// ProviderKeysPanel: the "Keys" list of one provider — add, rename, replace,
// delete, test (and list models) with one key, and "try other keys on rate limits".
// KeySelect: pick which key an agent / the default model / a rotation entry uses.
// Secrets are only ever sent to the server, never shown or read back.
import React, { useState } from 'react';
import { api, type ProbeResult, type ProviderKeysMap } from '../api';

interface PanelProps {
  provider: string;
  keysMap: ProviderKeysMap | null;
  onChanged: (next: ProviderKeysMap) => void;
}

type Busy = ProbeResult & { loading?: boolean };

export const ProviderKeysPanel: React.FC<PanelProps> = ({ provider, keysMap, onChanged }) => {
  const info = keysMap?.[provider];
  const [label, setLabel] = useState('');
  const [secret, setSecret] = useState('');
  const [editing, setEditing] = useState<{ id: string; label: string; secret: string } | null>(null);
  const [tests, setTests] = useState<Record<string, Busy>>({});
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  if (!info) return null;
  const base = `/api/provider-keys/${encodeURIComponent(provider)}`;

  const call = async (fn: () => Promise<{ providers: ProviderKeysMap }>) => {
    setError(null);
    try {
      const res = await fn();
      onChanged(res.providers);
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    }
  };

  const add = async () => {
    const ok = await call(() => api(base, { method: 'POST', body: JSON.stringify({ label: label.trim(), api_key: secret }) }));
    if (ok) { setLabel(''); setSecret(''); setAdding(false); }
  };

  const saveEdit = async () => {
    if (!editing) return;
    const body: Record<string, string> = {};
    if (editing.id !== 'default') body.label = editing.label;
    if (editing.secret) body.api_key = editing.secret;
    const ok = await call(() => api(`${base}/${encodeURIComponent(editing.id)}`, { method: 'PATCH', body: JSON.stringify(body) }));
    if (ok) setEditing(null);
  };

  const remove = (id: string, name: string) => {
    if (!window.confirm(`Delete the key “${name}”?`)) return;
    call(() => api(`${base}/${encodeURIComponent(id)}`, { method: 'DELETE' }));
  };

  const test = async (id: string) => {
    setTests(t => ({ ...t, [id]: { ok: false, models: [], loading: true } }));
    try {
      const r = await api<ProbeResult>(`${base}/${encodeURIComponent(id)}/test`, { method: 'POST' });
      setTests(t => ({ ...t, [id]: r }));
    } catch (e) {
      setTests(t => ({ ...t, [id]: { ok: false, models: [], error: (e as Error).message } }));
    }
  };

  return (
    <div className="keys-panel">
      <div className="field-label-row">
        <span className="field-label">Keys</span>
        <button type="button" className="ghost-btn" onClick={() => setAdding(a => !a)}>{adding ? 'Cancel' : '+ Add key'}</button>
      </div>
      <ul className="key-list">
        {info.keys.map(k => {
          const r = tests[k.id];
          const isEditing = editing?.id === k.id;
          return (
            <li key={k.id} className="key-row">
              <div className="key-main">
                <b>{k.label}</b> {k.id !== 'default' && <code>{k.id}</code>}
                {k.set ? <span className="badge">saved</span> : <span className="badge err">no key</span>}
                <div className="muted small">{k.used_by.length ? `Used by: ${k.used_by.join(', ')}` : 'Not used by any agent'}</div>
              </div>
              <span className="row-actions">
                <button type="button" className="ghost-btn" disabled={!k.set || r?.loading} onClick={() => test(k.id)}
                  title="Test the connection with this key and list the models it can use">{r?.loading ? 'Testing…' : 'Test'}</button>
                <button type="button" className="ghost-btn" onClick={() => setEditing(isEditing ? null : { id: k.id, label: k.label, secret: '' })}>
                  {k.id === 'default' ? 'Replace' : 'Edit'}</button>
                {k.id !== 'default' && (
                  <button type="button" className="ghost-btn danger" disabled={k.used_by.length > 0}
                    title={k.used_by.length ? 'Pick another key for the agents that use it first' : 'Delete this key'}
                    onClick={() => remove(k.id, k.label)}>Delete</button>
                )}
              </span>
              {r && !r.loading && (
                <div className={r.ok ? 'probe-ok small key-test' : 'probe-err small key-test'}>
                  {r.ok ? `Works — ${r.models.length} model${r.models.length === 1 ? '' : 's'}${r.latency_ms != null ? ` (${r.latency_ms} ms)` : ''}` : r.error}
                </div>
              )}
              {isEditing && editing && (
                <div className="field-row key-edit">
                  {k.id !== 'default' && (
                    <input className="field-input" value={editing.label} placeholder="Name" aria-label="Key name"
                      onChange={e => setEditing({ ...editing, label: e.target.value })} />
                  )}
                  <input className="field-input" type="password" autoComplete="off" value={editing.secret}
                    placeholder={k.set ? 'New key (leave empty to keep)' : 'Paste the key'} aria-label="API key"
                    onChange={e => setEditing({ ...editing, secret: e.target.value })} />
                  <button type="button" className="primary-btn" onClick={saveEdit}
                    disabled={k.id === 'default' ? !editing.secret : !(editing.secret || editing.label !== k.label)}>Save</button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
      {adding && (
        <div className="field-row key-edit">
          <input className="field-input" value={label} placeholder="Name, e.g. Work account" aria-label="Key name" onChange={e => setLabel(e.target.value)} />
          <input className="field-input" type="password" autoComplete="off" value={secret} placeholder="Paste the key" aria-label="API key"
            onChange={e => setSecret(e.target.value)} />
          <button type="button" className="primary-btn" disabled={!label.trim() || !secret.trim()} onClick={add}>Add key</button>
        </div>
      )}
      {info.keys.length > 1 && (
        <label className="field field-inline key-rotate">
          <span className="field-label">When a key hits a rate limit or runs out of quota, try the other keys</span>
          <span className="switch">
            <input type="checkbox" checked={info.rotate_keys}
              onChange={e => call(() => api(`${base}/rotate`, { method: 'PUT', body: JSON.stringify({ enabled: e.target.checked }) }))} />
            <span className="switch-track"><span className="switch-thumb" /></span>
          </span>
        </label>
      )}
      {error && <div className="probe-err small">{error}</div>}
    </div>
  );
};

interface SelectProps {
  provider: string;
  keysMap: ProviderKeysMap | null;
  value: string | undefined;
  onChange: (key: string) => void;
  className?: string;
}

/** Key picker; hidden while the provider has only its default key. */
export const KeySelect: React.FC<SelectProps> = ({ provider, keysMap, value, onChange, className }) => {
  const keys = keysMap?.[provider]?.keys || [];
  const current = value || '';
  if (keys.length <= 1 && !current) return null;
  const known = current === '' || keys.some(k => k.id === current);
  return (
    <select className={className || 'field-input'} value={current} aria-label="API key" title="Which API key of the provider to use"
      onChange={e => onChange(e.target.value)}>
      <option value="">Default key</option>
      {keys.filter(k => k.id !== 'default').map(k => <option key={k.id} value={k.id}>{k.label}</option>)}
      {!known && <option value={current}>{current} (missing — default key is used)</option>}
    </select>
  );
};
