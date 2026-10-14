// Channels page (v0.7.3): create and manage chat channels in one place —
// name, topic (shown to the agents as the channel's purpose), members,
// who answers (reply mode), clear history, delete. Direct-message channels
// are listed too (members fixed: one agent).
import React, { useCallback, useEffect, useState } from 'react';
import { ago, api, type AgentInfo, type Channel } from '../api';
import { agentColor } from '../colors';

interface Props {
  agents: AgentInfo[];
  onChanged: () => void;
  onOpen: (id: string) => void;
}

type ChannelRow = Channel & { message_count?: number; last_activity?: number | null; busy?: boolean };

const MODES: { id: Channel['reply_mode']; label: string; help: string }[] = [
  { id: 'lead', label: 'Lead answers', help: 'The first member answers; others only when @mentioned.' },
  { id: 'all', label: 'Everyone, in turn', help: 'Each member answers and sees the earlier answers.' },
  { id: 'parallel', label: 'Everyone, at once', help: 'All members answer at the same time.' },
];

interface Draft { name: string; topic: string; members: string[]; reply_mode: Channel['reply_mode'] }
const EMPTY: Draft = { name: '', topic: '', members: ['default'], reply_mode: 'lead' };

const ChannelsView: React.FC<Props> = ({ agents, onChanged, onOpen }) => {
  const [rows, setRows] = useState<ChannelRow[]>([]);
  const [editing, setEditing] = useState<string | null>(null); // channel id, or "new"
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const draftRaw = draft;
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api<{ channels: ChannelRow[] }>('/api/channels');
      setRows(r.channels);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  }, []);

  // eslint-disable-next-line react-hooks/set-state-in-effect -- initial data load
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<unknown>, ok: string) => {
    setNotice(null);
    try {
      await fn();
      setNotice({ kind: 'ok', text: ok });
      setEditing(null);
      await load();
      onChanged();
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const startEdit = (c: ChannelRow) => {
    setEditing(c.id);
    setDraft({ name: c.name, topic: c.topic || '', members: c.members, reply_mode: c.reply_mode });
  };

  const save = () => {
    const known = new Set(agents.map(a => a.name));
    const draft = { ...draftRaw, members: draftRaw.members.filter(m => known.has(m)) };
    if (editing === 'new') {
      act(async () => {
        const ch = await api<Channel>('/api/channels', { method: 'POST', body: JSON.stringify(draft) });
        onOpen(ch.id);
      }, `Channel #${draft.name} created.`);
    } else if (editing) {
      act(() => api(`/api/channels/${encodeURIComponent(editing)}`, { method: 'PATCH', body: JSON.stringify(draft) }), 'Channel saved.');
    }
  };

  const clear = (c: ChannelRow) => {
    if (!window.confirm(`Delete all messages in ${c.kind === 'dm' ? '@' + c.members[0] : '#' + c.name}? The agents also forget this conversation.`)) return;
    act(() => api(`/api/channels/${encodeURIComponent(c.id)}/messages`, { method: 'DELETE' }), 'History cleared.');
  };

  const remove = (c: ChannelRow) => {
    if (!window.confirm(`Delete ${c.kind === 'dm' ? 'the conversation with @' + c.members[0] : '#' + c.name} and its history?`)) return;
    act(() => api(`/api/channels/${encodeURIComponent(c.id)}`, { method: 'DELETE' }), 'Channel deleted.');
  };

  const toggleMember = (name: string) =>
    setDraft(d => ({ ...d, members: d.members.includes(name) ? d.members.filter(m => m !== name) : [...d.members, name] }));

  const knownNames = new Set(agents.map(a => a.name));
  const missing = draft.members.filter(m => !knownNames.has(m));
  const editor = editing && (
    <div className="agent-editor">
      <div className="agent-editor-grid">
        <div className="field"><label className="field-label">Name</label>
          <input className="field-input" value={draft.name} autoFocus placeholder="e.g. research"
            onChange={e => setDraft({ ...draft, name: e.target.value })} /></div>
      </div>
      <div className="field"><label className="field-label">Topic — what this channel is for (the agents see it)</label>
        <input className="field-input" value={draft.topic} placeholder="e.g. Plan and review the weekly blog post"
          onChange={e => setDraft({ ...draft, topic: e.target.value })} /></div>
      <div className="field"><span className="field-label">Members</span>
        <div className="chip-row">
          {agents.map(a => (
            <button key={a.name} className={`chip ${draft.members.includes(a.name) ? 'active' : ''}`} onClick={() => toggleMember(a.name)}
              title={a.description || `${a.provider} · ${a.model}`}>
              <span className="member-dot" style={{ background: agentColor(a.name) }} /> @{a.name}
            </button>
          ))}
          {missing.map(m => (
            <button key={m} className="chip danger" title="This agent no longer exists — it is removed when you save"
              onClick={() => toggleMember(m)}>@{m} (deleted) ✕</button>
          ))}
        </div>
      </div>
      <div className="field"><span className="field-label">Who answers a message without @mentions</span>
        <div className="chip-row">
          {MODES.map(m => (
            <button key={m.id} className={`chip ${draft.reply_mode === m.id ? 'active' : ''}`} title={m.help}
              onClick={() => setDraft({ ...draft, reply_mode: m.id })}>{m.label}</button>
          ))}
        </div>
        <div className="field-help">{MODES.find(m => m.id === draft.reply_mode)?.help}</div>
      </div>
      <div className="save-actions">
        <button className="ghost-btn" onClick={() => setEditing(null)}>Cancel</button>
        <button className="primary-btn" disabled={!draft.name.trim() || !draft.members.length} onClick={save}>
          {editing === 'new' ? 'Create channel' : 'Save'}
        </button>
      </div>
    </div>
  );

  const rooms = rows.filter(r => r.kind === 'channel');
  const dms = rows.filter(r => r.kind === 'dm');

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <div className="card-head">
            <h3>Channels</h3>
            <button className="primary-btn" onClick={() => { setEditing('new'); setDraft(EMPTY); }}>+ New channel</button>
          </div>
          <p className="muted">Group conversations with one or more agents. In a channel, <code>@name</code> asks a specific agent and <code>@all</code> asks everyone.</p>
          {editing === 'new' && editor}
          <table className="data-table">
            <thead><tr><th>Channel</th><th>Members</th><th>Who answers</th><th>Messages</th><th>Last activity</th><th /></tr></thead>
            <tbody>
              {rooms.map(c => (
                <React.Fragment key={c.id}>
                  <tr onClick={() => onOpen(c.id)}>
                    <td><b>#{c.name}</b>{c.busy && <span className="badge">answering…</span>}{c.topic && <div className="muted small">{c.topic}</div>}</td>
                    <td>{c.members.map(m => <span key={m} className="member-chip small-chip" style={{ borderColor: agentColor(m) }}>@{m}</span>)}</td>
                    <td>{MODES.find(m => m.id === c.reply_mode)?.label}</td>
                    <td>{c.message_count ?? '—'}</td>
                    <td>{ago(c.last_activity)}</td>
                    <td className="row-actions" onClick={e => e.stopPropagation()}>
                      <button className="ghost-btn" onClick={() => onOpen(c.id)}>Open</button>
                      <button className="ghost-btn" onClick={() => startEdit(c)}>Edit</button>
                      <button className="ghost-btn" onClick={() => clear(c)}>Clear</button>
                      {c.id !== 'general' && <button className="ghost-btn danger" onClick={() => remove(c)}>Delete</button>}
                    </td>
                  </tr>
                  {editing === c.id && <tr><td colSpan={6}>{editor}</td></tr>}
                </React.Fragment>
              ))}
            </tbody>
          </table>
        </div>

        <div className="settings-card">
          <h3>Direct messages</h3>
          <p className="muted">Private conversations with one agent. Click an agent in the sidebar to start one.</p>
          {dms.length === 0 && <div className="muted">None yet.</div>}
          {dms.length > 0 && (
            <table className="data-table">
              <thead><tr><th>Agent</th><th>Messages</th><th>Last activity</th><th /></tr></thead>
              <tbody>
                {dms.map(c => (
                  <tr key={c.id} onClick={() => onOpen(c.id)}>
                    <td><span className="member-dot" style={{ background: agentColor(c.members[0]) }} /> <b>@{c.members[0]}</b>
                      {c.orphan && <span className="badge" title="This agent was renamed or deleted in Settings">agent deleted</span>}</td>
                    <td>{c.message_count ?? '—'}</td>
                    <td>{ago(c.last_activity)}</td>
                    <td className="row-actions" onClick={e => e.stopPropagation()}>
                      <button className="ghost-btn" onClick={() => onOpen(c.id)}>Open</button>
                      <button className="ghost-btn" onClick={() => clear(c)}>Clear</button>
                      <button className="ghost-btn danger" onClick={() => remove(c)}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
};

export default ChannelsView;
