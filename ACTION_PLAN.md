# ZenSynora Action Plan

> **Generated**: 2026-04-23
> **Based on**: Comprehensive codebase audit (v0.4.1)
> **Goal**: Stabilize, secure, and harden the ZenSynora codebase
> **Status**: Phase 1 & 2 critical fixes implemented (2026-04-23). Phase 3+ pending.

---

## Legend

- [ ] Pending
- [~] In Progress
- [x] Done

---

## Phase 1: P0 -- Stop Crashes and Breaches (Week 1)

> **Goal**: Fix crashes, security breaches, and broken connection pools.
> **Exit Criteria**: App runs without infinite loops, shell is sanitized, admin endpoints gated.

- [x] **1.1 Fix Infinite Recursion in agent.py**
  - **File**: `myclaw/agent.py:575-601`
  - **Bug**: `_provider_chat()` calls itself recursively instead of delegating to `self.provider.chat()`.
  - **Fix**: Replace `await self._provider_chat(...)` with `await self.provider.chat(...)` on lines 583 and 586.
  - **Validation**: Mock `provider.chat` raising `ConnectionError`; assert fallback without `RecursionError`.
  - **Effort**: 15 min | **Risk**: Minimal

- [x] **1.2 Fix Shell Command Injection (newline bypass)**
  - **File**: `myclaw/tools/shell.py:65`, `myclaw/backends/*.py`
  - **Bug**: `asyncio.create_subprocess_shell(cmd, ...)` passes raw command string to `/bin/sh -c`. Newlines bypass the regex.
  - **Fix**: Replace with `create_subprocess_exec(*parts, ...)` across all backend files. Add newline to dangerous regex.
  - **Validation**: Assert newline-separated commands return error.
  - **Effort**: 45 min | **Risk**: Low

- [x] **1.3 Fix Error Message Disclosure**
  - **File**: `myclaw/tools/shell.py:88`, `myclaw/backends/ssh.py`, `myclaw/mcp/*.py`
  - **Bug**: `return f"Error: {e}"` leaks internal exception strings to LLM/user.
  - **Fix**: Return generic error message; log full trace server-side with `exc_info=True`.
  - **Validation**: Verify no internal paths or system info in error responses.
  - **Effort**: 30 min | **Risk**: Minimal

- [x] **1.4 Fix CORS + Credentials Misconfiguration**
  - **File**: `myclaw/web/api.py:73-79`
  - **Bug**: `allow_origins=["*"]` with `allow_credentials=True` allows any website to make authenticated requests.
  - **Fix**: Restrict origins via `ZENSYNORA_CORS_ORIGINS` env var or config. Default to `http://localhost:5173`.
  - **Validation**: Verify preflight from unknown origin is rejected.
  - **Effort**: 15 min | **Risk**: Minimal

- [x] **1.5 Add API-Key Auth to Admin Endpoints**
  - **File**: `myclaw/web/api.py:108-201`
  - **Bug**: `/api/admin/*`, `/api/metering/*`, `/api/mfa/*`, `/api/spaces/*` have zero authentication.
  - **Fix**: Implement `require_auth` FastAPI dependency using HTTPBearer. Add `security.api_key: SecretStr` to config.
  - **Validation**: Integration tests asserting 401 on all admin endpoints without valid key.
  - **Effort**: 2-4 hours | **Risk**: Medium

- [x] **1.6 Fix MFA Secret Exposure**
  - **File**: `myclaw/web/api.py:154-160`
  - **Bug**: `mfa_setup` returns raw TOTP secret in response payload without authentication.
  - **Fix**: Require auth; return only provisioning URI and QR code. Never expose `secret` plaintext.
  - **Validation**: Verify response does not contain `secret` field.
  - **Effort**: 30 min | **Risk**: Low

- [x] **1.7 Fix Broken AsyncSQLitePool**
  - **File**: `myclaw/memory.py:91-160`
  - **Bug**: Pool returns the same connection to all concurrent acquirers. No checkout tracking causes DB lock errors.
  - **Fix**: Add `_checked_out` set per pool key. Update `release_connection` signature to require `conn` param.
  - **Validation**: Concurrent `get_connection()` calls return distinct connection objects.
  - **Effort**: 1 hour | **Risk**: Medium (signature change)

- [ ] **1.8 Harden register_tool AST Validation**
  - **File**: `myclaw/tools/toolbox.py:99-156`
  - **Bug**: AST bypass possible via `builtins.__import__`, `pathlib`, `getattr`, dynamic `importlib`, or variable-mode `open()`.
  - **Fix**: Add `builtins`, `pathlib`, `ctypes`, `winreg`, `importlib` to forbidden imports. Block `getattr` entirely. Block `open()` for dynamic tools.
  - **Validation**: Verify `builtins.__import__` and `pathlib.Path` are rejected.
  - **Effort**: 1-2 hours | **Risk**: Low

---

## Phase 2: P1 -- Security Hardening and Async Migration (Week 2)

> **Goal**: Patch OWASP-class vulnerabilities and replace blocking sync calls.
> **Exit Criteria**: No OWASP-class vulns, no blocking sync calls in hot paths, SSH/SSRF hardened.

- [x] **2.1 Replace Sync OpenAI Client with Async**
  - **File**: `myclaw/provider.py:601-735`
  - **Bug**: Uses sync `openai.OpenAI` inside `async def chat()`. Streaming `for chunk in response:` blocks the event loop.
  - **Fix**: Use `openai.AsyncOpenAI`; wrap streaming with `async for chunk in response`.
  - **Impact**: OpenAI, Groq, LMStudio, llama.cpp, OpenRouter providers.
  - **Validation**: Verify concurrent requests do not block each other.
  - **Effort**: 2-3 hours | **Risk**: Medium

- [x] **2.2 Fix SSH MITM (AutoAddPolicy)**
  - **File**: `myclaw/backends/ssh.py:42`
  - **Bug**: `paramiko.AutoAddPolicy()` accepts any host key.
  - **Fix**: Use `RejectPolicy()` + `load_host_keys("~/.ssh/known_hosts")`.
  - **Validation**: Verify connection to unknown host is rejected.
  - **Effort**: 30 min | **Risk**: Low

- [x] **2.3 Fix SSRF in Web Tools**
  - **File**: `myclaw/tools/web.py`
  - **Bug**: `browse()` / `download_file()` fetch arbitrary URLs without blocking private IPs.
  - **Fix**: Add `_is_safe_url()` guard blocking localhost, 169.254.169.254, RFC1918 nets, and non-HTTP schemes.
  - **Validation**: Assert internal metadata endpoints return error.
  - **Effort**: 45 min | **Risk**: Low

- [x] **2.4 Fix Rate Limiter Race Condition**
  - **File**: `myclaw/tools/core.py:55-117`
  - **Bug**: `RateLimiter.check()` is not atomic under concurrent async calls. Dead code duplication at lines 113-116.
  - **Fix**: Add `self._lock = threading.Lock()`; wrap check logic with `with self._lock:`. Remove duplicate code block.
  - **Validation**: Concurrent tool execution must not exceed rate limit.
  - **Effort**: 15 min | **Risk**: Minimal

- [x] **2.5 Fix HTTPClientPool Event Loop Binding**
  - **File**: `myclaw/provider.py:282-311`
  - **Bug**: Global `httpx.AsyncClient` instance crashes when event loop changes (tests, reloads).
  - **Fix**: Store client per-loop-id or recreate on `RuntimeError`.
  - **Validation**: No crashes during hot-reload or test loop changes.
  - **Effort**: 30 min | **Risk**: Low

- [x] **2.6 Fix AsyncScheduler Concurrency Limit**
  - **File**: `myclaw/async_scheduler.py:290`
  - **Bug**: `asyncio.gather(*[self._execute_job(job) for job in due_jobs])` runs all due jobs simultaneously.
  - **Fix**: Add `asyncio.Semaphore(5)` around job execution.
  - **Validation**: 100+ due jobs do not overwhelm the system.
  - **Effort**: 15 min | **Risk**: Minimal

- [x] **2.7 Fix Audit Log Tamper Weakness**
  - **File**: `myclaw/audit_log.py:87`
  - **Bug**: `clear()` unlinks the log file without authorization check or audit trail.
  - **Fix**: Add HMAC signature or use append-only log file. Require authorization for `clear()`.
  - **Validation**: Verify log integrity after rotation and clear attempts.
  - **Effort**: 1 hour | **Risk**: Low

- [x] **2.8 Fix Config Key Storage Weakness**
  - **File**: `myclaw/config_encryption.py`
  - **Bug**: Keys may be stored in plaintext in config files.
  - **Fix**: Enforce OS keyring or `ZENSYNORA_CONFIG_KEY` environment variable for secrets.
  - **Validation**: Verify key is not written to disk in plaintext.
  - **Effort**: 1 hour | **Risk**: Low

- [x] **2.9 Fix allowed_commands Config Drift**
  - **File**: `myclaw/config.py:369-372`
  - **Bug**: `python`, `python3`, `pip` still in `SecurityConfig.allowed_commands` defaults despite Phase 1.1 removal comments.
  - **Fix**: Remove python, python3, pip from defaults. Add startup validation.
  - **Validation**: Assert `shell("python --version")` returns error.
  - **Effort**: 5 min | **Risk**: Minimal

- [x] **2.10 Fix _reveal_secrets Non-Functional**
  - **File**: `myclaw/config.py:663-671`
  - **Bug**: `_reveal_secrets` walks dict but never unwraps `SecretStr` values.
  - **Fix**: Add `elif isinstance(v, SecretStr): out[k] = v.get_secret_value()`.
  - **Validation**: Round-trip config save/load preserves secret values.
  - **Effort**: 10 min | **Risk**: Minimal

- [x] **2.11 Fix F-string Nested Double Quotes**
  - **File**: `myclaw/agent.py:1241`
  - **Bug**: `f"Tool {r["tool_name"]}..."` requires Python 3.12+ PEP 701. Project supports 3.11+.
  - **Fix**: Use single quotes: `f"Tool {r['tool_name']}..."`.
  - **Validation**: Syntax check passes on Python 3.11.
  - **Effort**: 1 min | **Risk**: Minimal

---

## Phase 3: P2 -- Performance and Dependencies (Week 3)

> **Goal**: Fix caches, token counting, and async I/O bottlenecks. Restructure dependencies.
> **Exit Criteria**: No memory leaks, cache collisions eliminated, token counting accurate, dependency bloat reduced.

- [ ] **3.1 Fix LRU Cache Key Skips First Arg**
  - **File**: `myclaw/provider.py:105-136`
  - **Bug**: `_make_key` skips `args[0]` assuming it is `self`, causing cache collisions.
  - **Fix**: Hash all args including args[0], or use `hash((args, tuple(sorted(kwargs.items()))))`.
  - **Validation**: Different prompts produce different cache keys.
  - **Effort**: 15 min | **Risk**: Minimal

- [ ] **3.2 Fix Hardware Probe Blocking Init**
  - **File**: `myclaw/agent.py:286-294`
  - **Bug**: `GPUtil` / `psutil` probes block startup by 100-500ms per agent.
  - **Fix**: Move to async background task via `asyncio.to_thread()` or lazy property.
  - **Validation**: Agent instantiation completes in under 50ms.
  - **Effort**: 30 min | **Risk**: Low

- [ ] **3.3 Fix Unbounded _pending_preloads**
  - **File**: `myclaw/agent.py:263`
  - **Bug**: Fire-and-forget tasks stored in set grow indefinitely; memory leak over long runs.
  - **Fix**: Cap set size at 50-100; prune completed tasks aggressively.
  - **Validation**: Memory usage stable after 1000+ chat cycles.
  - **Effort**: 15 min | **Risk**: Minimal

- [ ] **3.4 Fix Semantic Cache O(n) Scan**
  - **File**: `myclaw/semantic_cache.py:270`
  - **Bug**: Linear scan over all entries causes latency spikes with 256+ entries.
  - **Fix**: Add model-aware cache key. Consider FAISS/SQLite VSS for vector search.
  - **Validation**: Cache lookup under 10ms with 500 entries.
  - **Effort**: 2 hours | **Risk**: Low

- [ ] **3.5 Fix FTS5 Cartesian Product**
  - **File**: `myclaw/knowledge/db.py`
  - **Bug**: Unbounded JOINs across FTS5 tables cause query slowdown.
  - **Fix**: Use UNION of independent FTS5 subqueries with LIMIT.
  - **Validation**: Query completes in under 100ms with 10k observations.
  - **Effort**: 1 hour | **Risk**: Low

- [ ] **3.6 Fix Inaccurate Token Counting**
  - **File**: `myclaw/context_window.py`
  - **Bug**: `len(text) // 4` heuristic is wrong for most tokenizers.
  - **Fix**: Add `tiktoken` for OpenAI models; per-provider tokenizer mapping; better fallback.
  - **Validation**: Token count within 5% of actual for known models.
  - **Effort**: 30 min | **Risk**: Low

- [ ] **3.7 Fix Memory History Cache Never Expires**
  - **File**: `myclaw/memory.py:264, 400-404`
  - **Bug**: `get_history()` caches results with no TTL. Stale data returned after external writes.
  - **Fix**: Add 5-second TTL to cached entries.
  - **Validation**: External DB write reflected within 5 seconds.
  - **Effort**: 15 min | **Risk**: Minimal

- [ ] **3.8 Fix Gateway Sync Cleanup in Async Context**
  - **File**: `myclaw/gateway.py:172-193`
  - **Bug**: `finally` block calls `asyncio.get_event_loop()` and `asyncio.run()` after loop may be closed.
  - **Fix**: Make `start()` async; use `await _sched.shutdown()` in `finally`.
  - **Validation**: Graceful shutdown without RuntimeError.
  - **Effort**: 1 hour | **Risk**: Medium

- [ ] **3.9 Restructure Dependencies for On-Demand Providers**
  - **File**: `pyproject.toml`, `requirements.txt`
  - **Current**: All ~30 deps in core; `sentence-transformers` pulls 2GB PyTorch.
  - **Fix**: Move to extras:
    - `openai`, `anthropic`, `google-generativeai` -- per-provider SDK extras
    - `semantic-cache` -- `sentence-transformers`
    - `voice` -- `vosk`
    - `redis` -- `redis`
    - `metrics` -- `prometheus-client`, `nvidia-ml-py` (replaces `GPUtil`)
    - `security` -- `cryptography`, `keyring`
    - `mfa` -- `pyotp`, `qrcode`
    - `ssh` -- `paramiko` (already in core, keep)
  - **Remove**: `apscheduler` (unused), `speedtest-cli` (dead), `requests` (httpx covers it), `GPUtil` (unmaintained).
  - **Pin**: `numpy<2.0` until tested; relax `python-telegram-bot` exact pin.
  - **Validation**: `pip install zensynora` installs only core deps under 50MB.
  - **Effort**: 1 hour | **Risk**: Medium (users must update install commands)

---

## Phase 4: P2/P3 -- Quality, Testing and Architecture (Week 4+)

> **Goal**: Fix broken tests, standardize types, refactor globals, begin Agent decomposition.
> **Exit Criteria**: All tests passing, type hints consistent, no bare `except Exception:` blocks.

- [ ] **4.1 Add Missing Tests**
  - `Agent.stream_think()` -- streaming yields chunks, handles tool calls, no RecursionError
  - `AsyncScheduler` -- startup/shutdown, concurrency limit, job execution, persistence
  - `AsyncSQLitePool` -- concurrent checkout/checkin, pool exhaustion, distinct connections
  - `HTTPClientPool` -- connection reuse, per-loop isolation, no crash on loop change
  - `StateStore` -- Redis backend basic ops, fallback to memory
  - `RateLimiter` -- concurrent calls do not exceed limit
  - `SemanticCache` -- TTL expiration, cache hit/miss, model isolation
  - `SecuritySandbox` -- escape vector resistance (subprocess, file, network blocking)
  - **Effort**: 4 hours | **Risk**: Low

- [ ] **4.2 Fix Broken Tests**
  - **File**: `tests/test_memory_pool_concurrency.py`, `tests/test_memory_batching.py`
  - **Bug**: Tests call `AsyncSQLitePool(max_connections=3)` which does not exist. `release_connection` signature mismatch.
  - **Fix**: Update tests to match actual API or remove if testing implementation detail.
  - **Effort**: 30 min | **Risk**: None

- [ ] **4.3 Standardize Type Hints**
  - Target 90% coverage.
  - Use Python 3.11+ syntax: `dict[str, ...]`, `list[str]`, `str | None`.
  - Remove `typing.Dict`, `typing.List`, `typing.Optional`.
  - **Effort**: 2 hours | **Risk**: Minimal

- [ ] **4.4 Create Exception Hierarchy**
  - **New**: `myclaw/exceptions.py`
  - `ZenSynoraError` base with `ConfigError`, `ProviderError`, `SecurityError`, `ToolError`, `SandboxError`, `RateLimitError`.
  - Replace bare `except Exception:` with specific handlers.
  - **Effort**: 2 hours | **Risk**: Low

- [ ] **4.5 Refactor Global Mutable State**
  - **File**: `myclaw/tools/core.py`
  - Replace `_HOOKS`, `TOOLS`, `_agent_registry` globals with registry classes or dependency injection.
  - Maintain backward-compatible module-level aliases.
  - **Effort**: 3 hours | **Risk**: Medium

- [ ] **4.6 Resolve Circular Imports**
  - **Files**: `tools/core.py`, `sandbox.py`, `worker_pool.py`, `audit_log.py`
  - Use lazy imports inside functions; extract shared interfaces to `myclaw/interfaces.py`.
  - **Effort**: 2 hours | **Risk**: Medium

- [ ] **4.7 Fix State Store Singleton Race Condition**
  - **File**: `myclaw/state_store.py:349-376`
  - **Bug**: `_Store_LOCK = False` is boolean, not `threading.Lock()`.
  - **Fix**: `import threading; _Store_LOCK = threading.Lock()`; use `with _Store_LOCK:`.
  - **Effort**: 10 min | **Risk**: Minimal

- [ ] **4.8 Fix Sandbox Escape Vectors**
  - **File**: `myclaw/sandbox.py:266-314`
  - **Bug**: `os` imported before restrictions; `socket.socket` never replaced; `SandboxedImporter` never installed in `sys.meta_path`; no Windows resource limits.
  - **Fix**: Remove dangerous modules from `sys.modules` before user code. Block `os.system`/`os.popen`. Install importer in `sys.meta_path`. Consider Docker for true isolation.
  - **Effort**: 2-4 hours | **Risk**: Medium

- [ ] **4.9 Agent Class Decomposition (Start)**
  - **File**: `myclaw/agent.py` (1,665 lines)
  - Split into `myclaw/agent/` package: `MessageRouter`, `ContextBuilder`, `ToolExecutor`, `ResponseHandler`.
  - Phase 1 = create module stubs and migrate docstrings. No behavior changes.
  - **Effort**: 4 hours | **Risk**: High (architectural)

- [ ] **4.10 Documentation Sync**
  - Update README, architecture docs, API references to reflect:
    - New auth requirements (admin_api_key)
    - Optional dependency installation (`pip install zensynora[openai,redis]`)
    - CORS origin configuration
    - SSH known_hosts requirement
  - **Effort**: 2 hours | **Risk**: Minimal

---

## File-by-File Implementation Guide

| File | Issues | Changes | Effort | Tests |
|------|--------|---------|--------|-------|
| `myclaw/agent.py` | P0.1, P1.8, P2.2, P2.3, P3.2 | Fix recursion, f-string quotes, defer hardware probe, cap preloads | 1.5h | Add stream_think test |
| `myclaw/provider.py` | P1.1, P2.5, P3.1 | AsyncOpenAI, loop-safe client pool, fix cache key | 3h | Concurrent request test |
| `myclaw/config.py` | P1.9, P2.9, P2.10 | Remove python/pip from defaults, fix _reveal_secrets, validate commands | 30m | Round-trip secret test |
| `myclaw/tools/core.py` | P1.4, P4.5, P4.7 | Add lock to rate limiter, refactor globals, fix dead code | 1h | Concurrent rate limit test |
| `myclaw/tools/shell.py` | P0.2, P1.3 | create_subprocess_exec, generic errors, newline regex | 45m | Injection attempt test |
| `myclaw/tools/toolbox.py` | P0.4 | Block builtins, pathlib, getattr, open | 1.5h | AST bypass attempt test |
| `myclaw/tools/web.py` | P1.3 | _is_safe_url guard for SSRF | 45m | Internal IP block test |
| `myclaw/web/api.py` | P0.3, P1.4, P1.5 | require_auth dependency, cors_origins config | 3h | 401 integration tests |
| `myclaw/web/auth.py` | P0.3, P1.5 | NEW: HTTPBearer auth middleware | 1h | Token validation test |
| `myclaw/sandbox.py` | P1.12, P4.8 | Block os.system, install meta_path importer, Windows limits | 3h | Escape vector tests |
| `myclaw/memory.py` | P0.5, P3.7 | Checkout tracking, TTL cache, fix release signature | 1.5h | Concurrent pool test |
| `myclaw/state_store.py` | P1.13 | threading.Lock singleton | 10m | Concurrent init test |
| `myclaw/async_scheduler.py` | P2.6 | Semaphore concurrency limit | 15m | Job overload test |
| `myclaw/semantic_cache.py` | P2.5, P3.4 | Model-aware keys, scan limit | 2h | TTL/hit-miss tests |
| `myclaw/gateway.py` | P3.8 | Async start(), await shutdown | 1h | Graceful shutdown test |
| `myclaw/backends/ssh.py` | P1.2 | RejectPolicy, known_hosts | 30m | Unknown host reject test |
| `myclaw/audit_log.py` | P2.7 | HMAC signing, auth on clear | 1h | Tamper detection test |
| `myclaw/config_encryption.py` | P2.8 | Env var key priority | 1h | Key storage test |
| `myclaw/context_window.py` | P3.6 | tiktoken integration | 30m | Token accuracy test |
| `myclaw/knowledge/db.py` | P3.5 | UNION instead of JOIN | 1h | Query performance test |
| `pyproject.toml` | P3.9 | Optional extras, remove dead deps | 1h | Install size check |
| `tests/` | P4.1, P4.2 | Add missing tests, fix broken ones | 4h | Full suite pass |

---

## Implementation Roadmap

### Critical Path
```
Week 1: 1.1 (recursion) -> 1.2 (shell) -> 1.5 (auth) -> 1.7 (pool)
Week 2: 2.1 (async client) -> 2.3 (SSRF) -> 2.4 (rate limit)
Week 3: 3.1 (cache key) -> 3.4 (semantic cache) -> 3.9 (deps)
Week 4: 4.1 (tests) -> 4.3 (types) -> 4.5 (globals)
```

### Rollback Procedures
- **Phase 1**: Git revert to HEAD~1 per item. Each P0 fix is isolated.
- **Phase 2**: Provider async migration can be toggled via `USE_ASYNC_OPENAI` env var.
- **Phase 3**: Dependency extras are additive; core deps unchanged.
- **Phase 4**: Agent decomposition is stub-only; no behavioral changes.

### Success Criteria
- [ ] All P0 items verified with unit tests
- [ ] Security scan (bandit, semgrep) passes with zero high/critical findings
- [ ] pytest suite passes with >80% coverage
- [ ] `pip install .` completes in under 30 seconds (core only)
- [ ] Agent responds to chat without RecursionError
- [ ] Admin endpoints return 401 without valid API key
- [ ] Shell tool rejects commands with newlines or shell metacharacters