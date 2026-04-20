# ZenSynora — Remaining Tasks (from planx.md)

*Derived from: `docs/dev/ark/planx.md`*  
*Last updated: 2026-04-19*

---

## P0 / P1 — High Priority

| # | Task | File(s) | Priority | Effort | Dependencies |
|---|------|---------|----------|--------|--------------|
| ~~1~~ | ~~**Web UI response streaming**~~ — ✅ **DONE** (2026-04-19). `Agent.stream_think()` wired to WebSocket via `__STREAM_START__` / chunk / `__STREAM_END__` protocol. React appends chunks to last agent message incrementally with typing indicator. | `webui/src/App.tsx`, `myclaw/web/api.py`, `webui/src/index.css` | P1 | — | — |
| ~~2~~ | ~~**Streaming tool calls**~~ — ✅ **DONE** (2026-04-19). All providers (OpenAI-compat, Ollama, Anthropic, Gemini) now collect `tool_calls` during streaming via `tool_calls_collector`. `stream_think()` streams initial reasoning, detects tool calls, yields `__TOOL_CALLS_START__`/JSON/`__TOOL_CALLS_END__` markers, executes tools via existing `_execute_tools()`, then streams follow-up response word-by-word. Frontend displays tool execution status in chat. | `myclaw/agent.py`, `myclaw/provider.py`, `webui/src/App.tsx`, `webui/src/index.css` | P1 | — | — |
| ~~3~~ | ~~**Prometheus `/metrics` endpoint**~~ — ✅ **DONE** (2026-04-19). Created `myclaw/metrics.py` with `PrometheusMetrics` class (lazy init, zero overhead when `prometheus-client` not installed). Instrumented all 4 providers, tool executor, semantic cache, and FastAPI middleware. `/metrics` endpoint serves `prometheus_client` exposition format. | `myclaw/metrics.py`, `myclaw/web/api.py`, `myclaw/provider.py`, `myclaw/tools/core.py`, `myclaw/semantic_cache.py`, `requirements.txt`, `pyproject.toml` | P1 | — | — |
| ~~4~~ | ~~**Encrypt secrets at rest**~~ — ✅ **DONE** (2026-04-19). Created `myclaw/config_encryption.py` with Fernet encryption, auto-detect format, OS keychain support via `keyring`, fallback to `~/.myclaw/.config_key` (0o600). Transparent to `load_config()` and `save_config()`. CLI: `zensynora config encrypt`, `zensynora config decrypt`, `zensynora config status`. | `myclaw/config_encryption.py`, `myclaw/config.py`, `myclaw/cli.py`, `requirements.txt`, `pyproject.toml` | P1 | — | — |
| ~~5~~ | ~~**Offline mode for core features**~~ — ✅ **DONE** (2026-04-19). Created `myclaw/offline.py` with `FallbackChatWrapper` that catches connection errors and retries with local providers (Ollama → LM Studio → llama.cpp). Agent `think()` and `stream_think()` now use `_provider_chat()` which auto-fallbacks. Config option `intelligence.offline_mode` (default: true). Cloud model names mapped to local equivalents. | `myclaw/offline.py`, `myclaw/agent.py`, `myclaw/config.py` | P1 | — | — |
| ~~6~~ | ~~**Async scheduler: fully replace apscheduler**~~ — ✅ **DONE** (2026-04-19). Removed last `apscheduler.BackgroundScheduler` import from `cli.py` (agent command). `AsyncScheduler` now used consistently in both `gateway.py` and `cli.py`. `python-telegram-bot[job-queue]` still uses PTB's internal job queue for Telegram reminders, which is separate from apscheduler. | `myclaw/cli.py` | P1 | — | — |

---

## P2 — Medium Priority

| # | Task | File(s) | Priority | Effort | Dependencies |
|---|------|---------|----------|--------|--------------|
| ~~7~~ | ~~**Advanced search & filtering**~~ — ✅ **DONE** (2026-04-19). Created `myclaw/knowledge/advanced_search.py` with hybrid FTS5 + semantic search using `sentence-transformers`. `SearchFilters` supports date-range, tags, categories, and configurable semantic weight. `search_advanced()` combines FTS rank and cosine similarity. Async wrapper `a_search_advanced()` added. | `myclaw/knowledge/advanced_search.py`, `myclaw/knowledge/__init__.py` | P2 | — | — |
| ~~8~~ | ~~**Audit log CLI commands**~~ — ✅ **DONE** (2026-04-19). Added `audit` CLI group with `verify`, `export`, and `status` commands. `verify` checks hash-chain integrity. `export` copies log to file. `status` shows recent entries. | `myclaw/cli.py` | P2 | — | — |
| ~~9~~ | ~~**GDPR compliance helpers**~~ — ✅ **DONE** (2026-04-19). Created `myclaw/gdpr.py` with `delete_user_data()` (erasure) and `export_user_data()` (portability). CLI: `zensynora gdpr delete <user_id> [--dry-run]` and `zensynora gdpr export <user_id>`. **Opt-in during onboard** (default disabled). Config: `security.gdpr_enabled`. | `myclaw/gdpr.py`, `myclaw/cli.py`, `myclaw/config.py`, `myclaw/onboard.py` | P2 | — | — |
| 10 | **Optional Sentry integration** — Capture exceptions with PII scrubbing; disabled by default | `myclaw/logging_config.py` | P2 | 1–2 hrs | `sentry-sdk` optional dependency |
| ~~11~~ | ~~**Performance dashboard (`/admin`)**~~ — ✅ **DONE** (2026-04-19). `myclaw/admin_dashboard.py` tracks active WS sessions, response times, routing decisions, KB stats, provider health. `/api/admin/dashboard` serves aggregated JSON. Session tracking wired into WebSocket handler. | `myclaw/admin_dashboard.py`, `myclaw/web/api.py` | P2 | — | — |
| ~~12~~ | ~~**LLM cost tracking**~~ — ✅ **DONE** (2026-04-19). `myclaw/cost_tracker.py` with SQLite `usage_records` table, per-provider pricing, monthly aggregation. Hooked into OpenAI-compat provider. `/api/admin/costs` endpoint. CLI: `costs` group (planned). | `myclaw/cost_tracker.py`, `myclaw/provider.py`, `myclaw/web/api.py` | P2 | — | — |
| ~~13~~ | ~~**Collaborative knowledge spaces**~~ — ✅ **DONE** (2026-04-19). `myclaw/knowledge_spaces.py` with spaces + members tables, RBAC (viewer/editor/admin), permission checking. REST API: `/api/spaces` CRUD + members. CLI: `spaces` group with create/list/members/add-member/remove-member/delete. | `myclaw/knowledge_spaces.py`, `myclaw/web/api.py`, `myclaw/cli.py` | P2 | — | — |

---

## P3 / Business — Lower Priority

| # | Task | File(s) | Priority | Effort | Dependencies |
|---|------|---------|----------|--------|--------------|
| ~~14~~ | ~~**MFA/TOTP for Web UI**~~ — ✅ **DONE** (2026-04-19). `myclaw/mfa.py` with TOTP provisioning (`pyotp`), QR code generation (`qrcode`), SQLite secret storage. WebSocket handler intercepts first message for `__MFA__:<code>` verification. REST API: `/api/mfa/setup`, `/verify`, `/disable`, `/status`. CLI: `mfa setup/verify/disable/status`. Graceful degradation when `pyotp` not installed. | `myclaw/mfa.py`, `myclaw/web/api.py`, `myclaw/cli.py`, `requirements.txt`, `pyproject.toml` | P1–P2 | — | — |
| 15 | **Freemium gating (`LicenseManager`)** — License key module gating max agents, swarms, advanced routing, SSH | `myclaw/license.py` | P2 | 4–6 hrs | License validation logic |
| ~~16~~ | ~~**Usage-based metering**~~ — ✅ **DONE** (2026-04-19). `myclaw/metering.py` with SQLite `usage_events` + `user_quotas` tables. Per-user daily quotas: `llm_requests_daily`, `llm_tokens_daily`, `tool_executions_daily`, `web_requests_daily`. Quota check in WebSocket handler sends `__QUOTA_EXCEEDED__`. REST API: `/api/metering/status`, `/metering/quota`. CLI: `metering status`, `metering set-quota`. | `myclaw/metering.py`, `myclaw/web/api.py`, `myclaw/cli.py` | P2 | — | — |
| 17 | **Enterprise SSO** — OIDC/SAML auth for Web UI and API | `myclaw/web/api.py`, `webui/src/` | P3 | 8–12 hrs | `authlib` or similar OIDC/SAML library |

---

## Test Coverage Gaps

| # | Task | File(s) | Priority | Effort |
|---|---|---|---|---|
| ~~18~~ | ~~**Telegram/WhatsApp handler integration tests**~~ — ✅ **DONE** (2026-04-19). `tests/test_telegram_handlers.py` with mocked TelegramChannel: allowed/blocked users, rate limiting, agent routing (default/named/unknown), queue backpressure. WhatsApp webhook verify, text processing, unauthorized number rejection, non-text handling. | `tests/test_telegram_handlers.py` | P2 | — |
| ~~19~~ | ~~**Provider retry/backoff logic tests**~~ — ✅ **DONE** (2026-04-19). Extended `tests/test_provider_retry.py` with `httpx.ConnectError`, `httpx.TimeoutException`, `httpx.HTTPStatusError` (5xx retried, 4xx not retried) tests. | `tests/test_provider_retry.py` | P2 | — |
| ~~20~~ | ~~**SQLite pool load/concurrency tests**~~ — ✅ **DONE** (2026-04-19). `tests/test_memory_pool_concurrency.py` with 10 concurrent workers, pool limit enforcement (3 connections, 5 workers queued), DB isolation, connection reuse, close_all cleanup. | `tests/test_memory_pool_concurrency.py` | P2 | — |

---

## Quick Wins (≤ 2 hours each)

- [x] ~~**#8**~~ — ✅ Done — Audit log CLI (`audit verify`, `audit export`, `audit status`)
- [x] ~~**#9**~~ — ✅ Done — GDPR helpers (`gdpr delete`, `gdpr export`) with opt-in onboarding
- [x] ~~**#6**~~ — ✅ Done — Fully replaced apscheduler in CLI with AsyncScheduler
- [x] ~~**#18**~~ — ✅ Done — Telegram/WhatsApp handler integration tests
- [x] ~~**#19**~~ — ✅ Done — Provider retry/backoff tests with httpx exceptions
- [x] ~~**#20**~~ — ✅ Done — SQLite pool load/concurrency tests

---

## Implementation Order Recommendation

1. **Week 1**: #1 (Web UI streaming) + #6 (Async scheduler cleanup)
2. **Week 2**: #3 (Prometheus metrics) + #8/#9 (Audit/GDPR CLI)
3. **Week 3**: #4 (Secrets encryption) + #5 (Offline mode)
4. **Week 4**: #2 (Streaming tool calls) + #12 (Cost tracking)
5. **Week 5+**: #7 (Advanced search), #11 (Admin dashboard), #14 (MFA), #15–17 (Business features)

---

---

## Implementation Log

### 2026-04-19 — Task #1: Web UI Response Streaming

**Files changed:**
- `myclaw/web/api.py` — Replaced simulated response loop with real `Agent.stream_think()` integration via WebSocket
- `webui/src/App.tsx` — Added incremental chunk rendering, streaming state tracking, typing indicator, disabled input during streaming
- `webui/src/index.css` — Added `.typing-indicator` animation with bouncing dots

**WebSocket streaming protocol:**
```
Client sends: user message text
Server sends: __STREAM_START__   → frontend creates empty agent message + typing indicator
Server sends: "Hello"             → appended to last agent message
Server sends: " world"            → appended
Server sends: __STREAM_END__      → typing indicator removed, message finalized
```

**Backend changes:**
- Added `_ensure_registry()` to build and cache the agent registry on first WebSocket connection (mirrors `_build_registry()` from `cli.py`)
- `chat_websocket()` now looks up the agent by `agent_name`, calls `agent.stream_think(data, user_id="webui")`, and forwards each chunk
- Internal `[TOOL_CALLS_NONE]` marker is translated to `__STREAM_END__` so the frontend knows when streaming completes
- Errors during streaming send `\n\n[Error: ...]` followed by `__STREAM_END__`

**Frontend changes:**
- Added `isStreaming` state — disables input and submit button while agent is generating
- `ChatMessage` interface extended with optional `isStreaming` flag
- `ws.onmessage` handler now distinguishes four message types:
  1. `__pong__` — heartbeat (unchanged)
  2. `__STREAM_START__` — creates new empty agent message with `isStreaming: true`
  3. Regular chunks — appended to the last agent message via functional state update
  4. `__STREAM_END__` — clears `isStreaming` flag from last message
- Typing indicator (three animated dots) appears next to the streaming message text
- `saveMessages()` strips `isStreaming` flags before persisting to `localStorage`

**Known limitations (Task #1):**
- Only one streaming response per WebSocket connection at a time. Concurrent messages from the same client will overwrite the streaming state.

---

### 2026-04-19 — Task #2: Streaming Tool Calls

**Files changed:**
- `myclaw/provider.py` — All four providers (OpenAICompat, Ollama, Anthropic, Gemini) now return `(async_iterator, tool_calls_collector)` when `stream=True`
- `myclaw/agent.py` — `stream_think()` rewritten to: (1) stream initial response, (2) check collector for tool calls, (3) yield tool markers, (4) execute tools via `_execute_tools()`, (5) stream follow-up response word-by-word
- `webui/src/App.tsx` — Added `pendingToolsRef`, handlers for `__TOOL_CALLS_START__`, tool JSON, `__TOOL_CALLS_END__`; displays tool list as system message
- `webui/src/index.css` — Added `.chat-message.system` styling with blue accent border for tool execution visibility

**Provider streaming tool call collection:**
- **OpenAI-compat**: Buffers `delta.tool_calls` by index, assembles `id`/`name`/`arguments` from streamed deltas
- **Ollama**: Captures `message.tool_calls` from the final SSE line of the stream
- **Anthropic**: Listens to `content_block_stop` events with `tool_use` blocks
- **Gemini**: Captures `function_call` parts from streaming chunks

**Agent streaming flow:**
```
1. Stream initial LLM reasoning → user sees text appear in real-time
2. If tool_calls collected:
   a. Yield __TOOL_CALLS_START__
   b. Yield JSON for each tool call
   c. Yield __TOOL_CALLS_END__
   d. Execute tools via _execute_tools() (parallel + sequential)
   e. Yield __STREAM_START__
   f. Stream follow-up response word-by-word
   g. Yield __STREAM_END__
3. If no tool calls:
   a. Save response to memory
   b. Yield [TOOL_CALLS_NONE]
```

**Frontend tool call display:**
- When `__TOOL_CALLS_START__` arrives, the current streaming message is finalized
- Tool JSON chunks are collected in `pendingToolsRef`
- When `__TOOL_CALLS_END__` arrives, a system message is added: "🔧 Running tools: • browse • shell"
- The follow-up response then streams as a new agent message

**Known limitations:**
- Tool execution itself is not streamed (tools run synchronously before the follow-up). The follow-up LLM call is streamed word-by-word for UX.
- Anthropic's event-based streaming may need SDK-version-specific tuning; the implementation uses defensive `getattr()` checks.

---

### 2026-04-19 — Task #3: Prometheus `/metrics` Endpoint

**Files changed:**
- `myclaw/metrics.py` — New module: `PrometheusMetrics` + `_NoopMetrics` (zero-overhead fallback)
- `myclaw/web/api.py` — Added `MetricsMiddleware` (ASGI) + `/metrics` endpoint
- `myclaw/provider.py` — Instrumented all 4 providers: OpenAI-compat, Ollama, Anthropic, Gemini
- `myclaw/tools/core.py` — Instrumented `_execute_single_tool()`: success, error, rate_limited, blocked
- `myclaw/semantic_cache.py` — Instrumented `get()`: exact hit, similarity hit, miss
- `requirements.txt` / `pyproject.toml` — Added `prometheus-client>=0.20.0`

**Metrics exposed:**
| Metric | Type | Labels |
|---|---|---|
| `zensynora_request_duration_seconds` | Histogram | method, endpoint, status |
| `zensynora_llm_tokens_total` | Counter | provider, model, token_type (prompt\|completion) |
| `zensynora_llm_requests_total` | Counter | provider, model, status (success\|error\|cached) |
| `zensynora_llm_request_duration_seconds` | Histogram | provider, model |
| `zensynora_llm_cost_usd_total` | Counter | provider, model |
| `zensynora_tool_executions_total` | Counter | tool_name, status (success\|error\|rate_limited\|blocked) |
| `zensynora_tool_execution_duration_seconds` | Histogram | tool_name |
| `zensynora_cache_hits_total` | Counter | cache_type |
| `zensynora_cache_misses_total` | Counter | cache_type |
| `zensynora_errors_total` | Counter | component, error_type |
| `zensynora_active_sessions` | Gauge | — |
| `zensynora_knowledge_entries` | Gauge | — |
| `zensynora_app_info` | Info | version |

**Design decisions:**
- **Lazy initialization**: `prometheus-client` is optional; if not installed, `_NoopMetrics` is used (all methods are no-ops)
- **Status bucketing**: HTTP status codes bucketed into 2xx/4xx/5xx to prevent cardinality explosion
- **Cost estimation**: Built-in pricing table for OpenAI, Anthropic, Gemini, Groq; Ollama/LM Studio cost = $0
- **Context managers**: `timed_llm_request()` and `timed_tool_execution()` for easy instrumentation

**Usage:**
```bash
# Install prometheus-client
pip install prometheus-client

# Scrape metrics
curl http://localhost:8000/metrics
```

---

### 2026-04-19 — Task #4: Encrypt Secrets at Rest

**Files changed:**
- `myclaw/config_encryption.py` — New module: Fernet encryption with auto-detect format
- `myclaw/config.py` — `load_config()` now uses `load_encrypted_or_plain()`; `save_config()` uses `save_encrypted()`
- `myclaw/cli.py` — Added `config` subcommand group: `encrypt`, `decrypt`, `status`
- `requirements.txt` / `pyproject.toml` — Added `cryptography>=42.0.0`, `keyring>=25.0.0`

**Encryption format:**
```json
{
  "__encrypted__": true,
  "data": "gAAAAAB..."
}
```

**Key storage priority:**
1. OS keychain (via `keyring`)
2. `~/.myclaw/.config_key` file (permissions 0o600)

**Backward compatibility:**
- Plaintext configs load normally
- Encrypted configs auto-detect and decrypt transparently
- `save_config()` only encrypts if a key already exists (user must explicitly run `encrypt` first)

**CLI usage:**
```bash
zensynora config encrypt    # Encrypt existing config
zensynora config decrypt    # Decrypt for editing
zensynora config status     # Show encryption status
```

**Design decisions:**
- **Opt-in encryption**: Configs remain plaintext until user runs `encrypt`
- **Transparent loading**: `load_config()` auto-detects encrypted vs plaintext
- **Graceful degradation**: If `cryptography` is not installed, everything works as before

---

### 2026-04-19 — Task #5: Offline Mode (Local Model Fallback)

**Files changed:**
- `myclaw/offline.py` — New module: `FallbackChatWrapper` with automatic provider fallback
- `myclaw/agent.py` — Added `_offline_mode` flag, `_provider_chat()` wrapper, replaced all `self.provider.chat()` calls
- `myclaw/config.py` — Added `intelligence.offline_mode` (default: true) + `MYCLAW_OFFLINE_MODE` env override

**Fallback chain:**
```
Cloud provider (OpenAI/Anthropic/Gemini/Groq)
  ↓ ConnectionError / TimeoutError / OSError
Ollama (localhost:11434)
  ↓ if not available
LM Studio (localhost:1234)
  ↓ if not available
llama.cpp (localhost:8080)
  ↓ if not available
Error: "No local LLM provider available"
```

**Model name mapping (cloud → local):**
| Cloud Model | Local Fallback |
|---|---|
| gpt-4o, gpt-4o-mini | llama3.2 |
| gpt-4-turbo, gpt-4 | llama3.1 |
| claude-3-5-sonnet | llama3.2 |
| claude-3-opus | llama3.1 |
| gemini-1.5-pro | llama3.1 |
| gemini-1.5-flash | llama3.2 |
| llama3-70b | llama3.1 |

**Design decisions:**
- **Enabled by default**: `offline_mode: true` in config
- **Transparent**: User sees a log warning but the response flows normally
- **Lazy fallback init**: Fallback wrapper only created on first connection failure
- **All chat paths covered**: think(), stream_think(), summarization, recovery all use `_provider_chat()`

---

### 2026-04-19 — Task #6: Fully Replace apscheduler with AsyncScheduler

**Files changed:**
- `myclaw/cli.py` — Replaced `apscheduler.BackgroundScheduler` with `AsyncScheduler` in the `agent` command; added graceful shutdown

**What was changed:**
The `agent` command (interactive console) was the last place using `apscheduler.BackgroundScheduler`:
```python
# BEFORE:
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.add_job(...)
scheduler.start()

# AFTER:
from .async_scheduler import get_scheduler
_sched = get_scheduler()
_sched.add_job(...)
await _sched.start()
# ... on exit:
await _sched.shutdown(wait=True)
```

**Note:** `python-telegram-bot[job-queue]` uses PTB's built-in `JobQueue` for Telegram bot reminders (`/remind` command). This is a PTB-native async scheduler, not apscheduler, and is intentionally left as-is since it's the standard PTB pattern.

---

### 2026-04-19 — Task #7: Advanced Search & Filtering

**Files changed:**
- `myclaw/knowledge/advanced_search.py` — New module: hybrid FTS5 + semantic search
- `myclaw/knowledge/__init__.py` — Exports `search_advanced`, `SearchFilters`, `SearchResult`, `a_search_advanced`

**Features:**
- **Hybrid scoring**: Combines FTS5 BM25 rank and semantic cosine similarity with configurable `semantic_weight` (0.0 = pure FTS, 1.0 = pure semantic)
- **Date-range filtering**: `date_from` / `date_to` filters on `entities.created_at`
- **Tag filtering**: Only returns entities with observations matching at least one tag
- **Category filtering**: Filter observations by category
- **Semantic search**: Uses `sentence-transformers` (`all-MiniLM-L6-v2`) with normalized embeddings; falls back to FTS5 if not installed

**Usage:**
```python
from myclaw.knowledge import search_advanced, SearchFilters
from datetime import datetime

results = search_advanced(
    query="Python asyncio",
    filters=SearchFilters(
        date_from=datetime(2026, 1, 1),
        tags=["programming"],
        semantic_weight=0.3,
    ),
    limit=10
)
```

**Design decisions:**
- **Lazy model loading**: Embedding model loads on first call, shared across searches
- **Candidate pruning**: FTS5 acts as first-stage filter; semantic similarity only computed on candidates
- **No schema migration needed**: Uses existing `entities_fts` and `observations` tables

---

### 2026-04-19 — Task #8: Audit Log CLI Commands

**Files changed:**
- `myclaw/cli.py` — Added `audit` CLI group with `verify`, `export`, `status` commands

**Commands:**
```bash
zensynora audit verify              # Verify hash-chain integrity
zensynora audit export [path]       # Export audit log to file
zensynora audit status              # Show recent entries
```

**`audit verify` output:**
```
✅ Audit log integrity verified (152 entries, last hash: a3f7b2d9...)
# or:
❌ Audit log integrity FAILED at entry 47: prev_hash_mismatch
```

---

### 2026-04-19 — Task #9: GDPR Compliance Helpers (Opt-in)

**Files changed:**
- `myclaw/gdpr.py` — New module: `delete_user_data()` and `export_user_data()`
- `myclaw/cli.py` — Added `gdpr` CLI group with `delete` and `export` commands
- `myclaw/config.py` — Added `security.gdpr_enabled` (default: false)
- `myclaw/onboard.py` — Added GDPR opt-in prompt during setup (default: disabled)

**GDPR features (gated by `security.gdpr_enabled`):**

| Command | Description |
|---|---|
| `zensynora gdpr delete <user_id>` | Right to Erasure — delete memory, KB, audit logs |
| `zensynora gdpr delete <user_id> --dry-run` | Preview what would be deleted |
| `zensynora gdpr export <user_id> -o path.zip` | Right to Data Portability — ZIP export |

**Data deleted:**
- Memory SQLite DB (`~/.myclaw/memory_{user_id}.db`)
- Knowledge Markdown files (`~/.myclaw/knowledge/{user_id}/`)
- Knowledge graph DB (`~/.myclaw/knowledge_{user_id}.db`)
- Audit log entries matching user_id
- Knowledge gap entries matching user_id

**Onboarding prompt:**
```
🔒 GDPR Compliance:
  Enables user data deletion (right to erasure) and export.
  Default: disabled. You can enable later in config.
Enable GDPR compliance features? [y/N]:
```

**Design decisions:**
- **Opt-in by default**: `gdpr_enabled: false` in config; user must explicitly enable during onboard or via config edit
- **Gated CLI**: `gdpr` commands refuse to run if `gdpr_enabled` is false
- **Dry-run support**: `--dry-run` flag previews deletions without executing
- **Audit trail preserved**: Export includes manifest.json with export timestamp

---

### 2026-04-19 — Task #11: Performance Dashboard

**Files changed:**
- `myclaw/admin_dashboard.py` — New module: dashboard data aggregation
- `myclaw/web/api.py` — Added `/api/admin/dashboard` endpoint; wired WS session tracking

**Dashboard data sources:**
| Metric | Source |
|---|---|
| Active WS sessions | In-memory `_active_websocket_sessions` dict with 5-min stale cleanup |
| Avg response time | Circular buffer of last 100 response times |
| Routing decisions | In-memory log of last 50 `IntelligentRouter` decisions |
| KB growth | `KnowledgeDB.get_stats()` (entities/observations/relations) |
| Provider health | HTTP health checks for local providers; API key presence for cloud |

**WebSocket session tracking:**
- `register_websocket_session()` on connection open
- `update_session_activity()` on each message
- `unregister_websocket_session()` on close
- Stale sessions auto-purged after 5 minutes

**API response example:**
```json
{
  "timestamp": "2026-04-19T20:00:00",
  "sessions": {"active_websocket_count": 3, "avg_response_time_ms": 1200},
  "routing": {"enabled": true, "recent_decisions": [...]},
  "knowledge_base": {"entities": 42, "observations": 156, "relations": 89},
  "providers": [{"provider": "ollama", "status": "healthy", "latency_ms": 45}]
}
```

---

### 2026-04-19 — Task #12: LLM Cost Tracking

**Files changed:**
- `myclaw/cost_tracker.py` — New module: SQLite-based cost tracking
- `myclaw/provider.py` — Hooked `record_usage()` into OpenAI-compat provider
- `myclaw/web/api.py` — Added `/api/admin/costs` endpoint

**Pricing table covers:**
| Provider | Models |
|---|---|
| OpenAI | gpt-4o, gpt-4o-mini, gpt-4-turbo, gpt-4 |
| Anthropic | claude-3-5-sonnet, claude-3-opus, claude-3-haiku |
| Gemini | gemini-1.5-pro, gemini-1.5-flash, gemini-2.0-flash |
| Groq | llama3-70b, mixtral-8x7b |
| Local | Ollama, LM Studio, llama.cpp = $0 |

**Schema:**
```sql
CREATE TABLE usage_records (
    id INTEGER PRIMARY KEY,
    provider TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER,
    cost_usd REAL, timestamp TEXT, month_key TEXT
);
```

**API:**
```bash
GET /api/admin/costs?month=2026-04
# Returns monthly aggregation by provider + overall summary
```

---

### 2026-04-19 — Task #13: Collaborative Knowledge Spaces

**Files changed:**
- `myclaw/knowledge_spaces.py` — New module: RBAC space management
- `myclaw/web/api.py` — REST API: `/api/spaces` CRUD + members
- `myclaw/cli.py` — CLI: `spaces` group

**Role hierarchy:**
```
viewer (0) — read, search
editor (1) — read, search, write, update
admin (2) — full control, manage members
```

**REST API:**
| Method | Endpoint | Description |
|---|---|---|
| POST | `/api/spaces` | Create space |
| GET | `/api/spaces?user_id=alice` | List user's spaces |
| GET | `/api/spaces/{id}` | Get space details |
| POST | `/api/spaces/{id}/members` | Add member |
| DELETE | `/api/spaces/{id}/members/{user}` | Remove member |

**CLI:**
```bash
zensynora spaces create "Team Docs" --owner alice
zensynora spaces list alice
zensynora spaces members space_abc123
zensynora spaces add-member space_abc123 bob editor --by alice
zensynora spaces remove-member space_abc123 carol --by alice
zensynora spaces delete space_abc123 alice
```

**Schema:**
```sql
CREATE TABLE spaces (id, name, description, owner, created_at, updated_at);
CREATE TABLE space_members (space_id, user_id, role, added_at, added_by);
```

---

### 2026-04-20 — Task #14: MFA / TOTP for Web UI

**Files changed:**
- `myclaw/mfa.py` — New module: TOTP authentication with `pyotp`
- `myclaw/web/api.py` — MFA endpoints (`/api/mfa/setup`, `/verify`, `/disable`, `/status`); WebSocket handler intercepts first message for MFA
- `myclaw/cli.py` — `mfa` CLI group: `setup`, `verify`, `disable`, `status`
- `requirements.txt` / `pyproject.toml` — Added `pyotp>=2.9.0`, `qrcode>=7.4`

**WebSocket MFA flow:**
```
Client connects → Server sends nothing
Client sends "__MFA__:123456" → Server verifies
  → Valid: sends "__MFA_OK__", normal chat resumes
  → Invalid: sends "__MFA_FAIL__", closes connection
Client sends anything else → Server sends "__MFA_REQUIRED__"
```

**Design decisions:**
- **Graceful degradation**: If `pyotp` not installed, MFA is bypassed (returns True)
- **SQLite secret storage**: Secrets stored in `~/.myclaw/mfa.db` with `enabled` flag
- **QR code generation**: Optional `qrcode` dependency; provisioning URI always returned
- **User-scoped**: Per-user MFA (default user "webui" for single-user deployments)

---

### 2026-04-20 — Task #16: Usage-Based Metering

**Files changed:**
- `myclaw/metering.py` — New module: per-user quota tracking
- `myclaw/web/api.py` — Quota check in WebSocket handler; metering REST API
- `myclaw/cli.py` — `metering` CLI group: `status`, `set-quota`

**Default quotas:**
| Quota Name | Default Limit |
|---|---|
| `llm_requests_daily` | 500 |
| `llm_tokens_daily` | 1,000,000 |
| `tool_executions_daily` | 200 |
| `web_requests_daily` | 100 |

**Quota enforcement in WebSocket:**
```python
allowed, remaining = check_quota("webui", "llm_requests_daily")
if not allowed:
    await websocket.send_text('__QUOTA_EXCEEDED__: Daily LLM request limit reached.')
```

**Schema:**
```sql
CREATE TABLE usage_events (user_id, event_type, resource, quantity, timestamp, period_key);
CREATE TABLE user_quotas (user_id, quota_name, limit_value);
```

---

### 2026-04-20 — Task #18: Telegram/WhatsApp Integration Tests

**File:** `tests/test_telegram_handlers.py`

**Tests cover:**
- **Allowed/blocked users**: Allowed user messages are queued; blocked users are silently ignored
- **Rate limiting**: Exhausted token bucket triggers "Too many messages" reply
- **Agent routing**: `@agentname` routes to named agent; unknown agents fall back to default
- **Queue backpressure**: Full queue (100 messages) rejects with "Service is busy"
- **WhatsApp webhook**: Verify token matching; unauthorized number rejection

---

### 2026-04-20 — Task #19: Provider Retry/Backoff Tests

**File:** `tests/test_provider_retry.py` (extended)

**Added tests:**
- `test_httpx_connect_error`: `httpx.ConnectError` triggers retry, succeeds on 2nd attempt
- `test_httpx_timeout`: `httpx.TimeoutException` retried up to max_retries
- `test_httpx_http_status_error`: 5xx errors retried; 4xx errors fail immediately
- `test_backoff_increases`: Exponential backoff delays verified (0.1s → 0.2s)

---

### 2026-04-20 — Task #20: SQLite Pool Load/Concurrency Tests

**File:** `tests/test_memory_pool_concurrency.py`

**Tests cover:**
- **Single connection**: Basic acquire/release works
- **Multiple connections same DB**: 3 connections to same DB are distinct objects
- **Concurrent access**: 10 workers insert concurrently, all 10 rows committed
- **Pool limit enforced**: 5 workers with 3-connection pool take >80ms (queuing)
- **DB isolation**: Table created in db1 not visible in db2
- **Connection reuse**: Released connection returned to pool and reused
- **Close all cleanup**: `close_all()` properly releases all connections

---

*Focus: Observability → Security hardening → Features → Business gating*
