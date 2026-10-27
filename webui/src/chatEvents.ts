// Grouping of agent activity in a chat (2026-10-27).
//
// Every tool call, delegation and job step is stored as a role "event"
// message. Shown one per line they buried the conversation ("@Executor used
// browse" five times in a row). Consecutive events between two real messages
// are now one group with a one-line summary; the steps open on click.
import type { ChatMsg } from './api';

export type ActivityMode = 'grouped' | 'all' | 'hidden';
export const ACTIVITY_MODES: { id: ActivityMode; label: string; help: string }[] = [
  { id: 'grouped', label: 'Activity: grouped', help: 'One line per run of agent steps; click it to see each step' },
  { id: 'all', label: 'Activity: every step', help: 'Every tool call and delegation on its own line' },
  { id: 'hidden', label: 'Activity: hidden', help: 'Only messages; steps stay in the Activity panel' },
];

export type ChatItem =
  | { kind: 'message'; msg: ChatMsg }
  | { kind: 'events'; key: string; events: ChatMsg[] };

/** Messages with consecutive events collected into groups (order kept). */
export function groupMessages(messages: ChatMsg[], mode: ActivityMode): ChatItem[] {
  const out: ChatItem[] = [];
  let run: ChatMsg[] = [];
  const flush = () => {
    if (!run.length) return;
    if (mode === 'all') run.forEach(e => out.push({ kind: 'events', key: `e${e.id}`, events: [e] }));
    else if (mode === 'grouped') out.push({ kind: 'events', key: `g${run[0].id}`, events: run });
    run = [];
  };
  for (const m of messages) {
    if (m.role === 'event') {
      run.push(m);
    } else {
      flush();
      out.push({ kind: 'message', msg: m });
    }
  }
  flush();
  return out;
}

export interface GroupSummary {
  text: string;      // "@Executor: browse ×5 · @default asked @Executor · job “weather” ok"
  steps: number;
  failed: number;
  ms: number;        // total tool / delegation time
}

/** One line for a group of events: tools counted per agent, delegations and jobs named. */
export function summarizeEvents(events: ChatMsg[]): GroupSummary {
  const tools = new Map<string, Map<string, number>>(); // agent -> tool -> count
  const other: string[] = [];
  const jobs = new Map<string, string>(); // job name -> last status
  let failed = 0;
  let ms = 0;
  for (const e of events) {
    const m = (e.meta || {}) as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
    if (m.ok === false || (m.type === 'job_run' && m.status && !['started', 'ok'].includes(m.status))) failed += 1;
    if (typeof m.ms === 'number' && m.type !== 'delegation_end') ms += m.ms;
    switch (m.type) {
      case 'tool_end': {
        const agent = String(m.agent || e.sender || '?');
        const per = tools.get(agent) || new Map<string, number>();
        per.set(String(m.tool), (per.get(String(m.tool)) || 0) + 1);
        tools.set(agent, per);
        break;
      }
      case 'delegation_start': {
        const s = `@${m.from} asked @${m.to}`;
        if (!other.includes(s)) other.push(s);
        break;
      }
      case 'agent_created':
        other.push(`@${m.from} created @${m.to}`);
        break;
      case 'job_run':
        jobs.set(String(m.job), String(m.status));
        break;
      default:
        break; // delegation_end and unknown types: counted as steps only
    }
  }
  const parts: string[] = [];
  for (const [agent, per] of tools) {
    const list = [...per].map(([t, n]) => (n > 1 ? `${t} ×${n}` : t)).join(', ');
    parts.push(`@${agent}: ${list}`);
  }
  parts.push(...other);
  for (const [job, status] of jobs) parts.push(`job “${job}” ${status}`);
  return { text: parts.join(' · ') || `${events.length} steps`, steps: events.length, failed, ms };
}

export function loadActivityMode(): ActivityMode {
  try {
    const v = localStorage.getItem('zs.chat.activity');
    if (v === 'grouped' || v === 'all' || v === 'hidden') return v;
  } catch { /* storage blocked: use the default */ }
  return 'grouped';
}

export function saveActivityMode(mode: ActivityMode): void {
  try { localStorage.setItem('zs.chat.activity', mode); } catch { /* not important */ }
}
