// Updates page (v0.7.4): check for a new version, update, roll back and
// restart — without a terminal. The update itself is the safe updater
// (backups, side-by-side install, checks, automatic rollback); this page
// starts it and shows its progress.
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api';
import { restartServer, type UpdateStatus } from '../updates';

const UpdatesView: React.FC = () => {
  const [st, setSt] = useState<UpdateStatus | null>(null);
  const [checking, setChecking] = useState(false);
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null);
  const [ref, setRef] = useState('main');
  const [restarting, setRestarting] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(async (check = false) => {
    if (check) setChecking(true);
    try {
      const s = await api<UpdateStatus>(`/api/update/status${check ? '?check=true' : ''}`);
      setSt(prev => (check || !prev ? s : { ...prev, ...s, latest_version: s.latest_version ?? prev.latest_version,
        update_available: s.update_available ?? prev.update_available, history: s.history ?? prev.history,
        releases: s.releases ?? prev.releases, check_error: s.check_error ?? prev.check_error }));
      return s;
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
      return null;
    } finally {
      if (check) setChecking(false);
    }
  }, []);

  useEffect(() => { load(true); }, [load]);

  // Poll while an update or rollback runs; refresh the version check when it ends.
  useEffect(() => {
    if (!st?.update_running) return;
    timer.current = window.setInterval(async () => {
      const s = await load(false);
      if (s && !s.update_running) {
        if (timer.current) window.clearInterval(timer.current);
        load(true);
      }
    }, 2000);
    return () => { if (timer.current) window.clearInterval(timer.current); };
  }, [st?.update_running, load]);

  const run = async (action: 'start' | 'rollback') => {
    const what = action === 'start'
      ? `Update ZenSynora${ref !== 'main' ? ` from "${ref}"` : ''}? Your settings, API keys and knowledge are backed up first; if anything fails the previous version is restored automatically.`
      : 'Go back to the previous installed version? Your data stays as it is.';
    if (!window.confirm(what)) return;
    setNotice(null);
    try {
      await api(`/api/update/${action}`, { method: 'POST', body: JSON.stringify(action === 'start' ? { ref } : {}) });
      await load(false);
    } catch (e) {
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  const restart = async () => {
    setNotice(null);
    try {
      await restartServer(setRestarting);
    } catch (e) {
      setRestarting(null);
      setNotice({ kind: 'error', text: (e as Error).message });
    }
  };

  if (!st) return <div className="settings-view"><div className="settings-body"><div className="muted">Loading…</div></div></div>;

  const pct = st.step && st.steps ? Math.round((st.step / st.steps) * 100) : 0;
  const finished = !st.update_running && st.started;
  const releases = st.releases || [];
  const history = [...(st.history || [])].reverse();

  return (
    <div className="settings-view">
      <div className="settings-body">
        {notice && <div className={`notice ${notice.kind === 'error' ? 'error' : ''}`}>{notice.text}</div>}

        <div className="settings-card">
          <div className="card-head">
            <h3>Version</h3>
            <button className="ghost-btn" disabled={checking || !st.managed} onClick={() => load(true)}>
              {checking ? 'Checking…' : '↻ Check for updates'}
            </button>
          </div>
          <table className="data-table">
            <tbody>
              <tr><td>This server runs</td><td><b>{st.running_version}</b></td></tr>
              <tr><td>Installed</td><td><b>{st.installed_version}</b></td></tr>
              <tr><td>Latest on GitHub</td><td>{checking ? '…' : st.latest_version ? <b>{st.latest_version}</b> : '—'}</td></tr>
            </tbody>
          </table>
          {!st.managed && <div className="notice">{st.not_managed_reason}</div>}
          {st.check_error && <div className="notice error">{st.check_error}</div>}
          {st.managed && !checking && st.latest_version && !st.check_error && (
            st.update_available
              ? <div className="notice ok">Version {st.latest_version} is available.</div>
              : <div className="muted">You have the latest version.</div>
          )}
          {st.restart_needed && !st.update_running && (
            <div className="update-banner">
              <b>{st.installed_version} is installed</b>, but this server still runs {st.running_version}.{' '}
              <button className="primary-btn" disabled={!!restarting} onClick={restart}>{restarting || 'Restart ZenSynora now'}</button>
            </div>
          )}
        </div>

        {st.managed && (
          <div className="settings-card">
            <h3>Update</h3>
            <p className="muted">
              The safe updater backs up your settings, API keys, knowledge and the program folder, installs the new
              version next to the current one, checks it, and switches over only if everything works. If a step fails,
              the previous version is restored. It keeps running even if you close this page.
            </p>
            <div className="save-actions" style={{ justifyContent: 'flex-start', flexWrap: 'wrap', gap: 8 }}>
              <button className="primary-btn" disabled={st.update_running} onClick={() => run('start')}>
                {st.update_available ? `Update to ${st.latest_version}` : 'Update now'}
              </button>
              <button className="ghost-btn" disabled={st.update_running || releases.length < 2} onClick={() => run('rollback')}
                title={releases.length < 2 ? 'There is no previous version to go back to' : 'Switch back to the previous version'}>
                ↶ Roll back
              </button>
              <label className="muted small" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                Branch or tag
                <input className="field-input" style={{ width: 140 }} value={ref} disabled={st.update_running}
                  onChange={e => setRef(e.target.value.trim() || 'main')} />
              </label>
            </div>

            {(st.update_running || finished) && (
              <div style={{ marginTop: 12 }}>
                <div className="field-label">
                  {st.update_running
                    ? `${st.action === 'rollback' ? 'Rolling back' : 'Updating'} — step ${st.step ?? '…'} of ${st.steps ?? '…'}: ${st.step_text || 'starting'}`
                    : st.finished_ok
                      ? `✔ ${st.result}`
                      : `✖ The ${st.action || 'update'} did not finish${st.exit_code != null ? ` (exit code ${st.exit_code})` : ''}. ${st.action === 'update' ? 'The previous version was kept.' : ''}`}
                </div>
                {st.update_running && (
                  <div className="progress-bar" style={{ height: 6, background: 'var(--border, #444)', borderRadius: 3, overflow: 'hidden', margin: '6px 0' }}>
                    <div style={{ width: `${pct}%`, height: '100%', background: 'var(--accent, #6c8cff)', transition: 'width .4s' }} />
                  </div>
                )}
                {st.tail && <pre className="log-tail" style={{ maxHeight: 260, overflow: 'auto', fontSize: 12, whiteSpace: 'pre-wrap' }}>{st.tail}</pre>}
                {st.log && <div className="muted small">Full log: <code>{st.log}</code></div>}
              </div>
            )}
          </div>
        )}

        {st.managed && (releases.length > 0 || history.length > 0) && (
          <div className="settings-card">
            <h3>History</h3>
            {releases.length > 0 && <p className="muted">Versions kept for rollback: {releases.map(r => (r === st.active_release ? `${r.split('-')[0]} (active)` : r.split('-')[0])).join(', ')}</p>}
            {history.length > 0 && (
              <table className="data-table">
                <thead><tr><th>When</th><th>What</th><th>Version</th></tr></thead>
                <tbody>
                  {history.slice(0, 10).map((h, i) => (
                    <tr key={i}><td>{(h.at || '').replace('T', ' ').slice(0, 16)}</td><td>{h.action === 'rollback' ? 'Rolled back' : 'Updated'}</td>
                      <td>{h.from_version} → <b>{h.version}</b></td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>
    </div>
  );
};

export default UpdatesView;
