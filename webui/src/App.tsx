import React, { useState, useEffect, useRef } from 'react';
import './index.css';

interface Agent {
  name: string;
  model: string;
}

function App() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeAgent, setActiveAgent] = useState<string>('default');
  const [messages, setMessages] = useState<{sender: string, text: string}[]>([
    { sender: 'system', text: 'Connecting to ZenSynora Gateway...' }
  ]);
  const [input, setInput] = useState('');
  
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    // Fetch available agents
    fetch('http://localhost:8000/api/agents')
      .then(res => res.json())
      .then(data => {
        setAgents(data.agents || []);
      })
      .catch(err => console.error("Error fetching agents:", err));
  }, []);

  useEffect(() => {
    if (wsRef.current) wsRef.current.close();
    
    // Connect to WebSocket for the active agent
    const ws = new WebSocket(`ws://localhost:8000/ws/chat/${activeAgent}`);
    ws.onopen = () => {
      setMessages([{ sender: 'system', text: `Connected securely to agent [${activeAgent}]` }]);
    };
    ws.onmessage = (event) => {
      setMessages(prev => [...prev, { sender: 'agent', text: event.data }]);
    };
    wsRef.current = ws;

    return () => ws.close();
  }, [activeAgent]);

  useEffect(() => {
    // Auto-scroll chat
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages]);

  const sendMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!input.trim() || !wsRef.current) return;
    
    setMessages(prev => [...prev, { sender: 'user', text: input }]);
    wsRef.current.send(input);
    setInput('');
  };

  return (
    <div className="app-container">
      {/* Sidebar Overlay */}
      <div className="sidebar glass-panel">
        <div className="sidebar-section">
          <div className="sidebar-title">
            <span>Swarm Directory</span>
            <div className="agent-status" style={{color: "var(--text-secondary)"}}>
              <span className="status-dot"></span> Online
            </div>
          </div>
          
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '16px' }}>
            {agents.map((agent) => (
              <div 
                key={agent.name} 
                className={`agent-item ${activeAgent === agent.name ? 'active' : ''}`}
                onClick={() => setActiveAgent(agent.name)}
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
      </div>

      {/* Main Chat Interface */}
      <div className="main-content">
        <div className="glass-panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', position: 'relative' }}>
          
          <div style={{ padding: '20px 24px', borderBottom: '1px solid var(--panel-border)', display: 'flex', alignItems: 'center', gap: '16px' }}>
            <h2 className="text-gradient">ZenSynora UI</h2>
            <span className="badge">Agent: {activeAgent}</span>
            <span className="badge">Workspace: f:\ANTI\zensynora</span>
          </div>

          <div className="chat-window" ref={scrollRef}>
            {messages.map((msg, i) => (
              <div key={i} className={`chat-message ${msg.sender === 'user' ? 'user' : ''}`}>
                <div className="message-avatar">
                  {msg.sender === 'user' ? 'U' : msg.sender === 'system' ? '⚙️' : '🤖'}
                </div>
                <div className="message-content">
                  {msg.text}
                </div>
              </div>
            ))}
          </div>

          <div className="glass-panel chat-input-wrapper" style={{ margin: '0 24px 24px 24px', width: 'auto' }}>
            <form onSubmit={sendMessage} style={{ display: 'flex', width: '100%', gap: '12px' }}>
              <input 
                type="text" 
                className="chat-input" 
                placeholder="Message the swarm..." 
                value={input}
                onChange={(e) => setInput(e.target.value)}
              />
              <button type="submit" className="chat-submit">
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
