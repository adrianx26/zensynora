// Message actions in the chat: copy, edit, delete, regenerate (v0.7.5).
//
// * MessageBody renders a message's text; fenced ``` code blocks get their own
//   box with a Copy button (no Markdown library — the rest stays plain text).
// * MessageActions is the small bar shown on hover (or with the "…" button on
//   touch screens and from the keyboard).
// * MessageEditor edits a message in place: Enter saves, Esc cancels.
import React, { useEffect, useRef, useState } from 'react';
import { copyText } from '../clipboard';

/** A button that copies and says "Copied" for a moment. */
export function CopyButton({ text, label = 'Copy', className = '' }: { text: string; label?: string; className?: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const onClick = async () => {
    setState((await copyText(text)) ? 'ok' : 'fail');
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), 1500);
  };
  return (
    <button type="button" className={`msg-action ${className}`} onClick={onClick} aria-live="polite">
      {state === 'ok' ? 'Copied' : state === 'fail' ? 'Copy failed' : label}
    </button>
  );
}

const FENCE = /```([^\n`]*)\n?([\s\S]*?)```/g;

/** Text with fenced code blocks shown as code, each with a Copy button. */
export function MessageBody({ text }: { text: string }) {
  const parts: React.ReactNode[] = [];
  let last = 0;
  let i = 0;
  for (const m of text.matchAll(FENCE)) {
    const at = m.index ?? 0;
    if (at > last) parts.push(<span key={i++}>{text.slice(last, at)}</span>);
    const lang = m[1].trim();
    const code = m[2].replace(/\n$/, '');
    parts.push(
      <div key={i++} className="code-block">
        <div className="code-head">
          <span>{lang || 'code'}</span>
          <CopyButton text={code} label="Copy code" />
        </div>
        <pre><code>{code}</code></pre>
      </div>,
    );
    last = at + m[0].length;
  }
  if (last < text.length) parts.push(<span key={i++}>{text.slice(last)}</span>);
  return <div className="msg-text">{parts}</div>;
}

interface ActionsProps {
  text: string;
  created: number;
  canEdit: boolean;
  canRegenerate: boolean;
  disabledReason?: string; // set while the agents are answering
  onEdit: () => void;
  onDelete: () => void;
  onRegenerate?: () => void;
}

export function MessageActions({ text, created, canEdit, canRegenerate, disabledReason, onEdit, onDelete, onRegenerate }: ActionsProps) {
  const [open, setOpen] = useState(false);
  const when = new Date(created * 1000);
  const locked = !!disabledReason;
  return (
    <div className={`msg-actions ${open ? 'open' : ''}`}>
      <button type="button" className="msg-action msg-more" aria-label="Message actions" aria-expanded={open}
        onClick={() => setOpen(o => !o)}>…</button>
      <div className="msg-action-list" role="toolbar" aria-label="Message actions">
        <time className="msg-time" dateTime={when.toISOString()} title={when.toLocaleString()}>
          {when.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
        </time>
        <CopyButton text={text} />
        {canEdit && <button type="button" className="msg-action" onClick={onEdit} disabled={locked} title={disabledReason || 'Edit'}>Edit</button>}
        {canRegenerate && onRegenerate && (
          <button type="button" className="msg-action" onClick={onRegenerate} disabled={locked}
            title={disabledReason || 'Write this answer again (the agent answers the same message once more)'}>Regenerate</button>
        )}
        <button type="button" className="msg-action danger" onClick={onDelete} disabled={locked} title={disabledReason || 'Delete'}>Delete</button>
      </div>
    </div>
  );
}

interface EditorProps {
  initial: string;
  resend: boolean; // your own message: saving sends it again
  onSave: (text: string) => void;
  onCancel: () => void;
}

export function MessageEditor({ initial, resend, onSave, onCancel }: EditorProps) {
  const [text, setText] = useState(initial);
  const ref = useRef<HTMLTextAreaElement | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  }, []);
  const save = () => { if (text.trim()) onSave(text); };
  return (
    <div className="msg-editor">
      <textarea
        ref={ref}
        className="field-input"
        value={text}
        rows={Math.min(12, Math.max(2, text.split('\n').length))}
        onChange={e => setText(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); save(); }
          if (e.key === 'Escape') { e.preventDefault(); onCancel(); }
        }}
      />
      <div className="msg-editor-bar">
        <span className="muted small">
          {resend ? 'Enter saves and sends again — later messages in this channel are removed.' : 'Enter saves, Esc cancels.'}
        </span>
        <button type="button" className="ghost-btn" onClick={onCancel}>Cancel</button>
        <button type="button" className="primary-btn" onClick={save} disabled={!text.trim()}>{resend ? 'Save & send' : 'Save'}</button>
      </div>
    </div>
  );
}
