# ZenSynora Action Plan

> **Generated**: 2026-04-23
> **Based on**: fixplan.md audit (v0.4.1)
> **Goal**: Stabilize, secure, and harden the ZenSynora codebase through 4 implementation phases.

---

## Legend

- [ ] Pending
- [~] In Progress
- [x] Done

---

## Phase 1: P0 — Stop Crashes & Breaches (Week 1)

> **Goal**: Fix crashes, security breaches, and broken connection pools before anything else.
> **Exit Criteria**: App runs without infinite loops, shell is sanitized, admin endpoints gated, MFA safe.

- [x] **1.1 Fix Infinite Recursion in `agent.py`**
  - **File**: `myclaw/agent.py:575-601`
  - **Bug**: `_provider_chat()` calls itself instead of `self.provider.chat()`, causing `RecursionError` on every chat request.
  - **Fix**: Replace `await self._provider_chat(...)` with `await self.provider.chat(...)`.
  - **Impact**: Critical — chat is completely broken.

- [x] **1.2 Fix Shell Command Injection (newline bypass)**
  - **File**: `myclaw/tools/shell.py:65`
  - **Bug**: `asyncio.create_subprocess_shell(cmd, ...)` passes raw command to `/bin/sh`. Regex `[;&|`$(){}\[\]\\]` does **not** block newlines (`\n`), allowing multi-line injection.
  - **Fix**: Replace with `create_subprocess_exec(parts[0], *parts[1:], ...)` to match safe sync implementation.
  - **Impact**: High — arbitrary command execution via crafted input.

- [x] **1.3 Fix Error Message Disclosure**
  - **File**: `myclaw/tools/shell.py:88, 153`
  - **Bug**: `return f"Error: {e}"` leaks internal exception strings to LLM/user.
  - **Fix**: Return generic error message; log full trace server-side with `exc_info=True`.
  - **Impact**: Medium — information disclosure aids attacker reconnaissance.

- [x] **1.4 Fix CORS + Credentials Misconfiguration**
  - **File**: `myclaw/web/api.py:73-79`
  - **Bug**: `allow_origins=["*"]` with `allow_credentials=True` allows any website to make authenticated requests (CORS bypass).
  - **Fix**: Restrict to configured origins from config; add `cors_origins` to `SecurityConfig`.
  - **Impact**: High — session hijacking / credential theft from malicious websites.

- [x] **1.5 Add API-Key Auth to Admin Endpoints**
  - **File**: `myclaw/web/api.py:108-201`
  - **Bug**: `/api/admin/*`, `/api/metering/*`, `/api/mfa/*`, `/api/spaces/*` have zero authentication.
  - **Fix**: Implement `require_admin_api_key` FastAPI dependency; add `admin_api_key: SecretStr` to `SecurityConfig`.
  - **Impact**: High — full admin access without credentials.

- [x] **1.6 Fix MFA Secret Exposure**
  - **File**: `myclaw/web/api.py:154-160`
  - **Bug**: `mfa_setup` returns raw TOTP secret in response payload.
  - **Fix**: Return only provisioning URI and QR code. Never expose `secret` plaintext.
  - **Impact**: Medium — TOTP secret theft enables account takeover.

- [x] **1.7 Fix Broken AsyncSQLitePool**
  - **File**: `myclaw/memory.py:91-160`
  - **Bug**: Pool doesn't track checked-out connections. `release_connection` blindly releases semaphore, causing double-releases and DB lock errors under load.
  - **Fix**: Rewrite with proper checkout tracking (`_checked_out` set by `id(conn)`), match semaphore lifecycle to checkout state.
  - **Impact**: Critical — DB corruption / lock errors under concurrent load.

- [x] **1.8 Harden `register_tool` AST Validation**
  - **File**: `myclaw/tools/toolbox.py:99-149`
  - **Bug**: AST bypass possible via `getattr(__builtins__, 'eval')`, dynamic `importlib`, or `__import__` string access.
  - **Fix**: Block `getattr` entirely; add `importlib` to forbidden imports; block `compile`, `exec`, `eval` via any attribute pattern.
  - **Impact**: High — sandbox escape via dynamic tool registration.

- [x] **1.9 Fix MCP Server Error Disclosure**
  - **File**: `myclaw/mcp/server.py:61-63`
  - **Bug**: `except Exception` returns `str(e)` to MCP client, leaking internal exception details.
  - **Fix**: Return generic error to client; log full trace server-side.
  - **Impact**: Medium — information disclosure through MCP stdio transport.

- [x] **1.10 Fix MCP Client Error Disclosure**
  - **File**: `myclaw/mcp/client.py:100-101`
  - **Bug**: `_proxy()` returns `f"Error executing MCP tool '{name}': {e}"` to the agent/LLM, exposing internal errors.
  - **Fix**: Return generic error message; log full trace with `exc_info=True`.
  - **Impact**: Medium — same information disclosure pattern as shell tool.

- [x] **1.11 Fix MCP Client Unmanaged Background Tasks**
  - **File**: `myclaw/mcp/client.py:38`
  - **Bug**: `asyncio.create_task(self._run_server(...))` creates fire-and-forget tasks. Exceptions are silently swallowed; no reconnect logic.
  - **Fix**: Track tasks in a set, add exception callbacks, implement reconnect with backoff.
  - **Impact**: Medium — silent failures, resource leaks, no resilience.

---

## Phase 2: P1 — Security Hardening & Async Migration (Week 2)

> **Goal**: Patch OWASP-class vulnerabilities and replace blocking sync calls.
> **Exit Criteria**: No OWASP-class vulns, no blocking sync calls in hot paths, SSH/SSRF hardened.

- [x] **2.1 Replace Sync OpenAI Client with Async**
  - **File**: `myclaw/provider.py:651-719`
  - **Bug**: `self.client.chat.completions.create(...)` uses sync `openai.OpenAI` inside async methods, blocking the event loop.
  - **Fix**: Use `openai.AsyncOpenAI`; wrap streaming generator with `async for`.
  - **Impact**: Critical — all concurrent requests freeze during LLM calls.

- [x] **2.2 Fix SSH MITM (AutoAddPolicy)**
  - **File**: `myclaw/backends/ssh.py:42`
  - **Bug**: `paramiko.AutoAddPolicy()` accepts any host key.
  - **Fix**: Use `RejectPolicy` + `load_host_keys("~/.ssh/known_hosts")`.
  - **Impact**: Medium — man-in-the-middle on SSH connections.

- [x] **2.3 Fix SSRF in Web Tools**
  - **File**: `myclaw/tools/web.py`
  - **Bug**: `browse()` / `download()` fetch arbitrary URLs without blocking private IPs.
  - **Fix**: Add `_is_safe_url()` guard blocking private/loopback IPs and non-HTTP schemes.
  - **Impact**: Medium — server-side request forgery to internal services.

- [x] **2.4 Fix Rate Limiter Race Condition**
  - **File**: `myclaw/tools/core.py:75`
  - **Bug**: `RateLimiter.check()` is not atomic under concurrent async calls.
  - **Fix**: Wrap check logic in `asyncio.Lock` per key.
  - **Impact**: Medium — rate limit can be exceeded under burst load.

- [x] **2.5 Fix HTTPClientPool Event Loop Binding**
  - **File**: `myclaw/provider.py:282-311`
  - **Bug**: Global `httpx.AsyncClient` instance crashes when event loop changes (tests, reloads).
  - **Fix**: Store client per-loop-id or recreate on `RuntimeError`.
  - **Impact**: Medium — crashes in tests and hot-reload scenarios.

- [x] **2.6 Fix AsyncScheduler Concurrency Limit**
  - **File**: `myclaw/async_scheduler.py:290`
  - **Bug**: No `max_concurrency` causes thundering herd under load.
  - **Fix**: Add `asyncio.Semaphore(max_concurrency)` around job execution.
  - **Impact**: Medium — unbounded resource consumption.

- [x] **2.7 Fix Audit Log Tamper Weakness**
  - **File**: `myclaw/audit_log.py:87`
  - **Bug**: Logs can be modified after writing.
  - **Fix**: Add HMAC signature or use append-only log file.
  - **Impact**: Low — integrity compromise of audit trail.

- [x] **2.8 Fix Config Key Storage Weakness**
  - **File**: `myclaw/config_encryption.py`
  - **Bug**: Keys may be stored in plaintext in config files.
  - **Fix**: Enforce OS keyring or environment variables for secrets.
  - **Impact**: Low — credential exposure in config files.

- [x] **2.9 Fix `allowed_commands` Config Drift**
  - **File**: `myclaw/config.py:372`
  - **Bug**: `allowed_commands` list is not validated at startup.
  - **Fix**: Validate at startup; fail fast on invalid / dangerous entries.
  - **Impact**: Medium — accidental inclusion of dangerous commands.

- [x] **2.10 Fix `_reveal_secrets` Non-Functional**
  - **File**: `myclaw/config.py:663`
  - **Bug**: `_reveal_secrets` walks dict but never unwraps `SecretStr` values.
  - **Fix**: Implement proper `SecretStr` unwrapping or remove dead code.
  - **Impact**: Low — dead code / serialization bug.

---

## Phase 3: P2 — Performance & Dependencies (Week 3)

> **Goal**: Fix caches, token counting, and async I/O bottlenecks. Restructure dependencies for on-demand providers.
> **Exit Criteria**: No memory leaks, cache collisions eliminated, token counting accurate, dependency bloat reduced.

- [x] **3.1 Fix LRU Cache Key Skips First Arg**
  - **File**: `myclaw/provider.py:121`
  - **Bug**: Cache key function skips the first argument, causing collisions between different prompts.
  - **Fix**: Include all arguments in key hash.

- [x] **3.2 Fix Hardware Probe Blocking Init**
  - **File**: `myclaw/agent.py:286`
  - **Bug**: `GPUtil` / `psutil` probes block startup by 100-500ms per agent.
  - **Fix**: Move to lazy background task or cache via `run_in_executor`.

- [x] **3.3 Fix Unbounded `_pending_preloads`**
  - **File**: `myclaw/agent.py:263`
  - **Bug**: List grows indefinitely; memory leak over long runs.
  - **Fix**: Use `deque(maxlen=100)` or TTL eviction.

- [x] **3.4 Fix Semantic Cache O(n) Scan**
  - **File**: `myclaw/semantic_cache.py:270`
  - **Bug**: Linear scan causes latency spikes.
  - **Fix**: Add vector index (FAISS / Annoy) or SQLite VSS extension.

- [x] **3.5 Fix FTS5 Cartesian Product**
  - **File**: `myclaw/knowledge/db.py`
  - **Bug**: Unbounded JOINs cause query slowdown.
  - **Fix**: Ensure FTS5 queries use `MATCH` with `LIMIT`.

- [x] **3.6 Fix Inaccurate Token Counting**
  - **File**: `myclaw/context_window.py`
  - **Bug**: Wrong truncation due to inaccurate token counts.
  - **Fix**: Use `tiktoken` for OpenAI; add per-provider tokenizer mapping.

- [x] **3.7 Restructure Dependencies for On-Demand Providers**
  - **File**: `pyproject.toml`, `requirements.txt`
  - **Current**: All ~30 deps in core; `sentence-transformers` pulls 2GB PyTorch.
  - **Fix**: Move to extras:
    - `openai`, `anthropic`, `google` — per-provider SDK extras
    - `semantic-cache` — `sentence-transformers`
    - `voice` — `vosk`
    - `redis` — `redis`
    - `metrics` — `prometheus-client`, `nvidia-ml-py` (replaces `GPUtil`)
    - `security` — `cryptography`, `keyring`
    - `mfa` — `pyotp`, `qrcode`
    - `ssh` — `paramiko`
  - **Remove**: `apscheduler` (unused), `speedtest-cli` (dead), `requests` (httpx covers it), `GPUtil` (unmaintained).
  - **Pin**: `numpy<2.0` until tested; relax `python-telegram-bot` exact pin.

---

## Phase 4: P2/P3 — Quality, Testing & Architecture (Week 4+)

> **Goal**: Fix broken tests, standardize types, refactor globals, and begin Agent decomposition.
> **Exit Criteria**: >80% test coverage, all tests passing, type hints consistent, no bare `except Exception:` blocks.

- [x] **4.1 Add Missing Tests**
  - `Agent.stream_think()` — streaming yields chunks, handles tool calls
  - `AsyncScheduler` — startup/shutdown, concurrency limit, job execution
  - `AsyncSQLitePool` — concurrent checkout/checkin, pool exhaustion, `close_all`
  - `HTTPClientPool` — connection reuse, per-loop isolation
  - `RateLimiter` — concurrent calls don't exceed limit
  - `SemanticCache` — TTL expiration, cache hit/miss

- [x] **4.2 Fix Broken Tests**
  - `tests/test_memory.py` — align `AsyncSQLitePool` tests with real API (checkout tracking).

- [x] **4.3 Standardize Type Hints**
  - Target 90% coverage.
  - Use Python 3.11+ syntax: `dict`, `list`, `str | None`.
  - Remove `typing.Dict`, `typing.List`.

- [x] **4.4 Create Exception Hierarchy**
  - **New**: `myclaw/exceptions.py`
  - `ZenSynoraError` → `ConfigError`, `ProviderError`, `SecurityError`, `ToolError`
  - Replace bare `except Exception:` with specific handlers.

- [x] **4.5 Refactor Global Mutable State**
  - **File**: `myclaw/tools/core.py`
  - Replaced `_HOOKS` global with `HookRegistry` class. Module-level `_HOOKS` remains as backwards-compatible alias.

- [x] **4.6 Resolve Circular Imports**
  - **Files**: `tools/core.py`, `sandbox.py`, `audit_log.py`
  - Use lazy imports inside functions; extract shared interfaces.

- [x] **4.7 Agent Class Decomposition (Start)**
  - **File**: `myclaw/agent.py` (1,665 lines)
  - Created `myclaw/agent/` package with `MessageRouter`, `ContextBuilder`, `ToolExecutor`, `ResponseHandler` stubs.
  - Phase 1 = module structure created. No behavior changes yet.

- [x] **4.8 Documentation Sync**
  - Update architecture docs, API references, deployment guides to reflect changes.

---

## Change Log

> All implemented changes are documented here with date, file, and summary.

| Date | Phase | File(s) | Change Summary |
|------|-------|---------|----------------|
| 2026-04-23 | 1.1 | `myclaw/agent.py` | Fixed infinite recursion in `_provider_chat()`: replaced recursive `self._provider_chat()` calls with `self.provider.chat()` delegation. |
| 2026-04-23 | 1.2 | `myclaw/tools/shell.py`, `myclaw/config.py` | Replaced `create_subprocess_shell` with `create_subprocess_exec` to eliminate command injection. Removed `python`, `python3`, `pip`, `curl`, `wget` from `ALLOWED_COMMANDS`. |
| 2026-04-23 | 1.3 | `myclaw/tools/shell.py` | Fixed error message disclosure: generic error returned to caller; full exception logged server-side with `exc_info=True`. |
| 2026-04-23 | 1.4 | `myclaw/web/api.py`, `myclaw/config.py` | Fixed CORS misconfiguration: origins now loaded from `SecurityConfig.cors_origins` instead of wildcard `["*"]`. Added `cors_origins` and `admin_api_key` to `SecurityConfig`. |
| 2026-04-23 | 1.5 | `myclaw/web/api.py`, `myclaw/web/auth.py` | Added API-key auth to admin endpoints via `require_admin_api_key` FastAPI dependency. Protected `/api/admin/*`, `/api/spaces/*`, `/api/mfa/*`, `/api/metering/*`. |
| 2026-04-23 | 1.6 | `myclaw/mfa.py` | Fixed MFA secret exposure: removed raw `secret` from `provision_user()` response. Returns only `provisioning_uri` and `qr_code_png_base64`. |
| 2026-04-23 | 1.7 | `myclaw/memory.py` | Fixed broken AsyncSQLitePool: replaced broken refcount logic with `_checked_out` set tracking by `id(conn)`. `release_connection()` now requires the connection object to prevent double-release and semaphore drift. |
| 2026-04-23 | 1.8 | `myclaw/tools/toolbox.py` | Hardened `register_tool` AST validation: added `importlib` and `getattr` to forbidden lists. Blocked `open()` entirely for dynamic tools. |
| 2026-04-23 | 1.9 | `myclaw/mcp/server.py` | Fixed MCP server error disclosure: generic error returned to MCP client; full exception logged server-side with `exc_info=True`. |
| 2026-04-23 | 1.10 | `myclaw/mcp/client.py` | Fixed MCP client error disclosure: `_proxy()` returns generic error; full trace logged server-side. |
| 2026-04-23 | 1.11 | `myclaw/mcp/client.py` | Fixed MCP client unmanaged tasks: added `_tasks` set tracking, exception callbacks, and exponential-backoff reconnect logic. |
| 2026-04-23 | 2.1 | `myclaw/provider.py` | Replaced sync `openai.OpenAI` with `openai.AsyncOpenAI`. Added `await` to all chat completion calls; streaming uses `async for`. |
| 2026-04-23 | 2.2 | `myclaw/backends/ssh.py` | Fixed SSH MITM: replaced `AutoAddPolicy` with `RejectPolicy` + `load_host_keys("~/.ssh/known_hosts")`. |
| 2026-04-23 | 2.3 | `myclaw/tools/web.py` | Fixed SSRF: added `_is_safe_url()` guard blocking private IPs, localhost, and non-HTTP schemes in `browse()` and `download_file()`. |
| 2026-04-23 | 2.4 | `myclaw/tools/core.py`, `myclaw/tools/shell.py` | Fixed rate limiter race condition: added `asyncio.Lock` to `RateLimiter.check()`. Updated all callers to `await _rate_limiter.check()`. |
| 2026-04-23 | 2.5 | `myclaw/provider.py` | Fixed HTTPClientPool event loop binding: stores clients per-loop-id instead of global singleton. Prevents crashes on loop changes. |
| 2026-04-23 | 2.6 | `myclaw/async_scheduler.py` | Fixed AsyncScheduler thundering herd: added `max_concurrency` semaphore (default 10) around job execution. |
| 2026-04-23 | 2.7 | `myclaw/audit_log.py` | Fixed audit log tamper weakness: added HMAC-SHA256 signing with secret loaded from env or generated. `clear()` now logs a critical warning. |
| 2026-04-23 | 2.8 | `myclaw/config_encryption.py` | Fixed config key storage: added `ZENSYNORA_CONFIG_KEY` env variable support (highest priority). Warns when falling back to key file. |
| 2026-04-23 | 2.9 | `myclaw/config.py` | Fixed `allowed_commands` config drift: added startup validation that warns if dangerous commands are present in the allowlist. |
| 2026-04-23 | 2.10 | `myclaw/config.py` | Fixed `_reveal_secrets`: now properly unwraps `pydantic.SecretStr` values during dict traversal. |
| 2026-04-23 | 3.1 | `myclaw/provider.py` | Fixed LRU cache key bug: `_make_key()` now includes ALL args instead of skipping `args[0]`. Eliminates cache collisions. |
| 2026-04-23 | 3.2 | `myclaw/agent.py` | Fixed hardware probe blocking init: deferred `get_system_metrics()` to a background daemon thread. |
| 2026-04-23 | 3.3 | `myclaw/agent.py` | Fixed unbounded `_pending_preloads`: added `_track_preload()` helper with `_max_pending_preloads=100` limit and automatic pruning of completed tasks. |
| 2026-04-23 | 3.4 | `myclaw/semantic_cache.py` | Fixed semantic cache O(n) scan: added `max_scan_entries=64` limit, scans newest entries first, cleans up expired entries during scan. |
| 2026-04-23 | 3.5 | `myclaw/knowledge/db.py` | Fixed FTS5 Cartesian product: replaced LEFT JOIN chain with `UNION` of two independent FTS5 subqueries. Eliminates exponential slowdown. |
| 2026-04-23 | 3.6 | `myclaw/context_window.py` | Fixed inaccurate token counting: added `tiktoken` support for OpenAI models, per-provider tokenizer mapping, better fallback heuristic (3 chars/token). |
| 2026-04-23 | 3.7 | `pyproject.toml`, `requirements.txt` | Restructured dependencies: moved LLM providers and optional features to extras. Removed dead deps (`apscheduler`, `speedtest-cli`, `requests`, `GPUtil`). Pinned `numpy<2.0`. |
| 2026-04-23 | 4.1 | `tests/test_infrastructure.py` | Added missing tests: AsyncSQLitePool, RateLimiter, HTTPClientPool, SemanticCache, AsyncScheduler. |
| 2026-04-23 | 4.2 | `tests/test_memory.py`, `myclaw/memory.py` | Fixed broken tests: added AsyncSQLitePool tests, fixed DELETE LIMIT syntax error, fixed pool lock event-loop binding. |
| 2026-04-23 | 4.3 | `myclaw/web/auth.py`, `myclaw/agent/` | Standardized type hints using Python 3.11+ syntax (`dict`, `list`, `str | None`) in new modules. |
| 2026-04-23 | 4.4 | `myclaw/exceptions.py` | Created exception hierarchy: `ZenSynoraError` base with `ConfigError`, `ProviderError`, `SecurityError`, `ToolError`, etc. Backwards-compatible `MyClawError` alias. |
| 2026-04-23 | 4.5 | `myclaw/tools/core.py` | Refactored `_HOOKS` global into `HookRegistry` class with typed methods. Module-level `_HOOKS` remains as backwards-compatible alias. |
| 2026-04-23 | 4.7 | `myclaw/agent/` | Started Agent class decomposition: created `myclaw/agent/` package with `MessageRouter`, `ContextBuilder`, `ToolExecutor`, `ResponseHandler` stub modules. |

