/* eslint-disable @typescript-eslint/no-explicit-any */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { api, ApiError, getAdminKey, setAdminKey, type ProbeResult, type ProviderKeysMap } from '../api';
import { KeySelect, ProviderKeysPanel } from './ProviderKeys';
import { LatencyTest, ModelPicker } from './ModelPicker';
import { ContextLength } from './ContextLength';
import { LevelBadge } from './LevelBadge';
import { AgentFusions } from './AgentFusions';

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
  {
    path: 'channels.telegram.allowFrom', label: 'Allowed Telegram users', kind: 'list',
    help: 'Your numeric user id — send /whoami to the bot to see it — or @username. Nobody else can use the bot.',
  },
  { path: 'channels.telegram.run_in_webui', label: 'Run the bot inside the Web UI', kind: 'bool', help: 'Turn off if you run the bot with zensynora gateway instead.' },
  { path: 'channels.telegram.shared_knowledge', label: 'Telegram uses the same knowledge base as the Web UI', kind: 'bool' },
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
      { path: 'knowledge.detect_conflicts', label: 'Flag facts that contradict each other', kind: 'bool', help: 'E.g. "32 GB RAM" and "64 GB RAM" on the same note. Only flags them (⚠ on the Knowledge page); nothing is changed until you choose.' },
      { path: 'knowledge.hub_penalty', label: 'Rank very linked notes lower in the agent’s context', kind: 'bool', help: 'Notes with many links (hubs) match many questions but say little about each; they are kept, just ranked lower, and not walked through.' },
      { path: 'knowledge.hub_degree', label: 'A note is a hub above this many links', kind: 'number', help: 'Default 25.' },
      { path: 'knowledge.semantic_search', label: 'Semantic search (find notes by meaning)', kind: 'bool', help: 'Agents, search_knowledge and the Knowledge page also match notes by meaning, not only by their words. Runs on this computer; needs the optional package fastembed (Install / Backfill on the Knowledge page → Tags & types).' },
      { path: 'knowledge.distill', label: 'Distil facts, scenarios and a persona', kind: 'bool', help: 'In the background, turns the notes into deduplicated facts with a confidence, groups of related facts and a short summary of you; the agent sees the matching ones first (Knowledge page → Distilled). Derived data only — notes are never changed; secrets are never included.' },
      { path: 'knowledge.distill_every', label: 'Distil after this many changed notes', kind: 'number', help: 'Default 20 (or after the idle time below).' },
      { path: 'knowledge.distill_idle_minutes', label: 'Distil after this many minutes without changes', kind: 'number', help: 'Default 10; 0 = only after the number of changed notes above.' },
      { path: 'knowledge.watch_files', label: 'Watch note files (live re-index)', kind: 'bool', help: 'The Web UI re-indexes a note within about a second when you create, edit, move or delete its file by hand (editor, Obsidian, git pull). Ignores its own writes, .trash, dot-folders and temporary files. Uses the optional package watchfiles (pip install "zensynora[watch]"); without it the folder is checked every 5 seconds.' },
      { path: 'knowledge.embedding_model', label: 'Embedding model', kind: 'select', options: ['BAAI/bge-small-en-v1.5', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'], help: 'bge-small-en-v1.5: English, 67 MB (default). paraphrase-multilingual-MiniLM: ~50 languages incl. Romanian, 220 MB, shorter chunks. Changing it needs a Backfill.' },
      { path: 'intelligence.research_enabled', label: 'Background researcher', kind: 'bool' },
      { path: 'intelligence.research_interval_hours', label: 'Research interval (hours)', kind: 'number' },
      { path: 'intelligence.research_idle_minutes', label: 'Research only after idle (minutes)', kind: 'number' },
      { path: 'intelligence.offline_mode', label: 'Fall back to a local model when offline', kind: 'bool' },
    ],
  },
  {
    title: 'Agents & tools',
    fields: [
      { path: 'agents.allow_agent_creation', label: 'Agents may create new agents', kind: 'bool' },
      { path: 'agents.allow_job_creation', label: 'Agents may create and change jobs', kind: 'bool', help: 'Tools job_create, job_run, job_status, job_update. Jobs made by agents show "by agent:<name>" on the Jobs page.' },
      { path: 'swarm.enabled', label: 'Agent swarms', kind: 'bool' },
      { path: 'swarm.max_concurrent_swarms', label: 'Max concurrent swarms', kind: 'number' },
      { path: 'mcp.enabled', label: 'MCP client', kind: 'bool' },
      { path: 'semantic_cache.enabled', label: 'Semantic response cache', kind: 'bool' },
      { path: 'sandbox.enabled', label: 'Sandbox for generated tools', kind: 'bool' },
      { path: 'sandbox.allow_network', label: 'Sandbox may use the network', kind: 'bool' },
    ],
  },
  {
    // v0.7.1: approval-gated self-update (see Doctor → Change proposals)
    title: 'Self-update by agents',
    fields: [
      { path: 'medic.self_update.enabled', label: 'Agents may read the code and propose changes', kind: 'bool', help: 'Nothing is installed without your approval.' },
      { path: 'medic.self_update.auto_approve_low_risk', label: 'Install documentation-only changes without asking', kind: 'bool' },
      { path: 'medic.self_update.run_tests', label: 'Run the test suite before switching to a changed version', kind: 'bool', help: 'Needs pytest in the virtual environment.' },
    ],
  },
  {
    title: 'Timeouts & memory',
    fields: [
      { path: 'timeouts.llm_seconds', label: 'LLM request timeout (s)', kind: 'number' },
      { path: 'timeouts.shell_seconds', label: 'Shell command timeout (s)', kind: 'number' },
      { path: 'timeouts.http_seconds', label: 'HTTP request timeout (s)', kind: 'number' },
      { path: 'timeouts.task_seconds', label: 'Longest an agent may work on one message (s)', kind: 'number', help: '60–7200, default 600. Jobs use their own timeout.' },
      { path: 'memory.auto_cleanup_enabled', label: 'Delete old conversation history', kind: 'bool' },
      { path: 'memory.auto_cleanup_days', label: 'Keep history for (days)', kind: 'number' },
      { path: 'agents.summarization_threshold', label: 'Summarize after N messages', kind: 'number' },
      { path: 'agents.max_tool_rounds', label: 'Tool steps per chat message', kind: 'number', help: 'How many rounds of tool calls an agent may use to answer one message (1–50). At the limit it answers with what it found and you can say "continue". Jobs have their own setting.' },
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

interface TelegramBot {
  state: 'stopped' | 'starting' | 'running' | 'conflict' | 'error' | string;
  bot_username?: string | null;
  error?: string | null;
}

interface TelegramStatus {
  enabled: boolean;
  run_in_webui: boolean;
  token_set: boolean;
  allow_from: string[];
  allow_from_placeholder: boolean;
  bot: TelegramBot;
}

type TelegramTest = { ok: boolean; bot_username?: string | null; bot_name?: string | null; error?: string; loading?: boolean };

function botStateText(bot: TelegramBot): { text: string; ok: boolean } {
  switch (bot.state) {
    case 'running': return { text: bot.bot_username ? `Running as @${bot.bot_username}` : 'Running', ok: true };
    case 'starting': return { text: 'Starting…', ok: true };
    case 'conflict': return {
      text: "Another ZenSynora (zensynora gateway?) is already running this bot — stop it or turn off 'Run the bot inside the Web UI'.",
      ok: false,
    };
    case 'error': return { text: `Error: ${bot.error || 'the bot could not start.'}`, ok: false };
    default: return { text: 'Stopped', ok: false };
  }
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
  // v0.7.1: index of the named agent whose prompt is being generated
  const [generating, setGenerating] = useState<number | null>(null);
  // v0.7.5: skills that can be given to agents (names + descriptions)
  const [skillList, setSkillList] = useState<{ name: string; description: string }[]>([]);
  // v0.7.5: every provider's API keys (no secrets) — for the Keys lists and key pickers
  const [keysMap, setKeysMap] = useState<ProviderKeysMap | null>(null);
  const loadKeys = useCallback(async () => {
    try {
      setKeysMap((await api<{ providers: ProviderKeysMap }>('/api/provider-keys')).providers);
    } catch { /* optional: older server */ }
  }, []);
  // Telegram bot running inside the Web UI
  const [tgStatus, setTgStatus] = useState<TelegramStatus | null>(null);
  const [tgTest, setTgTest] = useState<TelegramTest | null>(null);
  const [tgBusy, setTgBusy] = useState(false);
  const [tgError, setTgError] = useState<string | null>(null);
  const loadTelegram = useCallback(async () => {
    try {
      setTgStatus(await api<TelegramStatus>('/api/telegram/status'));
    } catch { /* optional panel */ }
  }, []);
  useEffect(() => {
    api<{ skills: { name: string; description: string }[] }>('/api/skills').then(r => setSkillList(r.skills)).catch(() => { /* optional */ });
  }, []);
  const skillChips = (current: string[], onChange: (next: string[]) => void) => (
    <div className="field">
      <span className="field-label">Skills <span className="muted small">— instructions this agent can load (manage them on the Skills page)</span></span>
      <div className="chip-row">
        {skillList.map(s => {
          const on = current.includes(s.name);
          return (
            <button type="button" key={s.name} className={`chip small-chip ${on ? 'active' : ''}`} title={s.description}
              onClick={() => onChange(on ? current.filter(x => x !== s.name) : [...current, s.name])}>{s.name}</button>
          );
        })}
        {skillList.length === 0 && <span className="muted small">No skills found.</span>}
      </div>
    </div>
  );

  // 2026-10-27: agent level 1-9 (a label only for now — no permissions)
  const levelField = (value: number | undefined, onChange: (n: number) => void, changed = false) => (
    <div className="field">
      <label className="field-label">Level <LevelBadge level={value} />{changed && <span className="changed-dot" />}
        <span className="muted small"> — a label only for now; it does not change what the agent may do</span></label>
      <select className="field-input narrow" value={value || 1} onChange={e => onChange(Number(e.target.value))}>
        {[1, 2, 3, 4, 5, 6, 7, 8, 9].map(n => <option key={n} value={n}>{n}</option>)}
      </select>
    </div>
  );

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

  useEffect(() => { load(); loadKeys(); }, [load, loadKeys]);

  const secretSet = useMemo(() => new Set(data?.secrets_set || []), [data]);
  const secretPaths = useMemo(() => new Set(data?.secret_paths || []), [data]);

  const update = (path: string, value: Json) => {
    // A key belongs to one provider: switching provider goes back to its default key.
    if (path === 'agents.defaults.provider' && getPath(draft, 'agents.defaults.key')) update('agents.defaults.key', '');
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
      loadTelegram();
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

  // Models of a provider with one of its keys (Agents tab model list).
  const probeKeyId = (provider: string, key?: string) => (key ? `${provider}#${key}` : provider);
  const runKeyProbe = async (provider: string, key?: string) => {
    const id = probeKeyId(provider, key);
    setProbe(p => ({ ...p, [id]: { ok: false, models: [], loading: true } }));
    try {
      const res = await api<ProbeResult>('/api/settings/probe', { method: 'POST', body: JSON.stringify({ provider, key_id: key || null }) });
      setProbe(p => ({ ...p, [id]: res }));
    } catch (e) {
      setProbe(p => ({ ...p, [id]: { ok: false, models: [], error: (e as Error).message } }));
    }
  };

  // Agents tab: load the model list of every provider + key in use (once each).
  const agentPairs: string = !draft ? '' : JSON.stringify([
    [getPath(draft, 'agents.defaults.provider'), getPath(draft, 'agents.defaults.key') || ''],
    ...((getPath(draft, 'agents.named') || []) as Json[]).map((a: Json) => [
      a.provider || getPath(draft, 'agents.defaults.provider'),
      a.key || (a.provider ? '' : getPath(draft, 'agents.defaults.key') || ''),
    ]),
  ]);
  useEffect(() => {
    if (tab !== 'Agents' || !agentPairs) return;
    const seen = new Set<string>();
    for (const [prov, key] of JSON.parse(agentPairs) as [string, string][]) {
      const id = probeKeyId(prov, key);
      if (!prov || seen.has(id) || probe[id]) continue;
      seen.add(id);
      runKeyProbe(prov, key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, agentPairs]);

  const testTelegram = async () => {
    setTgTest({ ok: false, loading: true });
    const token = changes['channels.telegram.token'];
    try {
      setTgTest(await api<TelegramTest>('/api/telegram/test', {
        method: 'POST',
        body: JSON.stringify({ token: token || null }),
      }));
    } catch (e) {
      setTgTest({ ok: false, error: (e as Error).message });
    }
  };

  const restartTelegram = async () => {
    setTgBusy(true);
    setTgError(null);
    try {
      await api('/api/telegram/restart', { method: 'POST' });
    } catch (e) {
      setTgError((e as Error).message);
    } finally {
      setTgBusy(false);
      loadTelegram();
    }
  };

  // "Send test message": the bot writes to the allowed users (checks the whole path).
  const [tgSent, setTgSent] = useState<{ ok: boolean; text: string } | null>(null);
  const sendTelegramTest = async () => {
    setTgSent(null);
    try {
      const r = await api<{ ok: boolean; results: { chat_id: string; ok: boolean; error?: string }[] }>(
        '/api/telegram/send-test', { method: 'POST', body: '{}' });
      setTgSent({
        ok: r.ok,
        text: r.results.map(x => (x.ok ? `Sent to ${x.chat_id} ✔` : `${x.chat_id}: ${x.error}`)).join(' · '),
      });
    } catch (e) {
      setTgSent({ ok: false, text: (e as Error).message });
    }
  };

  // Refresh the bot state while the Channels tab is open (it may change on its own).
  useEffect(() => {
    if (tab !== 'Channels') return;
    loadTelegram();
    const t = window.setInterval(loadTelegram, 5000);
    return () => window.clearInterval(t);
  }, [tab, loadTelegram]);

  // v0.7.1: write an agent's system prompt from its name + description with the main LLM.
  const generatePrompt = async (i: number, a: Json, onDone: (prompt: string) => void) => {
    if (a.system_prompt?.trim() && !window.confirm('Replace the current system prompt with a generated one?')) return;
    setGenerating(i);
    setNotice(null);
    try {
      const res = await api<{ prompt: string; provider: string; model: string }>('/api/agents/generate-prompt', {
        method: 'POST',
        body: JSON.stringify({ name: a.name || '', description: a.description || '' }),
      });
      onDone(res.prompt);
      setNotice({ kind: 'ok', text: `Prompt written by ${res.provider}/${res.model} — review it, then Save settings.` });
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    } finally {
      setGenerating(null);
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
            {(keysMap?.[defaultProvider]?.keys.length || 0) > 1 && (
              <div className="field">
                <label className="field-label">Key{'agents.defaults.key' in changes && <span className="changed-dot" />}</label>
                <KeySelect provider={defaultProvider} keysMap={keysMap} value={getPath(draft, 'agents.defaults.key')}
                  onChange={k => update('agents.defaults.key', k)} />
              </div>
            )}
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
            <LatencyTest provider={defaultProvider} model={getPath(draft, 'agents.defaults.model') || ''} keyId={getPath(draft, 'agents.defaults.key') || ''} />
            <h4>{PROVIDER_LABELS[defaultProvider] || defaultProvider} connection</h4>
            {providerFields(defaultProvider).map(renderField)}
            <ProbeLine provider={defaultProvider} />
          </div>
        )}

        {tab === 'Providers' && (
          <div className="notice">Add local or online providers of your own (vLLM, LocalAI, DeepSeek, Mistral, Cerebras, GitHub Models…) on the <b>Models</b> page.</div>
        )}
        {tab === 'Providers' && data.providers.filter(p => PROVIDER_LABELS[p]).map(p => (
          <div className="settings-card" key={p}>
            <h3>{PROVIDER_LABELS[p] || p}{p === defaultProvider && <span className="badge default-badge">default</span>}</h3>
            {providerFields(p).map(renderField)}
            <ProbeLine provider={p} />
            <ProviderKeysPanel provider={p} keysMap={keysMap} onChanged={next => { setKeysMap(next); onSaved?.(); }} />
          </div>
        ))}

        {tab === 'Agents' && (
          <div className="settings-card">
            <h3>Default agent</h3>
            <p className="muted">Its model is also the default for named agents that don't pick their own.</p>
            <div className="agent-editor">
              <div className="agent-editor-grid three-model">
                <div className="field">
                  <label className="field-label">Provider{'agents.defaults.provider' in changes && <span className="changed-dot" />}</label>
                  <select className="field-input" value={defaultProvider} onChange={e => update('agents.defaults.provider', e.target.value)}>
                    {data.providers.map(p => <option key={p} value={p}>{PROVIDER_LABELS[p] || p}</option>)}
                  </select>
                </div>
                <div className="field">
                  <label className="field-label">Key</label>
                  {(keysMap?.[defaultProvider]?.keys.length || 0) > 1 || getPath(draft, 'agents.defaults.key')
                    ? <KeySelect provider={defaultProvider} keysMap={keysMap} value={getPath(draft, 'agents.defaults.key') || ''}
                      onChange={k => update('agents.defaults.key', k)} />
                    : <span className="muted small key-none">Default key</span>}
                </div>
                <div className="field">
                  <label className="field-label">Model{'agents.defaults.model' in changes && <span className="changed-dot" />}</label>
                  <ModelPicker value={getPath(draft, 'agents.defaults.model') || ''} onChange={m => update('agents.defaults.model', m)}
                    probe={probe[probeKeyId(defaultProvider, getPath(draft, 'agents.defaults.key') || '')]}
                    onRefresh={() => runKeyProbe(defaultProvider, getPath(draft, 'agents.defaults.key') || '')} />
                </div>
              </div>
              <LatencyTest provider={defaultProvider} model={getPath(draft, 'agents.defaults.model') || ''} keyId={getPath(draft, 'agents.defaults.key') || ''} />
              <ContextLength agentName="default" systemPrompt="" skills={getPath(draft, 'agents.defaults.skills') || []}
                provider={defaultProvider} model={getPath(draft, 'agents.defaults.model') || ''} keyId={getPath(draft, 'agents.defaults.key') || ''}
                value={getPath(draft, 'agents.defaults.context_length') || 0} onChange={v => update('agents.defaults.context_length', v)} />
              {levelField(getPath(draft, 'agents.defaults.level'), n => update('agents.defaults.level', n), 'agents.defaults.level' in changes)}
            </div>
            {skillChips(getPath(draft, 'agents.defaults.skills') || [], next => update('agents.defaults.skills', next))}
            <h3>Named agents</h3>
            <p className="muted">Extra agents appear in the sidebar and can be addressed in chat with <code>@name</code>.</p>
            {named.length === 0 && <div className="muted">No named agents yet.</div>}
            {named.map((a: Json, i: number) => {
              const setAgent = (key: string, val: Json) => {
                const next = named.map((x: Json, j: number) => (j === i ? { ...x, [key]: val } : x));
                update('agents.named', next);
              };
              // v0.7.5: provider → key → model; the model list comes from that provider + key
              const agentProvider: string = a.provider || defaultProvider;
              const agentKey: string = a.key || (a.provider ? '' : getPath(draft, 'agents.defaults.key') || '');
              const agentModels = probe[probeKeyId(agentProvider, agentKey)];
              return (
                <div className="agent-editor" key={i}>
                  <div className="agent-editor-grid four">
                    <div className="field">
                      <label className="field-label">Name <LevelBadge level={a.level} /></label>
                      <input className="field-input" value={a.name} onChange={e => setAgent('name', e.target.value.replace(/\s+/g, '_'))} />
                    </div>
                    <div className="field">
                      <label className="field-label">Provider</label>
                      <select className="field-input" value={a.provider || ''}
                        onChange={e => update('agents.named', named.map((x: Json, j: number) => (j === i ? { ...x, provider: e.target.value, key: '' } : x)))}>
                        <option value="">Same as default ({defaultProvider})</option>
                        {data.providers.map(p => <option key={p} value={p}>{PROVIDER_LABELS[p] || p}</option>)}
                      </select>
                    </div>
                    <div className="field">
                      <label className="field-label">Key</label>
                      {(keysMap?.[agentProvider]?.keys.length || 0) > 1 || a.key
                        ? <KeySelect provider={agentProvider} keysMap={keysMap} value={a.key || ''} onChange={k => setAgent('key', k)} />
                        : <span className="muted small key-none">Default key</span>}
                    </div>
                    <div className="field">
                      <label className="field-label">Model</label>
                      <ModelPicker value={a.model || ''} onChange={m => setAgent('model', m)} probe={agentModels}
                        placeholder={a.provider ? 'Pick a model…' : `Default (${getPath(draft, 'agents.defaults.model') || 'none'})`}
                        onRefresh={() => runKeyProbe(agentProvider, agentKey)} />
                    </div>
                  </div>
                  <LatencyTest provider={agentProvider} model={a.model || getPath(draft, 'agents.defaults.model') || ''} keyId={agentKey} />
                  <ContextLength agentName={a.name || `agent${i + 1}`} systemPrompt={a.system_prompt || ''} skills={a.skills || []}
                    provider={agentProvider} model={a.model || getPath(draft, 'agents.defaults.model') || ''} keyId={agentKey}
                    value={a.context_length || 0} onChange={v => setAgent('context_length', v)} />
                  {levelField(a.level, n => setAgent('level', n))}
                  <div className="field">
                    <label className="field-label">Short description</label>
                    <input className="field-input" value={a.description || ''} placeholder="e.g. Reviews Python code for bugs and explains fixes simply"
                      onChange={e => setAgent('description', e.target.value)} />
                  </div>
                  <div className="field">
                    <div className="field-label-row">
                      <label className="field-label">System prompt</label>
                      <button type="button" className="ghost-btn" disabled={generating !== null || !(a.name || a.description)}
                        title="Write the prompt from the name and description with the default (main) model"
                        onClick={() => generatePrompt(i, a, prompt => setAgent('system_prompt', prompt))}>
                        {generating === i ? 'Writing…' : '✨ Generate prompt'}
                      </button>
                    </div>
                    <textarea className="field-input" rows={a.system_prompt ? 8 : 3} value={a.system_prompt || ''} onChange={e => setAgent('system_prompt', e.target.value)} />
                  </div>
                  {skillChips(a.skills || [], next => setAgent('skills', next))}
                  <button type="button" className="ghost-btn danger" onClick={() => update('agents.named', named.filter((_: Json, j: number) => j !== i))}>
                    Remove agent
                  </button>
                </div>
              );
            })}
            <button type="button" className="ghost-btn" onClick={() => update('agents.named', [...named, {
              name: `agent${named.length + 1}`, provider: '', key: '', model: getPath(draft, 'agents.defaults.model') || '', system_prompt: '', description: '', skills: [], context_length: 0, level: 1,
            }])}>+ Add agent</button>
          </div>
        )}
        {tab === 'Agents' && (
          <AgentFusions dirty={dirtyCount > 0}
            agents={[{ name: 'default', level: getPath(data.config, 'agents.defaults.level') },
              ...((getPath(data.config, 'agents.named') || []) as Json[]).map((x: Json) => ({ name: x.name, level: x.level }))]}
            onChanged={() => { load(); onSaved?.(); }} />
        )}

        {tab === 'Channels' && (
          <div className="settings-card">
            <h3>Messaging channels</h3>
            <p className="muted">The Telegram bot runs inside the Web UI and restarts by itself when you save. WhatsApp runs in the gateway (<code>zensynora gateway</code>) — restart it after changing those.</p>
            {tgStatus && (
              <div className="telegram-status">
                <h4>Telegram bot</h4>
                <div className="probe-row">
                  <span>Status:</span>
                  {!tgStatus.enabled ? <span className="muted">Turned off</span>
                    : !tgStatus.run_in_webui ? <span className="muted">Runs in zensynora gateway, not in the Web UI</span>
                      : !tgStatus.token_set ? <span className="muted">No bot token yet</span>
                        : <span className={botStateText(tgStatus.bot).ok ? 'probe-ok' : 'probe-err'}>{botStateText(tgStatus.bot).text}</span>}
                </div>
                {tgStatus.enabled && tgStatus.allow_from_placeholder && (
                  <div className="notice">Nobody can use the bot yet: add your user id to <b>Allowed Telegram users</b> below.</div>
                )}
                <div className="probe-row">
                  <button type="button" className="ghost-btn" onClick={testTelegram} disabled={tgTest?.loading}>
                    {tgTest?.loading ? 'Testing…' : 'Test bot'}
                  </button>
                  <button type="button" className="ghost-btn" onClick={restartTelegram}
                    disabled={tgBusy || !tgStatus.enabled || !tgStatus.run_in_webui || !tgStatus.token_set}>
                    {tgBusy ? 'Restarting…' : 'Restart bot'}
                  </button>
                  <button type="button" className="ghost-btn" onClick={sendTelegramTest}
                    disabled={tgStatus.bot?.state !== 'running'}
                    title="The bot sends a short message to every user id in Allowed Telegram users">
                    Send test message
                  </button>
                  {tgTest && !tgTest.loading && (
                    <span className={tgTest.ok ? 'probe-ok' : 'probe-err'}>
                      {tgTest.ok ? `Token works — bot @${tgTest.bot_username}${tgTest.bot_name ? ` (${tgTest.bot_name})` : ''}` : tgTest.error}
                    </span>
                  )}
                  {tgError && <span className="probe-err">{tgError}</span>}
                  {tgSent && <span className={tgSent.ok ? 'probe-ok' : 'probe-err'}>{tgSent.text}</span>}
                </div>
              </div>
            )}
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
