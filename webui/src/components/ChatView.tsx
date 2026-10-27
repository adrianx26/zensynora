/* eslint-disable @typescript-eslint/no-explicit-any */
// Multi-agent chat for one channel (v0.7.1).
//
// * History comes from GET /api/channels/{id}/messages; everything live
//   (your messages from other tabs, streamed answers, tool calls, delegations
//   between agents, job runs) arrives over the /ws/channels/{id} socket.
// * Several agents can answer one message (reply mode or @mentions); each
//   answer streams into its own bubble.
// * role "event" messages are agent-to-agent activity. They are shown inline
//   as compact cards and, filtered, in the Activity panel on the right.
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import TypewriterText from './TypewriterText';
import { MessageActions, MessageBody, MessageEditor } from './MessageActions';
import { copyText } from '../clipboard';
import { api, getApiBase, getWsBase, type AgentInfo, type Channel, type ChatMsg } from '../api';
import { agentColor } from '../colors';
import { LevelBadge } from './LevelBadge';
import { ACTIVITY_MODES, groupMessages, loadActivityMode, saveActivityMode, summarizeEvents, type ActivityMode } from '../chatEvents';

interface Props {
  channelId: string;
  agents: AgentInfo[];
  onChannelChanged: () => void;
  onOpenSettings: () => void;
  onAgentsChanged?: () => void;
}

const REPLY_MODES: { id: Channel['reply_mode']; label: string; help: string }[] = [
  { id: 'lead', label: 'Lead answers', help: 'The first member answers; others only when @mentioned.' },
  { id: 'all', label: 'Everyone, in turn', help: 'Each member answers and sees the earlier answers.' },
  { id: 'parallel', label: 'Everyone, at once', help: 'All members answer at the same time.' },
];


function Avatar({ name }: { name: string }) {
  return (
    <div className="message-avatar" style={{ background: agentColor(name), color: '#0d1117' }} title={`@${name}`}>
      {name.charAt(0).toUpperCase()}
    </div>
  );
}

/** One line describing an agent-to-agent event. */
function eventSummary(m: Record<string, any>): { icon: string; title: string; body?: string; ok?: boolean } {
  switch (m.type) {
    case 'delegation_start':
      return { icon: '↪', title: `@${m.from} asked @${m.to}${m.via === 'swarm' ? ' (swarm)' : ''}`, body: m.task };
    case 'delegation_end':
      return { icon: m.ok === false ? '✘' : '↩', title: `@${m.to} answered @${m.from}${m.ms ? ` · ${(m.ms / 1000).toFixed(1)} s` : ''}`, body: m.result, ok: m.ok !== false };
    case 'tool_end':
      return { icon: m.ok ? '🔧' : '⚠️', title: `@${m.agent} used ${m.tool}${m.ms != null ? ` · ${m.ms} ms` : ''}`, body: m.result, ok: !!m.ok };
    case 'agent_created':
      return { icon: '✨', title: `@${m.from} created a new agent @${m.to}`, body: m.task, ok: true };
    case 'job_run':
      return {
        icon: '⏱', title: `Job “${m.job}” ${m.status === 'started' ? 'started' : m.status}`,
        body: m.status === 'started' ? m.task : m.result, ok: m.status === 'ok' || m.status === 'started',
      };
    default:
      return { icon: '•', title: m.type };
  }
}

function EventCard({ meta, compact }: { meta: Record<string, any>; compact?: boolean }) {
  const [open, setOpen] = useState(false);
  const s = eventSummary(meta);
  return (
    <div className={`event-card ${s.ok === false ? 'bad' : ''} ${compact ? 'compact' : ''}`}>
      <button className="event-head" onClick={() => setOpen(o => !o)} disabled={!s.body}>
        <span className="event-icon">{s.icon}</span>
        <span className="event-title">{s.title}</span>
        {s.body && <span className="event-toggle">{open ? '▾' : '▸'}</span>}
      </button>
      {open && s.body && <pre className="event-body">{s.body}</pre>}
    </div>
  );
}

/** A run of consecutive agent steps as one line; click to see each step (2026-10-27). */
function EventGroup({ events }: { events: ChatMsg[] }) {
  const [open, setOpen] = useState(false);
  const s = summarizeEvents(events);
  const secs = s.ms >= 1000 ? ` · ${(s.ms / 1000).toFixed(1)} s` : '';
  return (
    <div className={`event-card event-group ${s.failed ? 'bad' : ''}`}>
      <button className="event-head" onClick={() => setOpen(o => !o)} aria-expanded={open}
        title={open ? 'Hide the steps' : 'Show each step'}>
        <span className="event-icon">⚙</span>
        <span className="event-title">
          {s.steps} steps{s.failed ? ` · ${s.failed} failed` : ''}{secs} — {s.text}
        </span>
        <span className="event-toggle">{open ? '▾' : '▸'}</span>
      </button>
      {open && <div className="event-group-list">{events.map(e => <EventCard key={e.id} meta={e.meta} compact />)}</div>}
    </div>
  );
}

const ChatView: React.FC<Props> = ({ channelId, agents, onChannelChanged, onOpenSettings, onAgentsChanged }) => {
  const [channel, setChannel] = useState<Channel | null>(null);
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [live, setLive] = useState<Record<string, any>[]>([]); // transient tool_start etc.
  const [input, setInput] = useState('');
  const [status, setStatus] = useState<'connecting' | 'open' | 'closed'>('connecting');
  const [busy, setBusy] = useState(false);
  const [working, setWorking] = useState<string[]>([]);
  const [showActivity, setShowActivity] = useState(true);
  const [activityMode, setActivityMode] = useState<ActivityMode>(loadActivityMode);
  const [editMembers, setEditMembers] = useState(false);
  const [mfa, setMfa] = useState<{ required: boolean; code: string; error?: string }>({ required: false, code: '' });
  const [error, setError] = useState<string | null>(null);
  const [mention, setMention] = useState<{ query: string; index: number } | null>(null);
  const [editing, setEditing] = useState<{ id: number } | null>(null);
  const [askDelete, setAskDelete] = useState<number | null>(null); // your message: with or without answers
  const [exportMenu, setExportMenu] = useState(false);
  const [exportNote, setExportNote] = useState('');

  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const retry = useRef(0);
  const stickToBottom = useRef(true);

  // ── load history ──────────────────────────────────────────────────────────
  const loadHistory = useCallback(async () => {
    try {
      const res = await api<{ channel: Channel; messages: ChatMsg[] }>(`/api/channels/${encodeURIComponent(channelId)}/messages?limit=200`);
      setChannel(res.channel);
      setMessages(res.messages);
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [channelId]);

  // ── socket ────────────────────────────────────────────────────────────────
  useEffect(() => {
    let closed = false;
    let ws: WebSocket;
    let ping: ReturnType<typeof setInterval>;
    let timer: ReturnType<typeof setTimeout>;
    setMessages([]);
    setLive([]);
    setWorking([]);
    loadHistory();

    const connect = () => {
      setStatus('connecting');
      ws = new WebSocket(`${getWsBase()}/ws/channels/${encodeURIComponent(channelId)}`);
      wsRef.current = ws;
      ws.onopen = () => {
        retry.current = 0;
        setStatus('open');
        ping = setInterval(() => ws.readyState === WebSocket.OPEN && ws.send('__ping__'), 30000);
      };
      ws.onmessage = ev => {
        let e: any;
        try { e = JSON.parse(ev.data); } catch { return; }
        handleEvent(e);
      };
      ws.onclose = () => {
        clearInterval(ping);
        setStatus('closed');
        if (!closed) {
          const delay = Math.min(1000 * 2 ** retry.current, 15000);
          retry.current += 1;
          timer = setTimeout(() => { if (!closed) { loadHistory(); connect(); } }, delay);
        }
      };
    };

    const handleEvent = (e: any) => {
      switch (e.type) {
        case 'hello':
          setChannel(e.channel);
          setBusy(!!e.busy);
          setMfa(m => ({ ...m, required: !!e.mfa_required }));
          break;
        case 'mfa_ok':
          setMfa({ required: false, code: '' });
          break;
        case 'channel':
          setChannel(e.channel);
          onChannelChanged();
          break;
        case 'message':
          setMessages(prev => (prev.some(m => m.id === e.message.id) ? prev : [...prev, e.message]));
          break;
        case 'stream_start': {
          const started = { ...e.message, meta: { ...e.message.meta, streaming: true } };
          // Regenerate writes the new answer into the old bubble (same id).
          setMessages(prev => (prev.some(m => m.id === started.id)
            ? prev.map(m => (m.id === started.id ? started : m)) : [...prev, started]));
          setWorking(w => Array.from(new Set([...w, e.message.sender])));
          break;
        }
        case 'message_updated':
          setMessages(prev => prev.map(m => (m.id === e.message.id ? e.message : m)));
          break;
        case 'messages_deleted': {
          const gone = new Set<number>(e.ids || []);
          setMessages(prev => prev.filter(m => !gone.has(m.id)));
          setEditing(ed => (ed && gone.has(ed.id) ? null : ed));
          break;
        }
        case 'cleared':
          setMessages([]);
          break;
        case 'chunk':
          setMessages(prev => prev.map(m => (m.id === e.id ? { ...m, text: m.text + e.text } : m)));
          break;
        case 'stream_end':
          setMessages(prev => prev.map(m => (m.id === e.id ? { ...m, text: e.text, meta: { ...e.meta, streaming: false } } : m)));
          setMessages(prev => {
            const done = prev.find(m => m.id === e.id);
            if (done) setWorking(w => w.filter(n => n !== done.sender));
            return prev;
          });
          break;
        case 'turn_start':
          setBusy(true);
          setWorking(e.agents || []);
          break;
        case 'turn_end':
          setBusy(false);
          setWorking([]);
          setLive([]);
          break;
        case 'tool_start':
          setLive(prev => [...prev.slice(-20), e]);
          break;
        case 'delegation_start':
        case 'delegation_end':
        case 'tool_end':
        case 'job_run':
        case 'agent_created':
          if (e.type === 'agent_created') onAgentsChanged?.(); // v0.7.5: show the new agent in the sidebar
          if (e.type === 'tool_end') setLive(prev => prev.filter(x => !(x.type === 'tool_start' && x.tool === e.tool && x.agent === e.agent)));
          if (e.message_id) {
            const meta = { ...e };
            delete meta.channel;
            setMessages(prev => (prev.some(m => m.id === e.message_id) ? prev : [...prev, {
              id: e.message_id, channel_id: channelId, role: 'event', sender: e.from || e.agent || '', text: '', meta, created: e.ts,
            }]));
          }
          break;
        case 'error':
          if (/two-factor/i.test(e.error || '')) setMfa(m => ({ ...m, required: true, error: e.error }));
          else setError(e.error);
          break;
        default:
          break;
      }
    };

    connect();
    return () => {
      closed = true;
      clearInterval(ping);
      clearTimeout(timer);
      if (ws) { ws.onclose = null; ws.close(); }
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelId]);

  // ── scrolling ─────────────────────────────────────────────────────────────
  useEffect(() => {
    const el = scrollRef.current;
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight;
  }, [messages, live]);

  const onScroll = () => {
    const el = scrollRef.current;
    if (el) stickToBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
  };

  // ── sending ───────────────────────────────────────────────────────────────
  const send = (e?: React.FormEvent) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text || wsRef.current?.readyState !== WebSocket.OPEN) return;
    wsRef.current.send(JSON.stringify({ type: 'message', text }));
    setInput('');
    setMention(null);
    stickToBottom.current = true;
  };

  const stop = () => wsRef.current?.send(JSON.stringify({ type: 'stop' }));

  const sendMfa = (e: React.FormEvent) => {
    e.preventDefault();
    wsRef.current?.send(JSON.stringify({ type: 'mfa', code: mfa.code.trim() }));
  };

  // ── @mention autocomplete ─────────────────────────────────────────────────
  const mentionOptions = useMemo(() => {
    if (!mention) return [];
    const q = mention.query.toLowerCase();
    return [...agents.map(a => a.name), 'all'].filter(n => n.toLowerCase().startsWith(q)).slice(0, 8);
  }, [mention, agents]);

  const onInput = (value: string, caret: number) => {
    setInput(value);
    const m = /(^|\s)@([\w.-]*)$/.exec(value.slice(0, caret));
    setMention(m ? { query: m[2], index: 0 } : null);
  };

  const pickMention = (name: string) => {
    const el = inputRef.current;
    const caret = el?.selectionStart ?? input.length;
    const before = input.slice(0, caret).replace(/@([\w.-]*)$/, `@${name} `);
    const next = before + input.slice(caret);
    setInput(next);
    setMention(null);
    setTimeout(() => { el?.focus(); el?.setSelectionRange(before.length, before.length); }, 0);
  };

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (mention && mentionOptions.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault();
        const d = e.key === 'ArrowDown' ? 1 : -1;
        setMention(m => m && { ...m, index: (m.index + d + mentionOptions.length) % mentionOptions.length });
        return;
      }
      if (e.key === 'Tab' || e.key === 'Enter') {
        e.preventDefault();
        pickMention(mentionOptions[mention.index] || mentionOptions[0]);
        return;
      }
      if (e.key === 'Escape') { setMention(null); return; }
    }
    if (e.key === 'ArrowUp' && !input && !busy) { // edit your last message
      const mine = [...messages].reverse().find(m => m.role === 'user');
      if (mine) { e.preventDefault(); setEditing({ id: mine.id }); }
      return;
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  // ── message actions: edit, delete, regenerate, export ───────────────────
  const msgUrl = (id: number) => `/api/channels/${encodeURIComponent(channelId)}/messages/${id}`;

  const saveEdit = async (m: ChatMsg, text: string) => {
    try {
      await api(msgUrl(m.id), { method: 'PATCH', body: JSON.stringify({ text, resend: m.role === 'user' }) });
      setEditing(null);
      if (m.role === 'user') stickToBottom.current = true;
      setTimeout(() => inputRef.current?.focus(), 0);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const removeMessage = async (m: ChatMsg, withReplies = false) => {
    setAskDelete(null);
    if (m.role !== 'user' && !window.confirm('Delete this message?')) return;
    try {
      const res = await api<{ deleted: number[] }>(`${msgUrl(m.id)}?with_replies=${withReplies}`, { method: 'DELETE' });
      const gone = new Set(res.deleted);
      setMessages(prev => prev.filter(x => !gone.has(x.id)));
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const regenerate = async (m: ChatMsg) => {
    try {
      await api(`${msgUrl(m.id)}/regenerate`, { method: 'POST' });
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const exportMarkdown = async (how: 'copy' | 'download') => {
    setExportMenu(false);
    try {
      const res = await fetch(`${getApiBase()}/api/channels/${encodeURIComponent(channelId)}/export.md`);
      if (!res.ok) throw new Error(`Export failed (HTTP ${res.status})`);
      const md = await res.text();
      if (how === 'copy') {
        setExportNote((await copyText(md)) ? 'Conversation copied as Markdown' : 'Could not copy — use Download');
        setTimeout(() => setExportNote(''), 2500);
      } else {
        const url = URL.createObjectURL(new Blob([md], { type: 'text/markdown' }));
        const a = document.createElement('a');
        a.href = url;
        a.download = `${channel?.kind === 'dm' ? channel.members[0] : channel?.name || channelId}.md`;
        a.click();
        URL.revokeObjectURL(url);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const BUSY = 'Wait until the agents have finished (or press ■)';

  // ── channel settings ──────────────────────────────────────────────────────
  const patchChannel = async (patch: Partial<Channel>) => {
    try {
      const ch = await api<Channel>(`/api/channels/${encodeURIComponent(channelId)}`, { method: 'PATCH', body: JSON.stringify(patch) });
      setChannel(ch);
      onChannelChanged();
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const toggleMember = (name: string) => {
    if (!channel) return;
    // v0.7.5: only send agents that exist — a deleted agent left in the list made every change fail
    const known = new Set(agents.map(a => a.name));
    const current = channel.members.filter(m => known.has(m));
    const members = current.includes(name) ? current.filter(m => m !== name) : [...current, name];
    if (members.length) patchChannel({ members });
  };

  const clearChannel = async () => {
    if (!window.confirm('Delete all messages in this channel? The agents also forget this conversation.')) return;
    try {
      await api(`/api/channels/${encodeURIComponent(channelId)}/messages`, { method: 'DELETE' });
      setMessages([]);
    } catch (e) {
      setError((e as Error).message);
    }
  };

  const events = messages.filter(m => m.role === 'event');
  const items = useMemo(() => groupMessages(messages, activityMode), [messages, activityMode]);
  const isDm = channel?.kind === 'dm';

  return (
    <div className="chat-layout">
      <div className="chat-main">
        <div className="channel-bar">
          <div className="channel-title">
            <span className="channel-hash">{isDm ? '@' : '#'}</span>
            <span>{channel ? (isDm ? channel.members[0] : channel.name) : channelId}</span>
            <span className={`status-dot ${status}`} title={status === 'open' ? 'Connected' : status === 'connecting' ? 'Connecting…' : 'Disconnected — retrying'} />
          </div>
          {channel && !isDm && (
            <div className="channel-members">
              {channel.members.map(m => (
                <span key={m} className="member-chip" style={{ borderColor: agentColor(m) }}>
                  <span className="member-dot" style={{ background: agentColor(m) }} />@{m}
                  <LevelBadge level={agents.find(a => a.name === m)?.level} />
                  {working.includes(m) && <span className="member-working" title="answering…">…</span>}
                </span>
              ))}
              <button className="icon-btn" onClick={() => setEditMembers(v => !v)} title="Members and reply mode">⚙</button>
            </div>
          )}
          <div className="channel-actions">
            <select className="mini-select" value={activityMode} aria-label="How agent activity is shown"
              title={ACTIVITY_MODES.find(a => a.id === activityMode)?.help}
              onChange={e => { const v = e.target.value as ActivityMode; setActivityMode(v); saveActivityMode(v); }}>
              {ACTIVITY_MODES.map(a => <option key={a.id} value={a.id} title={a.help}>{a.label}</option>)}
            </select>
            <button className={`ghost-btn ${showActivity ? 'active' : ''}`} onClick={() => setShowActivity(v => !v)}>
              Activity{events.length ? ` (${events.length})` : ''}
            </button>
            <div className="export-wrap">
              <button className="ghost-btn" onClick={() => setExportMenu(v => !v)} aria-expanded={exportMenu}
                title="Copy or download this conversation as Markdown">⤓ MD</button>
              {exportMenu && (
                <div className="export-menu" role="menu">
                  <button role="menuitem" onClick={() => exportMarkdown('copy')}>Copy as Markdown</button>
                  <button role="menuitem" onClick={() => exportMarkdown('download')}>Download .md</button>
                </div>
              )}
            </div>
            <button className="ghost-btn" onClick={clearChannel} title="Clear this channel">🗑</button>
          </div>
          {exportNote && <span className="muted small" role="status">{exportNote}</span>}
        </div>

        {editMembers && channel && !isDm && (
          <div className="channel-editor">
            <div className="field">
              <span className="field-label">Members (click to add or remove)</span>
              <div className="chip-row">
                {agents.map(a => (
                  <button key={a.name} className={`chip ${channel.members.includes(a.name) ? 'active' : ''}`} onClick={() => toggleMember(a.name)}>
                    @{a.name}
                  </button>
                ))}
                <button className="chip" onClick={onOpenSettings}>+ new agent…</button>
              </div>
            </div>
            <div className="field">
              <span className="field-label">Who answers a message without @mentions</span>
              <div className="chip-row">
                {REPLY_MODES.map(r => (
                  <button key={r.id} title={r.help} className={`chip ${channel.reply_mode === r.id ? 'active' : ''}`} onClick={() => patchChannel({ reply_mode: r.id })}>
                    {r.label}
                  </button>
                ))}
              </div>
              <div className="field-help">{REPLY_MODES.find(r => r.id === channel.reply_mode)?.help} Use <code>@name</code> to pick agents, <code>@all</code> for everyone.</div>
            </div>
          </div>
        )}

        {error && <div className="notice error">{error} <button className="ghost-btn" onClick={() => setError(null)}>×</button></div>}
        {status === 'closed' && <div className="connection-banner"><span>Disconnected — reconnecting… Is <code>zensynora webui</code> running?</span></div>}

        <div className="chat-window" ref={scrollRef} onScroll={onScroll}>
          {items.length === 0 && (
            <div className="empty-chat">
              <h3>{isDm ? `Chat with @${channel?.members[0] ?? ''}` : `#${channel?.name ?? ''}`}</h3>
              <p className="muted">
                {isDm ? 'A private conversation with one agent.' : <>Members: {channel?.members.map(m => `@${m}`).join(', ')}. Mention <code>@name</code> to bring in another agent; delegations between agents appear here and in the Activity panel.</>}
              </p>
            </div>
          )}
          {items.map(item => {
            if (item.kind === 'events') {
              return (
                <div key={item.key} className="event-row">
                  {item.events.length === 1 ? <EventCard meta={item.events[0].meta} /> : <EventGroup events={item.events} />}
                </div>
              );
            }
            const m = item.msg;
            if (m.role === 'system') {
              return <div key={m.id} className="system-row">{m.text}</div>;
            }
            const mine = m.role === 'user';
            const streaming = !!m.meta?.streaming;
            const isEditing = editing?.id === m.id;
            const edited = m.meta?.edited_at ? <span className="muted edited" title={`Edited ${new Date(m.meta.edited_at * 1000).toLocaleString()}`}> (edited)</span> : null;
            return (
              <div key={m.id} className={`chat-message ${mine ? 'user' : 'agent'}`}>
                {mine ? <div className="message-avatar">U</div> : <Avatar name={m.sender} />}
                <div className={`message-content${m.meta?.question ? ' question-msg' : ''}`}>
                  {!mine && (
                    <div className="message-meta">
                      <b style={{ color: agentColor(m.sender) }}>@{m.sender}</b>
                      {agents.some(a => a.name === m.sender) && <LevelBadge level={agents.find(a => a.name === m.sender)?.level} />}
                      {m.meta?.question && <span className="badge question-badge" title="The agent asks you for information (request_information); your reply goes to it">question</span>}
                      {m.meta?.job && <span className="badge">job: {m.meta.job}</span>}
                      {m.meta?.model && !streaming && <span className="muted"> · {m.meta.provider}/{m.meta.model}{m.meta.ms ? ` · ${(m.meta.ms / 1000).toFixed(1)} s` : ''}</span>}
                      {!streaming && m.meta?.tps != null && (
                        <span className="muted token-speed" title={`≈${m.meta.tokens} tokens · first token after ${((m.meta.ttft_ms || 0) / 1000).toFixed(1)} s (token counts are estimated)`}>
                          {' '}· ⚡ {m.meta.tps} tok/s</span>
                      )}
                    </div>
                  )}
                  {isEditing ? (
                    <MessageEditor initial={m.text} resend={mine} onSave={t => saveEdit(m, t)}
                      onCancel={() => { setEditing(null); inputRef.current?.focus(); }} />
                  ) : streaming ? <TypewriterText text={m.text} active /> : <MessageBody text={m.text} />}
                  {!isEditing && edited}
                  {streaming && !m.text && <span className="typing-indicator"><span className="dot" /><span className="dot" /><span className="dot" /></span>}
                  {m.meta?.error && <div className="probe-err">{m.meta.error}</div>}
                  {askDelete === m.id && (
                    <div className="msg-confirm" role="alertdialog" aria-label="Delete message">
                      <button className="ghost-btn danger" onClick={() => removeMessage(m)}>Delete this message</button>
                      <button className="ghost-btn danger" onClick={() => removeMessage(m, true)}>Delete this message and the answers to it</button>
                      <button className="ghost-btn" onClick={() => setAskDelete(null)}>Cancel</button>
                    </div>
                  )}
                  {!streaming && !isEditing && (
                    <MessageActions
                      text={m.text}
                      created={m.created}
                      canEdit
                      canRegenerate={!mine && !m.meta?.job}
                      disabledReason={busy ? BUSY : undefined}
                      onEdit={() => { setAskDelete(null); setEditing({ id: m.id }); }}
                      onDelete={() => (mine ? setAskDelete(m.id) : removeMessage(m))}
                      onRegenerate={() => regenerate(m)}
                    />
                  )}
                </div>
              </div>
            );
          })}
          {live.filter(l => l.type === 'tool_start').map((l, i) => (
            <div key={`live-${i}`} className="event-row"><div className="event-card compact live"><span className="event-icon">⏳</span> @{l.agent} is running {l.tool}…</div></div>
          ))}
        </div>

        {mfa.required ? (
          <form className="chat-form mfa-form" onSubmit={sendMfa}>
            <span>Two-factor code:</span>
            <input className="field-input narrow" value={mfa.code} onChange={e => setMfa(m => ({ ...m, code: e.target.value }))} placeholder="123456" autoFocus />
            <button className="primary-btn" type="submit">Unlock chat</button>
            {mfa.error && <span className="probe-err">{mfa.error}</span>}
          </form>
        ) : (
          <div className="chat-input-wrapper">
            {mention && mentionOptions.length > 0 && (
              <div className="mention-menu">
                {mentionOptions.map((n, i) => (
                  <button key={n} className={i === mention.index ? 'active' : ''} onMouseDown={e => { e.preventDefault(); pickMention(n); }}>
                    <span className="member-dot" style={{ background: n === 'all' ? 'var(--text-secondary)' : agentColor(n) }} />@{n}
                    <span className="muted">{n === 'all' ? 'every member' : agents.find(a => a.name === n)?.description || agents.find(a => a.name === n)?.model}</span>
                  </button>
                ))}
              </div>
            )}
            <form onSubmit={send} className="chat-form">
              <textarea
                ref={inputRef}
                className="chat-input"
                rows={Math.min(6, Math.max(1, input.split('\n').length))}
                placeholder={status !== 'open' ? 'Waiting for connection…' : isDm ? `Message @${channel?.members[0] ?? ''}… (Shift+Enter = new line)` : `Message #${channel?.name ?? ''} — type @ to address an agent`}
                value={input}
                onChange={e => onInput(e.target.value, e.target.selectionStart)}
                onKeyDown={onKeyDown}
                disabled={status !== 'open'}
              />
              {busy ? (
                <button type="button" className="chat-submit stop" onClick={stop} title="Stop the agents">■</button>
              ) : (
                <button type="submit" className="chat-submit" aria-label="Send" disabled={status !== 'open' || !input.trim()}>
                  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                    <line x1="22" y1="2" x2="11" y2="13" /><polygon points="22 2 15 22 11 13 2 9 22 2" />
                  </svg>
                </button>
              )}
            </form>
            {busy && working.length > 0 && <div className="working-line">{working.map(w => `@${w}`).join(', ')} {working.length === 1 ? 'is' : 'are'} answering… (new messages wait their turn)</div>}
          </div>
        )}
      </div>

      {showActivity && (
        <aside className="activity-panel">
          <div className="sidebar-title"><span>Agent activity</span><button className="icon-btn" onClick={() => setShowActivity(false)}>×</button></div>
          <p className="muted small">Delegations, tool calls and job runs in this channel — who asked whom, and what came back.</p>
          {live.filter(l => l.type === 'tool_start').map((l, i) => (
            <div key={`a-live-${i}`} className="event-card compact live"><span className="event-icon">⏳</span> @{l.agent}: {l.tool}</div>
          ))}
          {events.length === 0 && live.length === 0 && <div className="muted small">Nothing yet.</div>}
          {[...events].reverse().slice(0, 100).map(m => <EventCard key={m.id} meta={m.meta} compact />)}
        </aside>
      )}
    </div>
  );
};

export default ChatView;
