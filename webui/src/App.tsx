import React, { useState, useEffect, useRef, useCallback } from 'react';
import './index.css';
import TypewriterText from './components/TypewriterText';

interface Agent {
  name: string;
  model: string;
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

// ── Configuration: derive API base from window.location ──────────────────────
function getApiBase(): string {
  const { protocol, hostname, port } = window.location;
  const apiPort = port === '5173' || port === '' ? '8000' : port;
  return `${protocol}//${hostname}:${apiPort}`;
}

function getWsBase(): string {
  const { protocol, hostname, port } = window.location;
  const wsProtocol = protocol === 'https:' ? 'wss:' : 'ws:';
  const apiPort = port === '5173' || port === '' ? '8000' : port;
  return `${wsProtocol}//${hostname}:${apiPort}`;
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

function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeAgent, setActiveAgent] = useState<string>('default');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [theme, setTheme] = useState<'dark' | 'light'>(getSavedTheme);
  const [connectionStatus, setConnectionStatus] = useState<'connecting' | 'open' | 'closed'>('connecting');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [isStreaming, setIsStreaming] = useState(false);

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const heartbeatRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const reconnectAttempts = useRef(0);
  const MAX_RECONNECT_ATTEMPTS = 10;
  const RECONNECT_BASE_DELAY = 1000;

  // Track pending tool calls during streaming
  const pendingToolsRef = useRef<ToolInfo[]>([]);

  // Apply theme on mount
  useEffect(() => { setThemeDoc(theme); }, [theme]);

  // Toggle theme
  const toggleTheme = useCallback(() => {
    setTheme(prev => {
      const next = prev === 'dark' ? 'light' : 'dark';
      setThemeDoc(next);
      return next;
    });
  }, []);

  // ── Fetch available agents ─────────────────────────────────────────────────
  useEffect(() => {
    const apiBase = getApiBase();
    fetch(`${apiBase}/api/agents`)
      .then(res => res.json())
      .then(data => {
        setAgents(data.agents || []);
      })
      .catch(err => console.error("Error fetching agents:", err));
  }, []);

  // ── Load persisted messages when agent changes ─────────────────────────────
  useEffect(() => {
    setMessages(loadMessages(activeAgent));
  }, [activeAgent]);

  // ── Persist messages ───────────────────────────────────────────────────────
  useEffect(() => {
    saveMessages(activeAgent, messages);
  }, [activeAgent, messages]);

  // ── WebSocket with reconnection, heartbeat, and streaming ──────────────────
  const connectWebSocket = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const wsBase = getWsBase();
    const wsUrl = `${wsBase}/ws/chat/${activeAgent}`;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    setConnectionStatus('connecting');

    ws.onopen = () => {
      reconnectAttempts.current = 0;
      setConnectionStatus('open');
      setMessages(prev => {
        if (prev.length === 1 && prev[0].sender === 'system' && prev[0].text.includes('Connecting')) {
          return [{ sender: 'system', text: `Connected securely to agent [${activeAgent}]` }];
        }
        return [...prev, { sender: 'system', text: `Reconnected to agent [${activeAgent}]` }];
      });

      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
      heartbeatRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send('__ping__');
        }
      }, 30000);
    };

    ws.onmessage = (event) => {
      const data = event.data as string;

      if (data === '__pong__') return;

      // ── Stream start ──
      if (data === '__STREAM_START__') {
        setIsStreaming(true);
        setMessages(prev => [...prev, { sender: 'agent', text: '', isStreaming: true }]);
        return;
      }

      // ── Stream end ──
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

      // ── Tool calls start ──
      if (data === '__TOOL_CALLS_START__') {
        pendingToolsRef.current = [];
        setIsStreaming(false);
        // Finalize the previous streaming message
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

      // ── Tool call info (JSON) ──
      if (data.startsWith('{"tool":')) {
        try {
          const toolInfo = JSON.parse(data) as ToolInfo;
          pendingToolsRef.current.push(toolInfo);
        } catch { /* ignore malformed */ }
        return;
      }

      // ── Tool calls end ──
      if (data === '__TOOL_CALLS_END__') {
        const tools = pendingToolsRef.current;
        pendingToolsRef.current = [];
        if (tools.length > 0) {
          const toolList = tools.map(t => `• ${t.name}`).join('\n');
          setMessages(prev => [...prev, {
            sender: 'system',
            text: `🔧 Running tools:\n${toolList}`,
            tools
          }]);
        }
        return;
      }

      // ── Error marker ──
      if (data.startsWith('__ERROR__:')) {
        setIsStreaming(false);
        setMessages(prev => [...prev, { sender: 'system', text: data }]);
        return;
      }

      // ── Regular chunk ──
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
        const delay = Math.min(
          RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts.current),
          30000
        );
        reconnectAttempts.current += 1;
        reconnectTimeoutRef.current = setTimeout(() => {
          connectWebSocket();
        }, delay);
      } else {
        setMessages(prev => [...prev, {
          sender: 'system',
          text: 'Connection lost. Max reconnection attempts reached. Please refresh the page.'
        }]);
      }
    };

    ws.onerror = (err) => {
      console.error('WebSocket error:', err);
      setConnectionStatus('closed');
      setIsStreaming(false);
    };
  }, [activeAgent]);

  useEffect(() => {
    if (wsRef.current) {
      wsRef.current.close();
    }
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
    }
    if (heartbeatRef.current) {
      clearInterval(heartbeatRef.current);
    }
    reconnectAttempts.current = 0;

    connectWebSocket();

    return () => {
      if (wsRef.current) wsRef.current.close();
      if (reconnectTimeoutRef.current) clearTimeout(reconnectTimeoutRef.current);
      if (heartbeatRef.current) clearInterval(heartbeatRef.current);
    };
  }, [activeAgent, connectWebSocket]);

  // ── Auto-scroll chat ───────────────────────────────────────────────────────
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !wsRef.current) return;
    if (wsRef.current.readyState !== WebSocket.OPEN) {
      setMessages(prev => [...prev, { sender: 'system', text: 'Not connected. Message queued.' }]);
      return;
    }

    setMessages(prev => [...prev, { sender: 'user', text: input }]);
    wsRef.current.send(input);
    setInput('');
  };

  const clearHistory = () => {
    setMessages([{ sender: 'system', text: `History cleared for agent [${activeAgent}]` }]);
    try { localStorage.removeItem(`${STORAGE_KEY}_${activeAgent}`); } catch { /* ignore */ }
  };

  return (
    <div className="app-container">
      {/* Mobile sidebar toggle */}
      <button
        className="mobile-sidebar-toggle"
        onClick={() => setSidebarOpen(prev => !prev)}
        aria-label="Toggle sidebar"
      >
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <line x1="3" y1="6" x2="21" y2="6"></line>
          <line x1="3" y1="12" x2="21" y2="12"></line>
          <line x1="3" y1="18" x2="21" y2="18"></line>
        </svg>
      </button>

      {/* Sidebar Overlay */}
      <div className={`sidebar glass-panel ${sidebarOpen ? 'open' : ''}`}>
        <div className="sidebar-section">
          <div className="sidebar-title">
            <span>Swarm Directory</span>
            <div className="agent-status" style={{color: "var(--text-secondary)"}}>
              <span className={`status-dot ${connectionStatus}`}></span>
              {connectionStatus === 'open' ? 'Online' : connectionStatus === 'connecting' ? 'Connecting...' : 'Offline'}
            </div>
          </div>

          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '16px' }}>
            {agents.map((agent) => (
              <div
                key={agent.name}
                className={`agent-item ${activeAgent === agent.name ? 'active' : ''}`}
                onClick={() => { setActiveAgent(agent.name); setSidebarOpen(false); }}
              >
                <div className="agent-avatar">
                  {agent.name.charAt(0).toUpperCase()}
                </div>
                <div className="agent-info">
                  <span className="agent-name">{agent.name}</span>
                  <span className="agent-status">{agent.model}</span>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="sidebar-section" style={{ borderTop: "1px solid var(--panel-border)" }}>
          <div className="sidebar-title">System Health</div>
          <div style={{ marginTop: '16px', fontSize: '0.85rem', color: "var(--text-secondary)", lineHeight: "1.8" }}>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Memory Graph DB</span>
              <span style={{ color: "var(--success-color)" }}>Connected</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>MCP Client</span>
              <span>Loaded (1 Servers)</span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <span>Swarm Orchestrator</span>
              <span>Idle</span>
            </div>
          </div>
        </div>

        <div className="sidebar-section sidebar-controls" style={{ borderTop: "1px solid var(--panel-border)" }}>
          <button className="control-btn" onClick={toggleTheme}>
            {theme === 'dark' ? '☀️ Light Mode' : '🌙 Dark Mode'}
          </button>
          <button className="control-btn danger" onClick={clearHistory}>
            🗑️ Clear History
          </button>
        </div>
      </div>

      {/* Main Chat Interface */}
      <div className="main-content">
        <div className="glass-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative' }}>

          <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--panel-border)', display: 'flex', alignItems: 'center', gap: '16px', flexWrap: 'wrap' }}>
            <h2 className="text-gradient">ZenSynora UI</h2>
            <span className="badge">Agent: {activeAgent}</span>
            <span className="badge">Workspace: {window.location.hostname}</span>
            <span className={`badge status-badge ${connectionStatus}`}>
              {connectionStatus === 'open' ? '🟢 Connected' : connectionStatus === 'connecting' ? '🟡 Connecting...' : '🔴 Disconnected'}
            </span>
          </div>

          <div className="chat-window" ref={scrollRef}>
            {messages.map((msg, i) => (
              <div key={i} className={`chat-message ${msg.sender}`}>
                <div className="message-avatar">
                  {msg.sender === 'user' ? 'U' : msg.sender === 'system' ? '⚙️' : '🤖'}
                </div>
                <div className="message-content">
                  {/*
                    Agent responses use the TypewriterText component for
                    smooth char-by-char reveal. User/system messages are
                    rendered verbatim — typewriting them would feel like
                    a UI lag for the operator's own input.
                  */}
                  {msg.sender === 'agent' ? (
                    <TypewriterText
                      text={msg.text}
                      active={!!msg.isStreaming}
                    />
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

          <div className="glass-panel chat-input-wrapper" style={{ margin: '0 24px 24px 24px', width: 'auto' }}>
            <form onSubmit={sendMessage} style={{ display: 'flex', width: '100%', gap: '12px' }}>
              <input
                type="text"
                className="chat-input"
                placeholder={isStreaming ? "Agent is thinking..." : "Message the swarm..."}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                disabled={isStreaming || connectionStatus !== 'open'}
              />
              <button
                type="submit"
                className="chat-submit"
                disabled={isStreaming || connectionStatus !== 'open' || !input.trim()}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="22" y1="2" x2="11" y2="13"></line>
                  <polygon points="22 2 15 22 11 13 2 9 22 2"></polygon>
                </svg>
              </button>
            </form>
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
