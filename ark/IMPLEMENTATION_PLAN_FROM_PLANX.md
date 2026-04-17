# ZenSynora Implementation Plan — From planx.md Review

## Objective
Address critical security gaps, performance bottlenecks, and maintainability issues identified in the comprehensive code review. Focus on production hardening before feature expansion.

---

## Phase 1: Security Hotfixes (Week 1)

| # | Task | File | Priority | Effort |
|---|------|------|----------|--------|
| 1.1 | **Remove `python`, `python3`, `pip` from `ALLOWED_COMMANDS`** — closes shell sandbox escape via `python -c "import os; os.system(...)"` | `myclaw/tools.py` | P0 | 30 min | ✅ DONE |
| 1.2 | **Harden `register_tool()` AST validation** — add `importlib` to forbidden imports; detect `getattr(__builtins__, 'eval')` via `ast.Attribute` traversal; restrict `open()` usage | `myclaw/tools.py` | P0 | 2–3 hrs | ✅ DONE |
| 1.3 | **Add path traversal tests** — validate `validate_path()` blocks `../../etc/passwd` and symlink attacks | `tests/test_security_path_traversal.py` | P0 | 2 hrs | ✅ DONE |
| 1.4 | **Add `register_tool()` AST validation tests** — test bypass attempts (`importlib.import_module`, `__builtins__.__dict__['eval']`) | `tests/test_register_tool_ast.py` | P0 | 2 hrs | ✅ DONE |
| 1.5 | **Sanitize FTS query inputs** in `_search_knowledge_context()` — escape `NOT`, `NEAR`, unmatched quotes for FTS5 safety | `myclaw/knowledge/db.py` | P0 | 1 hr | ✅ DONE |
| 1.6 | **Encrypt SSH password parameter** — accept key-based auth only, or use `getpass`/keyring; never log or cache plaintext passwords | `myclaw/tools.py` | P1 | 2 hrs | ✅ DONE |

---

## Phase 2: Async Performance Fixes (Week 1–2)

| # | Task | File | Priority | Effort |
|---|------|------|----------|--------|
| 2.1 | **Wrap `_search_knowledge_context()` in `asyncio.to_thread()`** — prevents blocking the event loop on large FTS queries | `myclaw/agent.py` | P0 | 1 hr | ✅ DONE |
| 2.2 | **Convert knowledge DB ops to async** — added `a_search_notes`, `a_build_context`, `a_read_note`, `a_write_note` async wrappers in `knowledge/__init__.py` using `asyncio.to_thread()` | `myclaw/knowledge/__init__.py` | P1 | 2 hrs | ✅ DONE |
| 2.3 | **Move context summarization off hot path** — summarization now runs as background task after responding; `_background_summarize_context()` stores results in KB | `myclaw/agent.py` | P0 | 3–4 hrs | ✅ DONE |
| 2.4 | **Replace `requests` with `httpx.AsyncClient`** in `browse()` and `download_file()` — both are now async using `httpx.AsyncClient` with proper exception handling | `myclaw/tools.py` | P1 | 2 hrs | ✅ DONE |
| 2.5 | **Implement true async SQLite pool** — `AsyncSQLitePool` now maintains 3 connections per DB with `asyncio.Semaphore` checkout instead of single connection + lock | `myclaw/memory.py` | P1 | 4–6 hrs | ✅ DONE |
| 2.6 | **Add TTL to provider cache** — `_provider_cache` now has 5-min TTL + config mtime invalidation; `_provider_cache_timestamps` tracks entry ages | `myclaw/provider.py` | P2 | 1 hr | ✅ DONE |
| 2.7 | **Optimize `KnowledgeGapCache` cleanup** — replaced O(n) dict-rebuild every call with amortized cleanup every 100 calls via `_call_count` | `myclaw/agent.py` | P2 | 1 hr | ✅ DONE |

---

## Phase 3: Code Health — Decompose God Modules (Week 2–3)

| # | Task | File | Priority | Effort |
|---|------|------|----------|--------|
| 3.1 | **Extract `tools.py` into `tools/` package** | — | P2 | 6–8 hrs |
| | ├── `tools/core.py` — registry, hook system, rate limiter, validation | | | |
| | ├── `tools/shell.py` — `shell_async()`, sandbox, command allowlist | | | |
| | ├── `tools/files.py` — `read_file()`, `write_file()`, path validation | | | |
| | ├── `tools/web.py` — `browse()`, `download_file()`, HTTP client | | | |
| | ├── `tools/swarm.py` — swarm tools, delegation | | | |
| | ├── `tools/ssh.py` — SSH remote execution | | | |
| | ├── `tools/kb.py` — knowledge base tools | | | |
| | └── `tools/scheduler.py` — task scheduling | | | |
| 3.2 | **Refactor `agent.py` `think()` method** — extract into sub-methods: `_route_message()`, `_build_context()`, `_execute_tools()`, `_handle_summarization()` | `myclaw/agent.py` | P1 | 4–6 hrs |
| 3.3 | **Add lazy imports** for heavy modules in `tools.py` to reduce startup time | `myclaw/tools/` | P2 | 1 hr |

---

## Phase 4: Web UI Hardening & UX (Week 3)

| # | Task | File | Priority | Effort |
|---|------|------|----------|--------|
| 4.1 | **Dynamic API/WebSocket base URL** — `getApiBase()` / `getWsBase()` derive from `window.location`; handles Vite dev server fallback to :8000 | `webui/src/App.tsx` | P1 | 1 hr | ✅ DONE |
| 4.2 | **WebSocket reconnection & heartbeat** — exponential backoff (max 30s, 10 attempts); `__ping__`/`__pong__` every 30s; backend handles ping in WS loop | `webui/src/App.tsx`, `myclaw/web/api.py` | P1 | 2–3 hrs | ✅ DONE |
| 4.3 | **Message history persistence** — per-agent `localStorage` key; loads on agent switch; saves on every message; clear history button | `webui/src/App.tsx` | P1 | 2 hrs | ✅ DONE |
| 4.4 | **Connection status indicator** — visual badges showing 🟢/🟡/🔴 state; send disabled when offline | `webui/src/App.tsx`, `webui/src/index.css` | P1 | 1 hr | ✅ DONE |
| 4.5 | **Mobile-responsive CSS** — sidebar slide-out drawer; hamburger toggle; touch-friendly inputs; media queries at 768px/1024px | `webui/src/index.css` | P2 | 2 hrs | ✅ DONE |
| 4.6 | **Dark mode toggle** — CSS `:root` + `[data-theme="light"]` variables; toggle button; persists to `localStorage` | `webui/src/index.css`, `webui/src/App.tsx`, `webui/index.html` | P2 | 2 hrs | ✅ DONE |
| 4.7 | **Console readline integration** — `readline` history at `~/.myclaw/.console_history`; tab completion for `@agentname`; graceful fallback | `cli.py` | P2 | 2 hrs | ✅ DONE |

---

## Phase 5: Observability & Monitoring (Week 4)

| # | Task | File | Priority | Effort |
|---|------|------|----------|--------|
| 5.1 | **Prometheus `/metrics` endpoint** — expose: request latency histograms, LLM token usage, tool execution rates, cache hit/miss, error rates | `myclaw/metrics.py`, FastAPI app | P1 | 3–4 hrs |
| 5.2 | **Optional Sentry integration** — capture exceptions with PII scrubbing; disabled by default | `myclaw/logging_config.py` | P1 | 1 hr |
| 5.3 | **Add `python cli.py audit verify`** — verify `TamperEvidentAuditLog` integrity via CLI | `cli.py`, `myclaw/audit_log.py` | P2 | 1 hr |
| 5.4 | **Add `python cli.py audit export`** — export audit logs to JSON/CSV | `cli.py` | P2 | 1 hr |
| 5.5 | **Add `python cli.py user delete <user_id>`** — GDPR-compliant purge of all user data | `cli.py`, `myclaw/memory.py`, `myclaw/knowledge/` | P2 | 2 hrs |

---

## Phase 6: Infrastructure & Scaling (Week 4–5)

| # | Task | File | Priority | Effort |
|---|------|------|----------|--------|
| 6.1 | **Extract in-memory state to Redis/shared store** — `_agent_registry`, `_HOOKS`, `_rate_limiter` for multi-worker deployments | `myclaw/state_store.py` | P1 | 4–6 hrs |
| 6.2 | **Replace `apscheduler.BackgroundScheduler` with async job queue** — use `arq` or `celery` for reminders and research jobs | `myclaw/scheduler.py` | P1 | 4–6 hrs |
| 6.3 | **Official `Dockerfile` + `docker-compose.yml`** — separate services: API, Web UI (nginx), Redis | repo root | P2 | 3–4 hrs |
| 6.4 | **GitHub Actions CI/CD** — lint (`ruff`, `mypy`), tests (`pytest`), security scan (`bandit`), container build/push | `.github/workflows/ci.yml` | P2 | 2–3 hrs |
| 6.5 | **Encrypt secrets at rest** — use `keyring` or master-password-derived `cryptography.fernet` key for `config.json` | `myclaw/config.py` | P1 | 3–4 hrs |

---

## Immediate Action Items (This Sprint)

1. **Security hotfix** — Remove `python`, `python3`, `pip` from `ALLOWED_COMMANDS` (30 min)
2. **Performance hotfix** — Wrap knowledge search in `asyncio.to_thread()` (1 hr)
3. **Code health** — Begin `tools.py` extraction into `tools/` package (start with `tools/core.py` + `tools/shell.py`)
4. **UX fix** — Make Web UI API base URL dynamic via `window.location` (1 hr)
5. **Test gap** — Create `test_security_path_traversal.py` and `test_register_tool_ast.py`

---

## Success Criteria

- [ ] `bandit` security scan passes with zero high-severity findings
- [ ] All P0 security items closed
- [ ] Knowledge search no longer blocks event loop (verified via `pytest-asyncio`)
- [ ] `tools.py` under 500 lines per module after decomposition
- [ ] Web UI loads correctly behind reverse proxy without hardcoded localhost
- [ ] CI pipeline green: lint, typecheck, tests, security scan

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| `tools.py` refactor breaks existing tools | Maintain `TOOLS` registry API; run full `test_tools.py` after each extraction |
| Async knowledge DB migration causes deadlocks | Use `aiosqlite` with existing WAL mode; add timeout and retry logic |
| WebSocket streaming introduces race conditions | Use atomic React state updates; add sequence IDs to chunks |
| Removing `python` from allowlist breaks legitimate use cases | Document workaround: users can still register custom Python tools via `register_tool()` |

---

*Plan derived from: `ark/planx.md`*  
*Focus: Hardening → Testing → Scaling → Features*
