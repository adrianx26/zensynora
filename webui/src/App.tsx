// ZenSynora Web UI shell.
//
// v0.7.1: the single-agent chat was replaced by channels — group rooms with
// several agents (#general, …) and direct messages with one agent — plus the
// Models, Jobs and Backups pages. Agent-to-agent activity (delegations, tool
// calls, job runs) is shown inside each channel.
import React, { useCallback, useEffect, useState } from 'react';
import './index.css';
import ChatView from './components/ChatView';
import { agentColor } from './colors';
import SettingsView from './components/SettingsView';
import ToolsView from './components/ToolsView';
import DoctorView from './components/DoctorView';
import CostDashboard from './components/CostDashboard';
import ModelsView from './components/ModelsView';
import JobsView from './components/JobsView';
import BackupsView from './components/BackupsView';
import ChannelsView from './components/ChannelsView';
import UpdatesView from './components/UpdatesView';
import SkillsView from './components/SkillsView';
import { restartServer } from './updates';
import { api, getAdminKey, getApiBase, type AgentInfo, type Channel, type SystemHealth } from './api';

type View = 'chat' | 'channels' | 'models' | 'jobs' | 'skills' | 'backups' | 'updates' | 'settings' | 'costs' | 'tools' | 'doctor';
const VIEWS: View[] = ['chat', 'channels', 'models', 'jobs', 'skills', 'backups', 'updates', 'settings', 'costs', 'tools', 'doctor'];
const VIEW_KEY = 'zensynora_view';
const CHANNEL_KEY = 'zensynora_channel';
const THEME_KEY = 'zensynora_theme';
const VIEW_TITLES: Record<View, string> = {
  chat: 'Chat', channels: 'Channels', models: 'Models', jobs: 'Jobs', skills: 'Skills', backups: 'Backups', updates: 'Updates', settings: 'Settings',
  costs: 'Costs & usage', tools: 'Tools', doctor: 'Doctor',
};

function load<T extends string>(key: string, allowed: readonly T[] | null, fallback: T): T {
  try {
    const v = localStorage.getItem(key) as T | null;
    if (v && (!allowed || allowed.includes(v))) return v;
  } catch { /* storage may be blocked */ }
  return fallback;
}

function store(key: string, value: string) {
  try { localStorage.setItem(key, value); } catch { /* ignore */ }
}

function setThemeDoc(theme: 'dark' | 'light') {
  document.documentElement.setAttribute('data-theme', theme);
  store(THEME_KEY, theme);
}

function HealthRow({ label, value, state }: { label: string; value: string; state?: 'ok' | 'warn' | 'err' | 'off' }) {
  return (
    <div className="health-row">
      <span>{label}</span>
      <span className={`health-value ${state || ''}`} title={value}>{value}</span>
    </div>
  );
}

function NavButton({ id, icon, label, count, view, go }: {
  id: View; icon: string; label: string; count?: number; view: View; go: (v: View) => void;
}) {
  return (
    <button className={`nav-btn ${view === id ? 'active' : ''}`} onClick={() => go(id)} aria-current={view === id ? 'page' : undefined}>
      <span className="nav-icon" aria-hidden>{icon}</span>{label}
      {!!count && <span className="nav-count">{count}</span>}
    </button>
  );
}

function App() {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [channelId, setChannelId] = useState<string>(() => load(CHANNEL_KEY, null, 'general'));
  const [theme, setTheme] = useState<'dark' | 'light'>(() => load(THEME_KEY, ['dark', 'light'] as const, 'dark'));
  const [view, setView] = useState<View>(() => load(VIEW_KEY, VIEWS, 'chat'));
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [restarting, setRestarting] = useState<string | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [newChannel, setNewChannel] = useState<{ name: string; members: string[] } | null>(null);
  const [pending, setPending] = useState(0); // proposals waiting for approval
  const [error, setError] = useState<string | null>(null);

  useEffect(() => { setThemeDoc(theme); }, [theme]);
  // v0.7.2: chat history now lives on the server (chat channels). Remove the
  // per-agent copies the pre-0.7.1 chat kept in this browser.
  useEffect(() => {
    try {
      Object.keys(localStorage).filter(k => k.startsWith('zensynora_chat_history_')).forEach(k => localStorage.removeItem(k));
    } catch { /* storage may be blocked */ }
  }, []);
  useEffect(() => { store(VIEW_KEY, view); }, [view]);
  useEffect(() => { store(CHANNEL_KEY, channelId); }, [channelId]);

  const loadAgents = useCallback(() => {
    api<{ agents: AgentInfo[] }>('/api/agents').then(d => setAgents(d.agents || [])).catch(() => { /* health shows it */ });
  }, []);

  const loadChannels = useCallback(() => {
    api<{ channels: Channel[] }>('/api/channels')
      .then(d => {
        setChannels(d.channels);
        setChannelId(cur => (d.channels.some(c => c.id === cur) ? cur : 'general'));
      })
      .catch(e => setError((e as Error).message));
  }, []);

  const loadHealth = useCallback(() => {
    api<SystemHealth>('/api/system/health').then(h => { setHealth(h); setHealthError(false); }).catch(() => setHealthError(true));
    api<{ proposals: { status: string }[] }>('/api/medic/proposals?status=pending')
      .then(d => setPending(d.proposals.length)).catch(() => setPending(0));
  }, []);

  useEffect(() => {
    loadAgents();
    loadChannels();
    loadHealth();
    const iv = setInterval(loadHealth, 15000);
    return () => clearInterval(iv);
  }, [loadAgents, loadChannels, loadHealth]);

  const go = (v: View) => { setView(v); setSidebarOpen(false); };

  const openChannel = (id: string) => { setChannelId(id); go('chat'); };

  const openDm = async (agent: string) => {
    try {
      const ch = await api<Channel>(`/api/channels/dm/${encodeURIComponent(agent)}`);
      loadChannels();
      openChannel(ch.id);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const createChannel = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newChannel?.name.trim()) return;
    try {
      const ch = await api<Channel>('/api/channels', {
        method: 'POST',
        body: JSON.stringify({ name: newChannel.name.trim(), members: newChannel.members.length ? newChannel.members : ['default'], reply_mode: newChannel.members.length > 1 ? 'all' : 'lead' }),
      });
      setNewChannel(null);
      loadChannels();
      openChannel(ch.id);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  const deleteChannel = async (ch: Channel) => {
    if (ch.id === 'general') return;
    if (!window.confirm(`Delete ${ch.kind === 'dm' ? 'the conversation with @' + ch.members[0] : '#' + ch.name} and its history?`)) return;
    try {
      await api(`/api/channels/${encodeURIComponent(ch.id)}`, { method: 'DELETE' });
      if (channelId === ch.id) setChannelId('general');
      loadChannels();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const onSettingsSaved = () => { loadAgents(); loadHealth(); };

  const rooms = channels.filter(c => c.kind === 'channel');
  const dms = channels.filter(c => c.kind === 'dm');
  const llm = health?.llm;

  return (
    <div className="app-container">
      <button className="mobile-sidebar-toggle" onClick={() => setSidebarOpen(p => !p)} aria-label="Toggle sidebar">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" />
        </svg>
      </button>

      <div className={`sidebar glass-panel ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-section">
          <div className="sidebar-title">
            <span>Channels</span>
            <span className="row-actions">
              <button className="icon-btn" title="New channel" onClick={() => setNewChannel(n => (n ? null : { name: '', members: ['default'] }))}>＋</button>
              <button className="icon-btn" title="Manage channels" onClick={() => go('channels')}>⚙</button>
            </span>
          </div>
          {newChannel && (
            <form className="new-channel" onSubmit={createChannel}>
              <input className="field-input" autoFocus placeholder="channel name, e.g. research" value={newChannel.name}
                onChange={e => setNewChannel({ ...newChannel, name: e.target.value })} />
              <div className="chip-row tight">
                {agents.map(a => (
                  <button type="button" key={a.name} className={`chip small ${newChannel.members.includes(a.name) ? 'active' : ''}`}
                    onClick={() => setNewChannel({ ...newChannel, members: newChannel.members.includes(a.name) ? newChannel.members.filter(m => m !== a.name) : [...newChannel.members, a.name] })}>
                    @{a.name}
                  </button>
                ))}
              </div>
              <button className="primary-btn" type="submit" disabled={!newChannel.name.trim()}>Create</button>
            </form>
          )}
          <div className="channel-list">
            {rooms.map(c => (
              <div key={c.id} className={`channel-item ${view === 'chat' && channelId === c.id ? 'active' : ''}`} onClick={() => openChannel(c.id)}>
                <span className="channel-hash">#</span>
                <span className="channel-name">{c.name}</span>
                <span className="channel-count" title={c.members.join(', ')}>{c.members.length}</span>
                {c.id !== 'general' && <button className="icon-btn hover-only" title="Delete channel" onClick={e => { e.stopPropagation(); deleteChannel(c); }}>×</button>}
              </div>
            ))}
          </div>
        </div>

        <div className="sidebar-section bordered">
          <div className="sidebar-title"><span>Agents</span><button className="icon-btn" title="Add or edit agents" onClick={() => go('settings')}>⚙</button></div>
          <div className="agent-list">
            {agents.map(a => {
              const dm = dms.find(d => d.members[0] === a.name);
              const active = view === 'chat' && dm && channelId === dm.id;
              return (
                <div key={a.name} className={`agent-item ${active ? 'active' : ''}`} onClick={() => openDm(a.name)} title={a.description || `Direct chat with @${a.name}`}>
                  <div className="agent-avatar" style={{ background: agentColor(a.name), color: '#0d1117' }}>{a.name.charAt(0).toUpperCase()}</div>
                  <div className="agent-info">
                    <span className="agent-name">@{a.name}</span>
                    <span className="agent-status">{a.provider ? `${a.provider} · ` : ''}{a.model}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="sidebar-section bordered">
          <div className="sidebar-title">Navigate</div>
          <nav className="nav-list">
            <NavButton id="chat" icon="💬" label="Chat" view={view} go={go} />
            <NavButton id="channels" icon="#" label="Channels" view={view} go={go} />
            <NavButton id="models" icon="🧠" label="Models" view={view} go={go} />
            <NavButton id="jobs" icon="⏱" label="Jobs" view={view} go={go} />
            <NavButton id="skills" icon="📚" label="Skills" view={view} go={go} />
            <NavButton id="backups" icon="💾" label="Backups" view={view} go={go} />
            <NavButton id="updates" icon="⬆️" label="Updates" count={health?.update_installed ? 1 : undefined} view={view} go={go} />
            <NavButton id="settings" icon="⚙️" label="Settings" view={view} go={go} />
            <NavButton id="costs" icon="📊" label="Costs & usage" view={view} go={go} />
            <NavButton id="tools" icon="🧰" label="Tools" view={view} go={go} />
            <NavButton id="doctor" icon="🩺" label="Doctor" count={pending} view={view} go={go} />
          </nav>
        </div>

        <div className="sidebar-section bordered">
          <div className="sidebar-title">
            <span>System health</span>
            <button className="icon-btn" onClick={loadHealth} title="Refresh" aria-label="Refresh health">↻</button>
          </div>
          {healthError && <div className="health-row"><span className="health-value err">Backend unreachable</span></div>}
          {health && (
            <div className="health-list">
              <HealthRow label="LLM" value={llm?.ok ? `${llm.provider} · ${llm.latency_ms} ms` : `${llm?.provider}: offline`}
                state={llm?.ok ? (llm.model_available === false ? 'warn' : 'ok') : 'err'} />
              {llm?.ok && llm.model_available === false && <div className="health-note">Model “{llm.model}” not found — pick one on the Models page.</div>}
              {!llm?.ok && llm?.error && <div className="health-note">{llm.error}</div>}
              <HealthRow label="Knowledge" value={health.knowledge.ok ? `${health.knowledge.entities ?? 0} notes` : 'error'} state={health.knowledge.ok ? 'ok' : 'err'} />
              <HealthRow label="Tools" value={`${health.tools}`} />
              <HealthRow label="Messaging"
                value={[health.channels.telegram && 'Telegram', health.channels.whatsapp && 'WhatsApp'].filter(Boolean).join(', ') || 'none'}
                state={health.channels.telegram || health.channels.whatsapp ? 'ok' : 'off'} />
              <button className="link-btn" onClick={() => go('doctor')}>Full diagnostics →</button>
              {!health.config_file_exists && <div className="health-note">Not configured yet — open Models to pick a model.</div>}
            </div>
          )}
        </div>

        <div className="sidebar-section sidebar-controls bordered">
          <button className="control-btn" onClick={() => setTheme(t => (t === 'dark' ? 'light' : 'dark'))}>
            {theme === 'dark' ? '☀️ Light mode' : '🌙 Dark mode'}
          </button>
          {health?.version && (
            <button className="app-version link-btn" onClick={() => go('updates')} title="Check for updates">ZenSynora v{health.version}</button>
          )}
        </div>
      </div>

      <div className="main-content">
        <div className="glass-panel main-panel">
          {view !== 'chat' && (
            <div className="main-header">
              <h2 className="text-gradient">ZenSynora</h2>
              <span className="view-title">{VIEW_TITLES[view]}</span>
              <div className="header-actions"><button className="ghost-btn" onClick={() => go('chat')}>← Back to chat</button></div>
            </div>
          )}
          {/* v0.7.2: the server still runs an older version than the one installed */}
          {health?.update_installed && view !== 'updates' && (
            <div className="update-banner">
              {health.update_installed.installed === health.update_installed.running
                ? <><b>A new copy of ZenSynora {health.update_installed.installed} is installed</b>; restart to use it.</>
                : <><b>ZenSynora {health.update_installed.installed} is installed</b>, but this server still runs {health.update_installed.running}.</>}
              {' '}<button className="primary-btn" disabled={!!restarting} onClick={async () => {
                try { await restartServer(setRestarting); } catch (e) { setRestarting(null); setError((e as Error).message); }
              }}>{restarting || 'Restart now'}</button>
            </div>
          )}
          {error && <div className="notice error">{error} <button className="ghost-btn" onClick={() => setError(null)}>×</button></div>}

          {view === 'chat' && (
            <ChatView key={channelId} channelId={channelId} agents={agents} onChannelChanged={loadChannels} onOpenSettings={() => go('settings')} onAgentsChanged={loadAgents} />
          )}
          {view === 'models' && <ModelsView onChanged={onSettingsSaved} />}
          {view === 'jobs' && <JobsView agents={agents} channels={channels} />}
          {view === 'channels' && <ChannelsView agents={agents} onChanged={loadChannels} onOpen={openChannel} />}
          {view === 'updates' && <UpdatesView />}
          {view === 'skills' && <SkillsView />}
          {view === 'backups' && <BackupsView onRestored={() => { loadAgents(); loadChannels(); loadHealth(); }} />}
          {view === 'settings' && <SettingsView onSaved={onSettingsSaved} />}
          {view === 'tools' && <ToolsView />}
          {view === 'doctor' && <DoctorView />}
          {view === 'costs' && (
            <div className="settings-view"><div className="settings-body">
              <CostDashboard apiBase={getApiBase()} apiKey={getAdminKey() || undefined} />
            </div></div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
