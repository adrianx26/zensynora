# Linux install check — 2026-10-12

> **Archived on 2026-10-13 (v0.7.0).** Linux install check report for 0.5.0 (October 2026). Kept for history; it describes the project at the time it was written and may not match the current code. Current documentation: [docs/README.md](../README.md).

Verified on Linux x86_64 with Python 3.14 (same as this machine), fresh venv,
`./install.sh --no-docker -y`, then re-run for idempotency.

## Step-by-step results

| # | Step | Before | After |
|---|------|--------|-------|
| 1 | System packages / python3-venv | OK | OK |
| 2 | Python version check | OK | OK |
| 3 | Create venv + pip bootstrap | OK | OK |
| 4 | `pip install -r requirements.txt` | numpy pinned `<2.0` → numpy 1.26 has no Python 3.13/3.14 wheel, compiled from source (slow, needs compiler, unsupported) | numpy `<3.0` → binary wheel |
| 4b | `pip install -e .` | skipped when run from the source dir (`import myclaw` succeeds via CWD) → no `zensynora` command | checks `pip show zensynora` + console script |
| 4c | Scrapling browsers | `--force` re-downloaded every run | idempotent |
| 6.6 | WebUI `npm install` + `vite build` | OK | OK |
| 10 | Verification | all green but checks ran from CWD (masked missing install) | runs from `/`, also checks `h2` and `zensynora --help` |

## Runtime bugs found after install (all fixed)

1. `zensynora agent` crashed immediately: `Got unexpected extra argument (default)` —
   `knowledge list` command was a module-level `def list()` that shadowed the builtin. (`myclaw/cli.py`)
2. Every LLM call failed: `http2=True` but `h2` not installed → `httpx[http2]`. (`requirements.txt`, `pyproject.toml`)
3. Web UI logged an `AttributeError` on every request when prometheus-client is absent. (`myclaw/metrics.py`)
4. `knowledge sync --force` crashed: `KnowledgeDB.clear_all_data` missing. (`myclaw/knowledge/db.py`)
5. `build_context` crashed: `_batch_read_notes()` got unexpected `db_path`. (`myclaw/knowledge/storage.py`)
6. Agent history dropped the latest turns (buffered messages not flushed before read). (`myclaw/memory.py`)
7. Process hung on exit (agent / gateway / webui / pytest): pooled aiosqlite connections never closed.
   (`cli.py`, `gateway.py`, `web/api.py`, `tests/conftest.py`)
8. "coroutine was never awaited" warning on Ctrl+C. (`myclaw/__init__.py`)
9. `uninstall.sh` removed `$(pwd)/venv` — running it from `~` would delete `~/venv`. Now uses the script dir.
10. `.gitignore` pattern `test*.py` hid the whole `tests/` suite from git; stray `=0.20.0` line removed.

## Result

- Install: exit 0, "All core components verified".
- Tests: 459 passed, 2 skipped (Python 3.14, numpy 2.5).
- `zensynora agent` end-to-end against an Ollama-compatible endpoint: reply received, clean exit.
- `zensynora webui`: `/`, `/health`, `/api/agents` → 200, clean Ctrl+C shutdown.

Note: plain `pytest` still fails the `--cov-fail-under=60` gate in pyproject (actual ≈30%).
That is a coverage target, not a failure.

## Web UI update (2026-10-12, evening)

- **"Disconnected / Max reconnection attempts reached"**: uvicorn was installed without a
  WebSocket library, so every `/ws/chat` connection was rejected. Now `uvicorn[standard]`
  (installs `websockets`); the installer checks for it.
- **Settings page** (sidebar → Settings): default provider/model with *Test connection & list
  models*, per-provider URLs and API keys, named agents (add/edit/remove), Telegram/WhatsApp,
  feature toggles, timeouts, security. Saves to `~/.myclaw/config.json`; secrets are never sent
  back to the browser. Only reachable from this computer unless an admin API key is set.
- **Navigation**: Chat, Settings, Costs & usage (cost dashboard), Tools (all 95 agent tools).
- **System Health** is live (LLM reachability + latency, model present, knowledge notes, MCP,
  swarms, channels) instead of hard-coded text.
- **Clear History** now also clears the server-side conversation memory (asks to confirm).
- Chat box spans the full width; a *Reconnect* button replaces "refresh the page"; switching
  agents no longer leaves stray reconnect loops.
