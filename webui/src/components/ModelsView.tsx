/* eslint-disable @typescript-eslint/no-explicit-any */
// Models page (v0.7.1): switch the default LLM in one click, keep a rotation
// list (fallback on errors, or round-robin), and add your own providers —
// local servers or online services, many with a free tier.
import React, { useCallback, useEffect, useState } from 'react';
import { api, type ModelChoice, type ModelsOverview, type ProbeResult } from '../api';

interface Preset { name: string; label: string; kind: 'openai' | 'ollama'; base_url: string; local: boolean; free?: boolean }

interface CustomProvider {
  name: string; label: string; kind: 'openai' | 'ollama'; base_url: string; api_key: string;
  local: boolean; models: string[]; notes: string;
}

interface SettingsSlice {
  config: any;
  secrets_set: string[];
  provider_presets: Preset[];
}

interface Props { onChanged?: () => void }

const BLANK: CustomProvider = { name: '', label: '', kind: 'openai', base_url: '', api_key: '', local: false, models: [], notes: '' };

const ModelsView: React.FC<Props> = ({ onChanged }) => {
  const [ov, setOv] = useState<ModelsOverview | null>(null);
  const [settings, setSettings] = useState<SettingsSlice | null>(null);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [pick, setPick] = useState<ModelChoice>({ provider: '', model: '' });
  const [probe, setProbe] = useState<Record<string, ProbeResult & { loading?: boolean }>>({});
  // v0.7.3: results of a real chat test per "provider/model"
  const [tests, setTests] = useState<Record<string, { ok?: boolean; error?: string; reply?: string; ms?: number; loading?: boolean }>>({});

  const testModel = async (m: ModelChoice) => {
    const key = `${m.provider}/${m.model}`;
    setTests(t => ({ ...t, [key]: { loading: true } }));
    try {
      const r = await api<{ ok: boolean; error?: string; reply?: string; ms: number }>('/api/models/test', { method: 'POST', body: JSON.stringify(m) });
      setTests(t => ({ ...t, [key]: r }));
    } catch (e) {
      setTests(t => ({ ...t, [key]: { ok: false, error: (e as Error).message } }));
    }
  };

  const testResult = (m: ModelChoice) => {
    const r = tests[`${m.provider}/${m.model}`];
    if (!r) return null;
    if (r.loading) return <span className="muted small"> testing…</span>;
    return r.ok
      ? <span className="probe-ok small"> ✔ answers ({r.ms} ms)</span>
      : <span className="probe-err small" title={r.error}> ✘ {r.error}</span>;
  };
  const [edit, setEdit] = useState<{ index: number | null; p: CustomProvider } | null>(null);
  const [rotAdd, setRotAdd] = useState<ModelChoice>({ provider: '', model: '' });

  const load = useCallback(async () => {
    try {
      const [o, s] = await Promise.all([api<ModelsOverview>('/api/models'), api<SettingsSlice>('/api/settings')]);
      setOv(o);
      setSettings(s);
      setPick(p => (p.provider ? p : o.default));
      setRotAdd(r => (r.provider ? r : { provider: o.default.provider, model: '' }));
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    setNotice(null);
    try {
      await fn();
      setNotice({ kind: 'ok', text: ok });
      await load();
      onChanged?.();
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const listModels = async (provider: string, extra?: { base_url?: string; api_key?: string; kind?: string }) => {
    setProbe(p => ({ ...p, [provider]: { ok: false, models: [], loading: true } }));
    try {
      const res = extra
        ? await api<ProbeResult>('/api/settings/probe', { method: 'POST', body: JSON.stringify({ provider, ...extra }) })
        : await api<ProbeResult>(`/api/models/${encodeURIComponent(provider)}`);
      setProbe(p => ({ ...p, [provider]: res }));
    } catch (e) {
      setProbe(p => ({ ...p, [provider]: { ok: false, models: [], error: (e as Error).message } }));
    }
  };

  useEffect(() => { if (pick.provider && !probe[pick.provider]) listModels(pick.provider); }, [pick.provider]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (rotAdd.provider && !probe[rotAdd.provider]) listModels(rotAdd.provider); }, [rotAdd.provider]); // eslint-disable-line react-hooks/exhaustive-deps

  const saveSettings = (changes: Record<string, any>) => api('/api/settings', { method: 'PUT', body: JSON.stringify({ changes }) });

  if (!ov || !settings) {
    return <div className="settings-view"><div className="settings-body">{notice ? <div className="notice error">{notice.text}</div> : <div className="muted">Loading…</div>}</div></div>;
  }

  const label = (name: string) => ov.providers.find(p => p.name === name)?.label || name;
  const rot = ov.rotation;
  const custom: CustomProvider[] = (settings.config?.providers?.custom || []).map((c: any) => ({ ...BLANK, ...c, api_key: '' }));
  const keySet = (name: string) => settings.secrets_set.includes(`providers.custom.${name}.api_key`);

  const setRotation = (entries: ModelChoice[], extra: Record<string, any> = {}) =>
    run(() => saveSettings({ 'agents.rotation.entries': entries, ...extra }), 'Rotation saved.');

  const move = (i: number, d: number) => {
    const e = [...rot.entries];
    const j = i + d;
    if (j < 0 || j >= e.length) return;
    [e[i], e[j]] = [e[j], e[i]];
    setRotation(e);
  };

  const saveCustom = () => edit && run(async () => {
    const list = custom.map(c => ({ ...c }));
    const item = { ...edit.p, name: edit.p.name.trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '-') };
    if (edit.index == null) list.push(item); else list[edit.index] = item;
    await saveSettings({ 'providers.custom': list });
    setEdit(null);
  }, 'Provider saved — it is now available everywhere a provider can be picked.');

  const removeCustom = (i: number) => {
    const name = custom[i].name;
    if (!window.confirm(`Remove provider “${name}”? Agents or rotation entries using it fall back to the default.`)) return;
    run(() => saveSettings({ 'providers.custom': custom.filter((_, j) => j !== i) }), 'Provider removed.');
  };

  // A render helper, not a component: a component defined inside render would
  // be re-created on every keystroke and the text box would lose focus.
  const modelPicker = (id: string, value: ModelChoice, onChange: (v: ModelChoice) => void) => {
    const r = probe[value.provider];
    return (
      <>
        <div className="field-row">
          <select className="field-input" value={value.provider} onChange={e => onChange({ provider: e.target.value, model: '' })}>
            {ov.providers.map(p => <option key={p.name} value={p.name}>{p.label}{p.local ? ' (local)' : ''}{p.custom ? ' ★' : ''}</option>)}
          </select>
          <input className="field-input" list={`${id}-models`} placeholder="model name" value={value.model} onChange={e => onChange({ ...value, model: e.target.value })} />
          <datalist id={`${id}-models`}>{(r?.models || []).map(m => <option key={m} value={m} />)}</datalist>
          <button className="ghost-btn" onClick={() => listModels(value.provider)} disabled={r?.loading}>{r?.loading ? '…' : '↻'}</button>
        </div>
        {r && !r.loading && (r.ok
          ? <div className="chip-row">{r.models.slice(0, 30).map(m => (
            <button key={m} className={`chip ${value.model === m ? 'active' : ''}`} onClick={() => onChange({ ...value, model: m })}>{m}</button>))}</div>
          : <div className="probe-err small">{r.error}</div>)}
      </>
    );
  };

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <h3>Default model</h3>
          <p className="muted">Now: <b>{label(ov.default.provider)}</b> · <code>{ov.default.model}</code>. Used by the default agent and every agent without its own model.</p>
          {modelPicker('pick', pick, setPick)}
          <div className="save-actions">
            <span>{testResult(pick)}</span>
            <button className="ghost-btn" disabled={!pick.model || tests[`${pick.provider}/${pick.model}`]?.loading}
              title="Send a tiny real request — a listed model can still refuse to answer"
              onClick={() => testModel(pick)}>Test</button>
            <button className="primary-btn" disabled={!pick.model || (pick.provider === ov.default.provider && pick.model === ov.default.model)}
              onClick={() => run(() => api('/api/models/default', { method: 'POST', body: JSON.stringify(pick) }), `Default model is now ${pick.provider}/${pick.model}. The previous one was added to the rotation list.`)}>
              Make default
            </button>
          </div>
        </div>

        <div className="settings-card">
          <div className="card-head">
            <h3>Rotation</h3>
            <span className="row-actions">
              <button className="ghost-btn" title="Send a tiny real request to every model in the list"
                onClick={() => [ov.default, ...rot.entries].forEach(testModel)}>Test all</button>
              <button className="ghost-btn" disabled={!rot.entries.length}
                onClick={() => run(() => api('/api/models/rotate', { method: 'POST' }), 'Switched to the next model in the list.')}>⟳ Rotate now</button>
            </span>
          </div>
          <p className="muted">Other models to use with the default one. <b>Fallback</b>: if the default fails (rate limit, server down, missing model) the next one answers. <b>Round-robin</b>: spread requests across all of them.</p>
          <div className="field field-inline">
            <label className="field-label">Use the rotation list automatically</label>
            <label className="switch"><input type="checkbox" checked={rot.enabled} onChange={e => run(() => saveSettings({ 'agents.rotation.enabled': e.target.checked }), e.target.checked ? 'Rotation on.' : 'Rotation off.')} />
              <span className="switch-track"><span className="switch-thumb" /></span></label>
          </div>
          <div className="chip-row">
            {(['fallback', 'round_robin'] as const).map(s => (
              <button key={s} className={`chip ${rot.strategy === s ? 'active' : ''}`} onClick={() => run(() => saveSettings({ 'agents.rotation.strategy': s }), 'Strategy saved.')}>
                {s === 'fallback' ? 'Fallback on errors' : 'Round-robin'}
              </button>
            ))}
          </div>
          <ol className="rotation-list">
            <li><b>{ov.default.provider}/{ov.default.model}</b> <span className="badge default-badge">default</span>
              <span className="row-actions"><button className="icon-btn" title="Test" onClick={() => testModel(ov.default)}>✓?</button></span>
              {testResult(ov.default)}</li>
            {rot.entries.map((e, i) => (
              <li key={`${e.provider}/${e.model}`}>
                {e.provider}/{e.model}
                <span className="row-actions">
                  <button className="icon-btn" onClick={() => move(i, -1)} title="Up">↑</button>
                  <button className="icon-btn" onClick={() => move(i, 1)} title="Down">↓</button>
                  <button className="icon-btn" title="Make default" onClick={() => run(() => api('/api/models/default', { method: 'POST', body: JSON.stringify(e) }), `Default is now ${e.provider}/${e.model}.`)}>★</button>
                  <button className="icon-btn" onClick={() => setRotation(rot.entries.filter((_, j) => j !== i))} title="Remove">×</button>
                  <button className="icon-btn" title="Test" onClick={() => testModel(e)}>✓?</button>
                </span>
                {testResult(e)}
              </li>
            ))}
          </ol>
          {modelPicker('rot', rotAdd, setRotAdd)}
          <div className="save-actions">
            <button className="ghost-btn" disabled={!rotAdd.model || rot.entries.some(e => e.provider === rotAdd.provider && e.model === rotAdd.model)}
              onClick={() => setRotation([...rot.entries, rotAdd])}>+ Add to rotation</button>
          </div>
        </div>

        <div className="settings-card">
          <div className="card-head">
            <h3>Your providers</h3>
            <button className="primary-btn" onClick={() => setEdit({ index: null, p: { ...BLANK } })}>+ Add provider</button>
          </div>
          <p className="muted">Any OpenAI-compatible server — on this computer, your network or online — or another Ollama server. Built-in providers are set up in Settings → Providers.</p>
          {custom.length === 0 && !edit && <div className="muted">None yet.</div>}
          {custom.map((c, i) => (
            <div className="provider-row" key={c.name}>
              <div>
                <b>{c.label || c.name}</b> <code>{c.name}</code> {c.local ? <span className="badge">local</span> : <span className="badge">online</span>}
                <div className="muted small">{c.base_url}{keySet(c.name) ? ' · key saved' : ''}</div>
              </div>
              <div className="row-actions">
                <button className="ghost-btn" onClick={() => listModels(c.name)}>Test</button>
                <button className="ghost-btn" onClick={() => setEdit({ index: i, p: { ...c } })}>Edit</button>
                <button className="ghost-btn danger" onClick={() => removeCustom(i)}>Remove</button>
              </div>
              {probe[c.name] && !probe[c.name].loading && (
                <div className={probe[c.name].ok ? 'probe-ok small' : 'probe-err small'}>
                  {probe[c.name].ok ? `Connected — ${probe[c.name].models.length} models` : probe[c.name].error}
                </div>
              )}
            </div>
          ))}

          {edit && (
            <div className="agent-editor">
              {edit.index == null && (
                <div className="field">
                  <span className="field-label">Start from a preset</span>
                  <div className="chip-row">
                    {settings.provider_presets.map(p => (
                      <button key={p.name} className="chip" title={p.base_url}
                        onClick={() => setEdit({ index: null, p: { ...BLANK, name: p.name, label: p.label, kind: p.kind, base_url: p.base_url, local: p.local } })}>
                        {p.label}{p.local ? ' · local' : ''}{p.free ? ' · free tier' : ''}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="agent-editor-grid">
                <div className="field"><label className="field-label">Id (used in settings)</label>
                  <input className="field-input" value={edit.p.name} disabled={edit.index != null} placeholder="e.g. deepseek" onChange={e => setEdit({ ...edit, p: { ...edit.p, name: e.target.value } })} /></div>
                <div className="field"><label className="field-label">Display name</label>
                  <input className="field-input" value={edit.p.label} onChange={e => setEdit({ ...edit, p: { ...edit.p, label: e.target.value } })} /></div>
                <div className="field"><label className="field-label">Type</label>
                  <select className="field-input" value={edit.p.kind} onChange={e => setEdit({ ...edit, p: { ...edit.p, kind: e.target.value as 'openai' | 'ollama' } })}>
                    <option value="openai">OpenAI-compatible</option><option value="ollama">Ollama</option>
                  </select></div>
              </div>
              <div className="field"><label className="field-label">Server URL</label>
                <input className="field-input" value={edit.p.base_url} placeholder="https://api.example.com/v1" onChange={e => setEdit({ ...edit, p: { ...edit.p, base_url: e.target.value } })} /></div>
              <div className="field"><label className="field-label">API key {edit.index != null && keySet(edit.p.name) && <span className="muted">(saved — leave empty to keep)</span>}</label>
                <input className="field-input" type="password" autoComplete="off" value={edit.p.api_key} placeholder={edit.p.local ? 'usually not needed' : 'paste the key'} onChange={e => setEdit({ ...edit, p: { ...edit.p, api_key: e.target.value } })} /></div>
              <div className="field"><label className="field-label">Model names (optional, one per line — for servers that cannot list them)</label>
                <textarea className="field-input" rows={2} value={edit.p.models.join('\n')} onChange={e => setEdit({ ...edit, p: { ...edit.p, models: e.target.value.split('\n').map(s => s.trim()).filter(Boolean) } })} /></div>
              <div className="field field-inline"><label className="field-label">Runs on my computer / network</label>
                <label className="switch"><input type="checkbox" checked={edit.p.local} onChange={e => setEdit({ ...edit, p: { ...edit.p, local: e.target.checked } })} />
                  <span className="switch-track"><span className="switch-thumb" /></span></label></div>
              <div className="probe-row">
                <button className="ghost-btn" disabled={!edit.p.base_url || !edit.p.name}
                  onClick={() => listModels(edit.p.name || '_new', { base_url: edit.p.base_url, api_key: edit.p.api_key || undefined, kind: edit.p.kind })}>Test connection</button>
                {probe[edit.p.name] && !probe[edit.p.name].loading && (
                  <span className={probe[edit.p.name].ok ? 'probe-ok' : 'probe-err'}>
                    {probe[edit.p.name].ok ? `Connected — ${probe[edit.p.name].models.length} models` : probe[edit.p.name].error}
                  </span>
                )}
              </div>
              <div className="save-actions">
                <button className="ghost-btn" onClick={() => setEdit(null)}>Cancel</button>
                <button className="primary-btn" disabled={!edit.p.name.trim() || !edit.p.base_url.trim()} onClick={saveCustom}>Save provider</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ModelsView;
