import React, { useState, useEffect, useRef, useCallback } from 'react';
import './index.css';
import TypewriterText from './components/TypewriterText';
import SettingsView from './components/SettingsView';
import ToolsView from './components/ToolsView';
import DoctorView from './components/DoctorView';
import CostDashboard from './components/CostDashboard';
import { api, getApiBase, getWsBase, getAdminKey, type SystemHealth } from './api';

type View = 'chat' | 'settings' | 'costs' | 'tools' | 'doctor';
const VIEW_KEY = 'zensynora_view';
const VIEW_TITLES: Record<View, string> = {
  chat: 'Chat', settings: 'Settings', costs: 'Costs & usage', tools: 'Tools', doctor: 'Doctor',
};

function getSavedView(): View {
  try {
    const v = localStorage.getItem(VIEW_KEY);
    if (v === 'chat' || v === 'settings' || v === 'costs' || v === 'tools' || v === 'doctor') return v;
  } catch { /* ignore */ }
  return 'chat';
}

interface Agent {
  name: string;
  model: string;
  provider?: string;
}

interface ChatMessage {
  sender: string;
  text: string;
  isStreaming?: boolean;
  tools?: ToolInfo[];
}

interface ToolInfo {
  name: string;
  status: string;
  args?: Record<string, unknown>;
}

// ── localStorage helpers for message persistence ─────────────────────────────
const STORAGE_KEY = 'zensynora_chat_history';
const THEME_KEY = 'zensynora_theme';

function loadMessages(agent: string): ChatMessage[] {
  try {
    const raw = localStorage.getItem(`${STORAGE_KEY}_${agent}`);
    if (raw) return JSON.parse(raw);
  } catch { /* ignore */ }
  return [{ sender: 'system', text: 'Connecting to ZenSynora Gateway...' }];
}

function saveMessages(agent: string, messages: ChatMessage[]) {
  try {
    const toSave = messages.map(m => ({ sender: m.sender, text: m.text }));
    localStorage.setItem(`${STORAGE_KEY}_${agent}`, JSON.stringify(toSave));
  } catch { /* ignore */ }
}

// ── Theme toggle helper ──────────────────────────────────────────────────────
function getSavedTheme(): 'dark' | 'light' {
  try {
    const t = localStorage.getItem(THEME_KEY);
    if (t === 'light' || t === 'dark') return t;
  } catch { /* ignore */ }
  return 'dark';
}

function setThemeDoc(theme: 'dark' | 'light') {
  document.documentElement.setAttribute('data-theme', theme);
  try { localStorage.setItem(THEME_KEY, theme); } catch { /* ignore */ }
}


function HealthRow({ label, value, state }: { label: string; value: string; state?: 'ok' | 'warn' | 'err' | 'off' }) {
  return (
    <div className="health-row">
      <span>{label}</span>
      <span className={`health-value ${state || ''}`} title={value}>{value}</span>
    </div>
  );
}

function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeAgent, setActiveAgent] = useState<string>('default');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [theme, setTheme] = useState<'dark' | 'light'>(getSavedTheme);
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'open' | 'closed'>('connecting');
  const [gaveUp, setGaveUp] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);
  const [view, setView] = useState<View>(getSavedView);
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttempts = useRef(0);
  const MAX_RECONNECT_ATTEMPTS = 10;
  const RECONNECT_BASE_DELAY = 1000;

  // Track pending tool calls during streaming
  const pendingToolsRef = useRef<ToolInfo[]>([]);
  // Latest connect function, used by the reconnect timer.
  const connectRef = useRef<() => void>(() => {});

  useEffect(() => { setThemeDoc(theme); }, [theme]);
  useEffect(() => { try { localStorage.setItem(VIEW_KEY, view); } catch { /* ignore */ } }, [view]);

  const toggleTheme = useCallback(() => {
    setTheme(prev => {
      const next = prev === 'dark' ? 'light' : 'dark';
      setThemeDoc(next);
      return next;
    });
  }, []);

  // ── Agents & live health ───────────────────────────────────────────────────
  const loadAgents = useCallback(() => {
    api<{ agents: Agent[] }>('/api/agents')
      .then(data => {
        const list = data.agents || [];
        setAgents(list);
        setActiveAgent(cur => (list.some(a => a.name === cur) ? cur : 'default'));
      })
      .catch(err => console.error('Error fetching agents:', err));
  }, []);

  const loadHealth = useCallback(() => {
    api<SystemHealth>('/api/system/health')
      .then(h => { setHealth(h); setHealthError(false); })
      .catch(() => setHealthError(true));
  }, []);

  useEffect(() => {
    loadAgents();
    loadHealth();
    const iv = setInterval(loadHealth, 15000);
    return () => clearInterval(iv);
  }, [loadAgents, loadHealth]);

  // ── Load / persist messages per agent ──────────────────────────────────────
  useEffect(() => { setMessages(loadMessages(activeAgent)); }, [activeAgent]);
  useEffect(() => { saveMessages(activeAgent, messages); }, [activeAgent, messages]);

  // ── WebSocket with reconnection, heartbeat, and streaming ──────────────────
  const teardownSocket = useCallback(() => {
    const ws = wsRef.current;
    if (ws) {
      // Detach handlers first so an intentional close does not trigger the
      // auto-reconnect logic for the previous agent.
      ws.onopen = null; ws.onclose = null; ws.onerror = null; ws.onmessage = null;
      ws.close();
      wsRef.current = null;
    }
    if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
    if (heartbeatRef.current) clearInterval(heartbeatRef.current);
    reconnectTimeoutRef.current = null;
    heartbeatRef.current = null;
  }, []);

  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const ws = new WebSocket(`${getWsBase()}/ws/chat/${encodeURIComponent(activeAgent)}`);
    wsRef.current = ws;
    setConnectionStatus('connecting');

    ws.onopen = () => {
      reconnectAttempts.current = 0;
      setGaveUp(false);
      setConnectionStatus('open');
      setMessages(prev => {
        if (prev.length === 1 && prev[0].sender === 'system' && prev[0].text.includes('Connecting')) {
          return [{ sender: 'system', text: `Connected to agent [${activeAgent}]` }];
        }
        return prev;
      });

      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
      heartbeatRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) ws.send('__ping__');
      }, 30000);
    };

    ws.onmessage = (event) => {
      const data = event.data as string;

      if (data === '__pong__') return;

      if (data === '__STREAM_START__') {
        setIsStreaming(true);
        setMessages(prev => [...prev, { sender: 'agent', text: '', isStreaming: true }]);
        return;
      }

      if (data === '__STREAM_END__') {
        setIsStreaming(false);
        setMessages(prev => {
          const last = prev[prev.length - 1];
          if (last && last.sender === 'agent' && last.isStreaming) {
            const updated = [...prev];
            updated[updated.length - 1] = { ...last, isStreaming: false };
            return updated;
          }
          return prev;
        });
        return;
      }

      if (data === '__TOOL_CALLS_START__') {
        pendingToolsRef.current = [];
        setIsStreaming(false);
        setMessages(prev => {
          const last = prev[prev.length - 1];
          if (last && last.sender === 'agent' && last.isStreaming) {
            const updated = [...prev];
            updated[updated.length - 1] = { ...last, isStreaming: false };
            return updated;
          }
          return prev;
        });
        return;
      }

      if (data.startsWith('{"tool":')) {
        try {
          pendingToolsRef.current.push(JSON.parse(data) as ToolInfo);
        } catch { /* ignore malformed */ }
        return;
      }

      if (data === '__TOOL_CALLS_END__') {
        const tools = pendingToolsRef.current;
        pendingToolsRef.current = [];
        if (tools.length > 0) {
          const toolList = tools.map(t => `• ${t.name}`).join('\n');
          setMessages(prev => [...prev, { sender: 'system', text: `🔧 Running tools:\n${toolList}`, tools }]);
        }
        return;
      }

      if (data.startsWith('__ERROR__:') || data.startsWith('__QUOTA_EXCEEDED__')) {
        setIsStreaming(false);
        setMessages(prev => [...prev, { sender: 'system', text: data.replace(/^__\w+__:\s*/, '') }]);
        return;
      }

      if (data === '__MFA_REQUIRED__') {
        setMessages(prev => [...prev, { sender: 'system', text: 'This Web UI requires an MFA code. Send it as: __MFA__:123456' }]);
        return;
      }

      setMessages(prev => {
        const last = prev[prev.length - 1];
        if (last && last.sender === 'agent' && last.isStreaming) {
          const updated = [...prev];
          updated[updated.length - 1] = { ...last, text: last.text + data };
          return updated;
        }
        return [...prev, { sender: 'agent', text: data }];
      });
    };

    ws.onclose = () => {
      setConnectionStatus('closed');
      setIsStreaming(false);
      pendingToolsRef.current = [];
      if (heartbeatRef.current) {
        clearInterval(heartbeatRef.current);
        heartbeatRef.current = null;
      }

      if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        const delay = Math.min(RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts.current), 30000);
        reconnectAttempts.current += 1;
        reconnectTimeoutRef.current = setTimeout(() => connectRef.current(), delay);
      } else {
        setGaveUp(true);
      }
    };

    ws.onerror = () => {
      setConnectionStatus('closed');
      setIsStreaming(false);
    };
  }, [activeAgent]);

  useEffect(() => { connectRef.current = connectWebSocket; }, [connectWebSocket]);

  useEffect(() => {
    teardownSocket();
    reconnectAttempts.current = 0;
    setGaveUp(false);
    connectWebSocket();
    return () => teardownSocket();
  }, [activeAgent, connectWebSocket, teardownSocket]);

  const reconnectNow = () => {
    teardownSocket();
    reconnectAttempts.current = 0;
    setGaveUp(false);
    connectWebSocket();
  };

  // ── Auto-scroll chat ───────────────────────────────────────────────────────
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [messages, view]);

  const sendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !wsRef.current) return;
    if (wsRef.current.readyState !== WebSocket.OPEN) {
      setMessages(prev => [...prev, { sender: 'system', text: 'Not connected — message not sent.' }]);
      return;
    }
    setMessages(prev => [...prev, { sender: 'user', text: input }]);
    wsRef.current.send(input);
    setInput('');
  };

  const clearHistory = async () => {
    if (!confirmClear) {
      setConfirmClear(true);
      setTimeout(() => setConfirmClear(false), 4000);
      return;
    }
    setConfirmClear(false);
    let note = `History cleared for agent [${activeAgent}]`;
    try {
      // Also wipe the server-side memory the agents use as context.
      await api('/api/history', { method: 'DELETE' });
    } catch (e) {
      note += ` (only in this browser: ${(e as Error).message})`;
    }
    try { agents.forEach(a => localStorage.removeItem(`${STORAGE_KEY}_${a.name}`)); } catch { /* ignore */ }
    setMessages([{ sender: 'system', text: note }]);
  };

  const onSettingsSaved = () => {
    loadAgents();
    loadHealth();
  };

  const current = agents.find(a => a.name === activeAgent);
  const llm = health?.llm;

  const NavButton = ({ id, icon, label }: { id: View; icon: string; label: string }) => (
    <button
      className={`nav-btn ${view === id ? 'active' : ''}`}
      onClick={() => { setView(id); setSidebarOpen(false); }}
      aria-current={view === id ? 'page' : undefined}
    >
      <span className="nav-icon" aria-hidden>{icon}</span>{label}
    </button>
  );

  return (
    <div className="app-container">
      <button className="mobile-sidebar-toggle" onClick={() => setSidebarOpen(prev => !prev)} aria-label="Toggle sidebar">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="3" y1="6" x2="21" y2="6"></line>
          <line x1="3" y1="12" x2="21" y2="12"></line>
          <line x1="3" y1="18" x2="21" y2="18"></line>
        </svg>
      </button>

      <div className={`sidebar glass-panel ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-section">
          <div className="sidebar-title">
            <span>Swarm Directory</span>
            <div className="agent-status">
              <span className={`status-dot ${connectionStatus}`}></span>
              {connectionStatus === 'open' ? 'Online' : connectionStatus === 'connecting' ? 'Connecting…' : 'Offline'}
            </div>
          </div>
          <div className="agent-list">
            {agents.map((agent) => (
              <div
                key={agent.name}
                className={`agent-item ${activeAgent === agent.name ? 'active' : ''}`}
                onClick={() => { setActiveAgent(agent.name); setView('chat'); setSidebarOpen(false); }}
              >
                <div className="agent-avatar">{agent.name.charAt(0).toUpperCase()}</div>
                <div className="agent-info">
                  <span className="agent-name">{agent.name}</span>
                  <span className="agent-status">{agent.provider ? `${agent.provider} · ` : ''}{agent.model}</span>
                </div>
              </div>
            ))}
          </div>
          <button className="link-btn" onClick={() => { setView('settings'); setSidebarOpen(false); }}>+ Add or edit agents</button>
        </div>

        <div className="sidebar-section bordered">
          <div className="sidebar-title">Navigate</div>
          <nav className="nav-list">
            <NavButton id="chat" icon="💬" label="Chat" />
            <NavButton id="settings" icon="⚙️" label="Settings" />
            <NavButton id="costs" icon="📊" label="Costs & usage" />
            <NavButton id="tools" icon="🧰" label="Tools" />
            <NavButton id="doctor" icon="🩺" label="Doctor" />
          </nav>
        </div>

        <div className="sidebar-section bordered">
          <div className="sidebar-title">
            <span>System Health</span>
            <button className="icon-btn" onClick={loadHealth} title="Refresh" aria-label="Refresh health">↻</button>
          </div>
          {healthError && <div className="health-row"><span className="health-value err">Backend unreachable</span></div>}
          {health && (
            <div className="health-list">
              <HealthRow
                label="LLM"
                value={llm?.ok ? `${llm.provider} · ${llm.latency_ms} ms` : `${llm?.provider}: offline`}
                state={llm?.ok ? (llm.model_available === false ? 'warn' : 'ok') : 'err'}
              />
              {llm?.ok && llm.model_available === false && (
                <div className="health-note">Model “{llm.model}” not found on the server — pick one in Settings.</div>
              )}
              {!llm?.ok && llm?.error && <div className="health-note">{llm.error}</div>}
              <HealthRow
                label="Knowledge DB"
                value={health.knowledge.ok ? `${health.knowledge.entities ?? 0} notes` : 'error'}
                state={health.knowledge.ok ? 'ok' : 'err'}
              />
              <HealthRow
                label="MCP client"
                value={health.mcp.enabled ? `${health.mcp.servers} server${health.mcp.servers === 1 ? '' : 's'}` : 'disabled'}
                state={health.mcp.enabled ? 'ok' : 'off'}
              />
              <HealthRow label="Swarms" value={health.swarm.enabled ? 'enabled' : 'disabled'} state={health.swarm.enabled ? 'ok' : 'off'} />
              <HealthRow label="Tools" value={`${health.tools}`} />
              <HealthRow
                label="Channels"
                value={[health.channels.telegram && 'Telegram', health.channels.whatsapp && 'WhatsApp'].filter(Boolean).join(', ') || 'none'}
                state={health.channels.telegram || health.channels.whatsapp ? 'ok' : 'off'}
              />
              <button className="link-btn" onClick={() => { setView('doctor'); setSidebarOpen(false); }}>Full diagnostics →</button>
              {!health.config_file_exists && (
                <div className="health-note">Not configured yet — open Settings to pick a model.</div>
              )}
            </div>
          )}
        </div>

        <div className="sidebar-section sidebar-controls bordered">
          <button className="control-btn" onClick={toggleTheme}>
            {theme === 'dark' ? '☀️ Light Mode' : '🌙 Dark Mode'}
          </button>
          <button className={`control-btn danger ${confirmClear ? 'confirm' : ''}`} onClick={clearHistory}>
            {confirmClear ? '⚠️ Click again to clear all history' : '🗑️ Clear History'}
          </button>
          {health?.version && <div className="app-version">ZenSynora v{health.version}</div>}
        </div>
      </div>

      <div className="main-content">
        <div className="glass-panel main-panel">
          <div className="main-header">
            <h2 className="text-gradient">ZenSynora</h2>
            <span className="view-title">{VIEW_TITLES[view]}</span>
            {view === 'chat' && (
              <>
                <span className="badge">Agent: {activeAgent}</span>
                {current && <span className="badge">Model: {current.model}</span>}
                <span className={`badge status-badge ${connectionStatus}`}>
                  {connectionStatus === 'open' ? '🟢 Connected' : connectionStatus === 'connecting' ? '🟡 Connecting…' : '🔴 Disconnected'}
                </span>
              </>
            )}
            <div className="header-actions">
              {view !== 'chat' && <button className="ghost-btn" onClick={() => setView('chat')}>← Back to chat</button>}
              {view === 'chat' && (
                <button className="ghost-btn" onClick={() => setView('settings')} title="Settings">⚙️ Settings</button>
              )}
            </div>
          </div>

          {view === 'chat' && (
            <>
              {gaveUp && (
                <div className="connection-banner">
                  <span>Lost connection to the ZenSynora server. Is <code>zensynora webui</code> still running?</span>
                  <button className="primary-btn" onClick={reconnectNow}>Reconnect</button>
                </div>
              )}

              <div className="chat-window" ref={scrollRef}>
                {messages.map((msg, i) => (
                  <div key={i} className={`chat-message ${msg.sender}`}>
                    <div className="message-avatar">
                      {msg.sender === 'user' ? 'U' : msg.sender === 'system' ? '⚙️' : '🤖'}
                    </div>
                    <div className="message-content">
                      {msg.sender === 'agent' ? (
                        <TypewriterText text={msg.text} active={!!msg.isStreaming} />
                      ) : (
                        msg.text
                      )}
                      {msg.isStreaming && (
                        <span className="typing-indicator">
                          <span className="dot"></span>
                          <span className="dot"></span>
                          <span className="dot"></span>
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              <div className="chat-input-wrapper">
                <form onSubmit={sendMessage} className="chat-form">
                  <input
                    type="text"
                    className="chat-input"
                    placeholder={
                      connectionStatus !== 'open' ? 'Waiting for connection…'
                        : isStreaming ? 'Agent is thinking…' : `Message ${activeAgent}…`
                    }
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    disabled={isStreaming || connectionStatus !== 'open'}
                  />
                  <button
                    type="submit"
                    className="chat-submit"
                    aria-label="Send"
                    disabled={isStreaming || connectionStatus !== 'open' || !input.trim()}
                  >
                    <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <line x1="22" y1="2" x2="11" y2="13"></line>
                      <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                    </svg>
                  </button>
                </form>
              </div>
            </>
          )}

          {view === 'settings' && <SettingsView onSaved={onSettingsSaved} />}
          {view === 'tools' && <ToolsView />}
          {view === 'doctor' && <DoctorView />}
          {view === 'costs' && (
            <div className="settings-view">
              <div className="settings-body">
                <CostDashboard apiBase={getApiBase()} apiKey={getAdminKey() || undefined} />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default App;
