/* eslint-disable @typescript-eslint/no-explicit-any */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, ApiError, getAdminKey, setAdminKey, type ProbeResult } from '../api';

type Json = any;

interface SettingsResponse {
  config: Json;
  secrets_set: string[];
  secret_paths: string[];
  providers: string[];
  config_file: string;
  config_file_exists: boolean;
}

type Kind = 'text' | 'password' | 'number' | 'bool' | 'select' | 'list';

interface FieldDef {
  path: string;
  label: string;
  kind: Kind;
  help?: string;
  options?: string[];
  placeholder?: string;
}

const PROVIDER_LABELS: Record<string, string> = {
  ollama: 'Ollama (local)',
  lmstudio: 'LM Studio (local)',
  llamacpp: 'llama.cpp server (local)',
  openai: 'OpenAI',
  anthropic: 'Anthropic',
  gemini: 'Google Gemini',
  groq: 'Groq',
  openrouter: 'OpenRouter',
};

const TABS = ['Model', 'Providers', 'Agents', 'Channels', 'Features', 'Security'] as const;
type Tab = typeof TABS[number];

const CHANNEL_FIELDS: FieldDef[] = [
  { path: 'channels.telegram.enabled', label: 'Telegram bot enabled', kind: 'bool' },
  { path: 'channels.telegram.token', label: 'Telegram bot token', kind: 'password', help: 'From @BotFather.' },
  { path: 'channels.telegram.max_workers', label: 'Telegram worker threads', kind: 'number' },
  { path: 'channels.whatsapp.enabled', label: 'WhatsApp enabled', kind: 'bool' },
  { path: 'channels.whatsapp.phone_number_id', label: 'WhatsApp phone number ID', kind: 'text' },
  { path: 'channels.whatsapp.business_account_id', label: 'WhatsApp business account ID', kind: 'text' },
  { path: 'channels.whatsapp.access_token', label: 'WhatsApp access token', kind: 'password' },
  { path: 'channels.whatsapp.verify_token', label: 'WhatsApp verify token', kind: 'password' },
];

const FEATURE_FIELDS: { title: string; fields: FieldDef[] }[] = [
  {
    title: 'Knowledge & research',
    fields: [
      { path: 'knowledge.enabled', label: 'Knowledge base', kind: 'bool' },
      { path: 'knowledge.auto_extract', label: 'Auto-index notes written to disk', kind: 'bool' },
      { path: 'intelligence.research_enabled', label: 'Background researcher', kind: 'bool' },
      { path: 'intelligence.research_interval_hours', label: 'Research interval (hours)', kind: 'number' },
      { path: 'intelligence.research_idle_minutes', label: 'Research only after idle (minutes)', kind: 'number' },
      { path: 'intelligence.offline_mode', label: 'Fall back to a local model when offline', kind: 'bool' },
    ],
  },
  {
    title: 'Agents & tools',
    fields: [
      { path: 'swarm.enabled', label: 'Agent swarms', kind: 'bool' },
      { path: 'swarm.max_concurrent_swarms', label: 'Max concurrent swarms', kind: 'number' },
      { path: 'mcp.enabled', label: 'MCP client', kind: 'bool' },
      { path: 'semantic_cache.enabled', label: 'Semantic response cache', kind: 'bool' },
      { path: 'sandbox.enabled', label: 'Sandbox for generated tools', kind: 'bool' },
      { path: 'sandbox.allow_network', label: 'Sandbox may use the network', kind: 'bool' },
    ],
  },
  {
    title: 'Timeouts & memory',
    fields: [
      { path: 'timeouts.llm_seconds', label: 'LLM request timeout (s)', kind: 'number' },
      { path: 'timeouts.shell_seconds', label: 'Shell command timeout (s)', kind: 'number' },
      { path: 'timeouts.http_seconds', label: 'HTTP request timeout (s)', kind: 'number' },
      { path: 'memory.auto_cleanup_enabled', label: 'Delete old conversation history', kind: 'bool' },
      { path: 'memory.auto_cleanup_days', label: 'Keep history for (days)', kind: 'number' },
      { path: 'agents.summarization_threshold', label: 'Summarize after N messages', kind: 'number' },
    ],
  },
];

const SECURITY_FIELDS: FieldDef[] = [
  {
    path: 'security.admin_api_key', label: 'Admin API key', kind: 'password',
    help: 'Lets other computers open these settings (sent as X-API-Key). Leave empty to allow settings only from this computer.',
  },
  { path: 'security.cors_origins', label: 'Allowed browser origins (CORS)', kind: 'list', help: 'One per line. Needs a Web UI restart.' },
  { path: 'security.allowed_commands', label: 'Shell commands the agent may run', kind: 'list', help: 'One per line.' },
  { path: 'security.blocked_commands', label: 'Always-blocked shell commands', kind: 'list', help: 'One per line.' },
  { path: 'security.gdpr_enabled', label: 'GDPR helpers', kind: 'bool' },
];

function getPath(obj: Json, path: string): Json {
  return path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
}

function setPathImmutable(obj: Json, path: string, value: Json): Json {
  const [head, ...rest] = path.split('.');
  const clone = Array.isArray(obj) ? [...obj] : { ...(obj || {}) };
  clone[head] = rest.length ? setPathImmutable(clone[head], rest.join('.'), value) : value;
  return clone;
}

interface Props {
  onSaved?: () => void;
}

const SettingsView: React.FC<Props> = ({ onSaved }) => {
  const [data, setData] = useState<SettingsResponse | null>(null);
  const [draft, setDraft] = useState<Json>(null);
  const [changes, setChanges] = useState<Record<string, Json>>({});
  const [clearSecrets, setClearSecrets] = useState<string[]>([]);
  const [tab, setTab] = useState<Tab>('Model');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [needsKey, setNeedsKey] = useState(false);
  const [keyInput, setKeyInput] = useState(getAdminKey());
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [probe, setProbe] = useState<Record<string, ProbeResult & { loading?: boolean }>>({});

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      const res = await api<SettingsResponse>('/api/settings');
      setData(res);
      setDraft(res.config);
      setChanges({});
      setClearSecrets([]);
      setNeedsKey(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 403) setNeedsKey(true);
      setLoadError((e as Error).message);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const secretSet = useMemo(() => new Set(data?.secrets_set || []), [data]);
  const secretPaths = useMemo(() => new Set(data?.secret_paths || []), [data]);

  const update = (path: string, value: Json) => {
    setDraft((d: Json) => setPathImmutable(d, path, value));
    setChanges(prev => {
      const next = { ...prev };
      const original = getPath(data?.config, path);
      const isSecret = secretPaths.has(path);
      if ((isSecret && value === '') || (!isSecret && JSON.stringify(original) === JSON.stringify(value))) {
        delete next[path];
      } else {
        next[path] = value;
      }
      return next;
    });
    if (secretPaths.has(path) && value) setClearSecrets(prev => prev.filter(p => p !== path));
    setNotice(null);
  };

  const toggleClearSecret = (path: string) => {
    setClearSecrets(prev => (prev.includes(path) ? prev.filter(p => p !== path) : [...prev, path]));
    setNotice(null);
  };

  const dirtyCount = Object.keys(changes).length + clearSecrets.length;

  const save = async () => {
    setSaving(true);
    setNotice(null);
    try {
      const res = await api<{ restart_needed_for: string[] }>('/api/settings', {
        method: 'PUT',
        body: JSON.stringify({ changes, clear_secrets: clearSecrets }),
      });
      const restart = res.restart_needed_for.length
        ? ' Channel/MCP/CORS changes apply after restarting the gateway or Web UI.'
        : ' New chats use the new settings right away.';
      setNotice({ kind: 'ok', text: `Saved.${restart}` });
      await load();
      onSaved?.();
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    } finally {
      setSaving(false);
    }
  };

  const runProbe = async (provider: string) => {
    setProbe(p => ({ ...p, [provider]: { ok: false, models: [], loading: true } }));
    const base = getPath(draft, `providers.${provider}.base_url`);
    const key = changes[`providers.${provider}.api_key`];
    try {
      const res = await api<ProbeResult>('/api/settings/probe', {
        method: 'POST',
        body: JSON.stringify({ provider, base_url: base || null, api_key: key || null }),
      });
      setProbe(p => ({ ...p, [provider]: res }));
    } catch (e) {
      setProbe(p => ({ ...p, [provider]: { ok: false, models: [], error: (e as Error).message } }));
    }
  };

  // ── Field renderer ────────────────────────────────────────────────────────
  const renderField = (f: FieldDef) => {
    const value = getPath(draft, f.path);
    if (value === undefined && f.kind !== 'password') return null;
    const id = `f-${f.path}`;
    const changed = f.path in changes || clearSecrets.includes(f.path);

    let control: React.ReactNode;
    if (f.kind === 'bool') {
      control = (
        <label className="switch">
          <input id={id} type="checkbox" checked={!!value} onChange={e => update(f.path, e.target.checked)} />
          <span className="switch-track"><span className="switch-thumb" /></span>
        </label>
      );
    } else if (f.kind === 'select') {
      control = (
        <select id={id} className="field-input" value={value ?? ''} onChange={e => update(f.path, e.target.value)}>
          {(f.options || []).map(o => <option key={o} value={o}>{PROVIDER_LABELS[o] || o}</option>)}
        </select>
      );
    } else if (f.kind === 'number') {
      control = (
        <input id={id} className="field-input narrow" type="number" value={value ?? ''}
          onChange={e => update(f.path, e.target.value === '' ? 0 : Number(e.target.value))} />
      );
    } else if (f.kind === 'list') {
      control = (
        <textarea id={id} className="field-input" rows={Math.min(8, Math.max(3, (value || []).length))}
          value={(value || []).join('\n')}
          onChange={e => update(f.path, e.target.value.split('\n').map(s => s.trim()).filter(Boolean))} />
      );
    } else if (f.kind === 'password') {
      const isSet = secretSet.has(f.path);
      const clearing = clearSecrets.includes(f.path);
      control = (
        <div className="field-row">
          <input id={id} className="field-input" type="password" autoComplete="off"
            placeholder={clearing ? 'Will be removed on save' : isSet ? '•••••••• saved — type to replace' : 'Not set'}
            value={changes[f.path] ?? ''} onChange={e => update(f.path, e.target.value)} />
          {isSet && (
            <button type="button" className={`ghost-btn ${clearing ? 'active' : ''}`} onClick={() => toggleClearSecret(f.path)}>
              {clearing ? 'Undo' : 'Remove'}
            </button>
          )}
        </div>
      );
    } else {
      control = (
        <input id={id} className="field-input" type="text" value={value ?? ''} placeholder={f.placeholder}
          onChange={e => update(f.path, e.target.value)} />
      );
    }

    return (
      <div className={`field ${f.kind === 'bool' ? 'field-inline' : ''}`} key={f.path}>
        <label htmlFor={id} className="field-label">
          {f.label}{changed && <span className="changed-dot" title="Unsaved change" />}
        </label>
        {control}
        {f.help && <div className="field-help">{f.help}</div>}
      </div>
    );
  };

  const providerFields = (p: string): FieldDef[] => {
    const cfg = getPath(draft, `providers.${p}`) || {};
    const out: FieldDef[] = [];
    if ('base_url' in cfg) out.push({ path: `providers.${p}.base_url`, label: 'Server URL', kind: 'text' });
    if ('api_key' in cfg) out.push({ path: `providers.${p}.api_key`, label: 'API key', kind: 'password' });
    if ('site_url' in cfg) out.push({ path: `providers.${p}.site_url`, label: 'Site URL (optional header)', kind: 'text' });
    if ('site_name' in cfg) out.push({ path: `providers.${p}.site_name`, label: 'Site name (optional header)', kind: 'text' });
    return out;
  };

  const ProbeLine: React.FC<{ provider: string }> = ({ provider }) => {
    const r = probe[provider];
    return (
      <div className="probe-row">
        <button type="button" className="ghost-btn" onClick={() => runProbe(provider)} disabled={r?.loading}>
          {r?.loading ? 'Testing…' : 'Test connection & list models'}
        </button>
        {r && !r.loading && (
          <span className={r.ok ? 'probe-ok' : 'probe-err'}>
            {r.ok ? `Connected (${r.latency_ms} ms) — ${r.models.length} model${r.models.length === 1 ? '' : 's'}` : r.error}
          </span>
        )}
      </div>
    );
  };

  // ── Access / loading states ───────────────────────────────────────────────
  if (needsKey) {
    return (
      <div className="settings-view">
        <div className="settings-card narrow-card">
          <h3>Admin key required</h3>
          <p className="muted">Settings can be changed from this computer without a key. From another device, enter the admin API key set in Settings → Security.</p>
          <form className="field-row" onSubmit={e => { e.preventDefault(); setAdminKey(keyInput.trim()); load(); }}>
            <input className="field-input" type="password" value={keyInput} onChange={e => setKeyInput(e.target.value)} placeholder="Admin API key" />
            <button className="primary-btn" type="submit">Unlock</button>
          </form>
          {loadError && <div className="notice error">{loadError}</div>}
        </div>
      </div>
    );
  }
  if (loadError) {
    return <div className="settings-view"><div className="notice error">Could not load settings: {loadError} <button className="ghost-btn" onClick={load}>Retry</button></div></div>;
  }
  if (!data || !draft) return <div className="settings-view muted">Loading settings…</div>;

  const defaultProvider: string = getPath(draft, 'agents.defaults.provider');
  const models = probe[defaultProvider]?.models || [];
  const named: Json[] = getPath(draft, 'agents.named') || [];

  return (
    <div className="settings-view">
      <div className="settings-tabs" role="tablist">
        {TABS.map(t => (
          <button key={t} role="tab" aria-selected={tab === t} className={`settings-tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>

      <div className="settings-body">
        {!data.config_file_exists && (
          <div className="notice">No config file yet — saving here creates <code>{data.config_file}</code> (same as running <code>zensynora onboard</code>).</div>
        )}

        {tab === 'Model' && (
          <div className="settings-card">
            <h3>Default model</h3>
            <p className="muted">Used by the <b>default</b> agent and by named agents that don't pick their own.</p>
            {renderField({ path: 'agents.defaults.provider', label: 'Provider', kind: 'select', options: data.providers })}
            <div className="field">
              <label htmlFor="f-model" className="field-label">
                Model{'agents.defaults.model' in changes && <span className="changed-dot" />}
              </label>
              <input id="f-model" className="field-input" list="model-options" value={getPath(draft, 'agents.defaults.model') ?? ''}
                onChange={e => update('agents.defaults.model', e.target.value)} />
              <datalist id="model-options">{models.map(m => <option key={m} value={m} />)}</datalist>
              {models.length > 0 && (
                <div className="chip-row">
                  {models.slice(0, 24).map(m => (
                    <button type="button" key={m} className={`chip ${getPath(draft, 'agents.defaults.model') === m ? 'active' : ''}`}
                      onClick={() => update('agents.defaults.model', m)}>{m}</button>
                  ))}
                </div>
              )}
            </div>
            <h4>{PROVIDER_LABELS[defaultProvider] || defaultProvider} connection</h4>
            {providerFields(defaultProvider).map(renderField)}
            <ProbeLine provider={defaultProvider} />
          </div>
        )}

        {tab === 'Providers' && data.providers.map(p => (
          <div className="settings-card" key={p}>
            <h3>{PROVIDER_LABELS[p] || p}{p === defaultProvider && <span className="badge default-badge">default</span>}</h3>
            {providerFields(p).map(renderField)}
            <ProbeLine provider={p} />
          </div>
        ))}

        {tab === 'Agents' && (
          <div className="settings-card">
            <h3>Named agents</h3>
            <p className="muted">Extra agents appear in the sidebar and can be addressed in chat with <code>@name</code>.</p>
            {named.length === 0 && <div className="muted">No named agents yet.</div>}
            {named.map((a: Json, i: number) => {
              const setAgent = (key: string, val: string) => {
                const next = named.map((x: Json, j: number) => (j === i ? { ...x, [key]: val } : x));
                update('agents.named', next);
              };
              return (
                <div className="agent-editor" key={i}>
                  <div className="agent-editor-grid">
                    <div className="field">
                      <label className="field-label">Name</label>
                      <input className="field-input" value={a.name} onChange={e => setAgent('name', e.target.value.replace(/\s+/g, '_'))} />
                    </div>
                    <div className="field">
                      <label className="field-label">Provider</label>
                      <select className="field-input" value={a.provider || ''} onChange={e => setAgent('provider', e.target.value)}>
                        <option value="">Same as default ({defaultProvider})</option>
                        {data.providers.map(p => <option key={p} value={p}>{PROVIDER_LABELS[p] || p}</option>)}
                      </select>
                    </div>
                    <div className="field">
                      <label className="field-label">Model</label>
                      <input className="field-input" value={a.model} onChange={e => setAgent('model', e.target.value)} />
                    </div>
                  </div>
                  <div className="field">
                    <label className="field-label">System prompt</label>
                    <textarea className="field-input" rows={3} value={a.system_prompt || ''} onChange={e => setAgent('system_prompt', e.target.value)} />
                  </div>
                  <button type="button" className="ghost-btn danger" onClick={() => update('agents.named', named.filter((_: Json, j: number) => j !== i))}>
                    Remove agent
                  </button>
                </div>
              );
            })}
            <button type="button" className="ghost-btn" onClick={() => update('agents.named', [...named, {
              name: `agent${named.length + 1}`, provider: '', model: getPath(draft, 'agents.defaults.model') || '', system_prompt: '',
            }])}>+ Add agent</button>
          </div>
        )}

        {tab === 'Channels' && (
          <div className="settings-card">
            <h3>Messaging channels</h3>
            <p className="muted">Channels run in the gateway (<code>zensynora gateway</code>). Restart it after changing these.</p>
            {CHANNEL_FIELDS.map(renderField)}
          </div>
        )}

        {tab === 'Features' && FEATURE_FIELDS.map(g => (
          <div className="settings-card" key={g.title}>
            <h3>{g.title}</h3>
            {g.fields.map(renderField)}
          </div>
        ))}

        {tab === 'Security' && (
          <div className="settings-card">
            <h3>Security</h3>
            {SECURITY_FIELDS.map(renderField)}
          </div>
        )}
      </div>

      <div className={`save-bar ${dirtyCount || notice ? 'visible' : ''}`}>
        {notice ? (
          <span className={notice.kind === 'ok' ? 'probe-ok' : 'probe-err'}>{notice.text}</span>
        ) : (
          <span className="muted">{dirtyCount} unsaved change{dirtyCount === 1 ? '' : 's'}</span>
        )}
        <div className="save-actions">
          <button className="ghost-btn" onClick={load} disabled={saving || !dirtyCount}>Discard</button>
          <button className="primary-btn" onClick={save} disabled={saving || !dirtyCount}>{saving ? 'Saving…' : 'Save settings'}</button>
        </div>
      </div>
    </div>
  );
};

export default SettingsView;
