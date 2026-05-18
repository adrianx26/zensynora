# ZenSynora — Optimization & Improvement Audit

> Generated: 2026-05-17  
> Scope: Full codebase (`myclaw/`, `webui/`, tooling, config)  
> Method: Targeted file inspection across `agent.py`, `provider.py`, `memory.py`, `knowledge/db.py`, `config.py`, `defaults.py`, `tools/shell.py`, `deploy.py`, `async_scheduler.py`, `agent_internals/classes.py`, `task_timer.py`, `.pre-commit-config.yaml`, and related modules

---

## Critical

### 1. `deploy.py` — Hardcoded SSH credentials committed to source tree

**File:** `deploy.py`, lines 23–26

```python
REMOTE_HOST = "192.168.8.110"
REMOTE_USER = "adi"
REMOTE_PASS = "Alpin2003@"
REMOTE_DIR  = "/home/adi/zensynora"
```

Plaintext SSH password and host are committed. Anyone with repo access gets live credentials.

**Fix:** Replace literals with environment variable reads:

```python
REMOTE_HOST = os.environ.get("ZENSYNORA_DEPLOY_HOST", "")
REMOTE_USER = os.environ.get("ZENSYNORA_DEPLOY_USER", "")
REMOTE_PASS = os.environ.get("ZENSYNORA_DEPLOY_PASS", "")
REMOTE_DIR  = os.environ.get("ZENSYNORA_DEPLOY_DIR", "/home/adi/zensynora")
```

Add a pre-commit hook that scans for `= "..."` / `= '...'` patterns on credential-named variables.

---

### 2. `memory.py` — Unbounded `_history_cache` can OOM on long sessions

**File:** `myclaw/memory.py`, ~line 466

`Memory._history_cache` is a plain `dict` keyed by `limit` (an integer). Every distinct `limit` value ever requested becomes a permanent cache entry. A session calling `get_history(limit=10)` then `limit=20` then `limit=50` accumulates entries without bound. The `_cache_max_size` cap uses a simple FIFO eviction that only fires for the default-columns path:

```python
if len(self._history_cache) >= self._cache_max_size:
    oldest_key = next(iter(self._history_cache))
    del self._history_cache[oldest_key]
```

This evicts one entry per overflow call — but never shrinks below the cap, and non-default column queries bypass the cap entirely.

**Fix:** Replace with `collections.OrderedDict` with a strict max-length policy, or use `cachetools.LRUCache`:

```python
from collections import OrderedDict
self._history_cache: OrderedDict[int, list] = OrderedDict()
# On every put:
self._history_cache[cache_key] = result
self._history_cache.move_to_end(cache_key)
if len(self._history_cache) > self._cache_max_size:
    self._history_cache.popitem(last=False)
```

Also: the sub-query `SELECT ... FROM (SELECT ... ORDER BY id DESC LIMIT ?) ORDER BY id ASC` forces SQLite to materialize a full reverse-sorted set before reversing. A primary-key scan `ORDER BY id DESC LIMIT ?` followed by in-Python reversal is cheaper for small `limit` values.

---

### 3. `asyncio.create_task` without exception guards — silent crashes at 27 call sites

**Files across:** `agent.py`, `skill_preloader.py`, `messaging/broker.py`, `agent_internals/tool_executor.py`, `agent_internals/context_builder.py`, `agent_internals/classes.py`, `worker_pool.py`, `gateway.py`, `mcp/client.py`, `async_scheduler.py`, `agents/medic_change_mgmt.py`, `tools/scheduler.py`, `task_timer.py`, `swarm/orchestrator.py`, `swarm/collaboration.py`, `swarm/storage.py`, `knowledge/sync.py`

27 fire-and-forget `asyncio.create_task()` / `loop.create_task()` calls exist. None attach a `.add_done_callback` that logs exceptions. If a background coroutine raises, the asyncio event loop either logs "Task exception was never retrieved" at shutdown (development) or silently discards the error (production with `loop.set_exception_handler` suppressed).

Worst offenders:
- `agent.py` lines 1608, 1615 — `_extract_and_save_knowledge` and `_background_summarize_context` (LLM calls that can raise silently)
- `agent_internals/classes.py` lines 201, 222 — same KB/summarization tasks
- `knowledge/sync.py` lines 349, 356, 360 — background file-watcher extraction tasks

**Fix:** Apply a module-level helper:

```python
def _fire_and_forget(coro, logger, context=""):
    task = asyncio.create_task(coro)
    task.add_done_callback(
        lambda t: logger.error(f"Bg task failed [{context}]", exc_info=t.exception())
        if t.exception() else None
    )
    return task
```

Then replace every raw `asyncio.create_task(coro)` with `_fire_and_forget(coro, logger, "context-name")`.

---

## High Impact

### 4. `re` module recompiled inside hot loops in 11+ files

**Files:** `agent.py`, `shell.py`, `memory.py`, `web_search.py`, `benchmark_runner.py`, `agents/medic_evolver.py`, `agents/medic_change_mgmt.py`, `backends/router.py`, `self_healer.py`, `skill_generator.py`, `agents/skill_adapter.py`, `sandbox.py`

Several files compile regex inline on every call instead of once at module scope:

- **`agent.py` line 796:** `re.sub(r"[^\w\s]", " ", ...)` on every `think()` call  
- **`shell.py` line 52:** `re.compile(r"[\n\r;&|`$(){}\[\]\\]")` once per `shell_async()` call  
- **`memory.py`:** `re.findall(pattern, text)` with uncompiled `pattern` strings in `extract_knowledge_candidates`

**Fix:** Module-level compiled constants:

```python
# agent.py
_TOPIC_SANITIZE_RE = re.compile(r"[^\w\s]")

# shell.py
_DANGEROUS_CHARS_RE = re.compile(r"[\n\r;&|`$(){}\[\]\\]")

# memory.py
_ENTITY_PATTERNS_COMPILED = [re.compile(p) for p in ENTITY_PATTERNS]
```

---

### 5. `agent.py` still 1,630 lines — class decomposition in `agent_internals/classes.py` exists but is unused

Sprint 5 extracted 4 free functions; Sprint 9 wrapped them in thin class adapters (`MessageRouter`, `ContextBuilder`, `ToolExecutor`, `ResponseHandler`). `agent.py` still imports and calls the free functions directly. The class layer was built for DI/testability but `Agent` was never migrated to it.

Migrating `Agent._route_message → MessageRouter.run()`, `_build_context → ContextBuilder.run()`, `_execute_tools → ToolExecutor.run()` would cut `agent.py` by an estimated 40–50% and give every phase an explicit dependency surface for testing.

---

### 6. Semantic cache manual `np.dot` loop — 5–15× slower than batched matmul

**File:** `myclaw/vector/semantic_cache.py` (or equivalent semantic cache module)

The embedding scan iterates over every cached entry individually:

```python
for entry in cache.values():
    score = np.dot(query_vec, entry.embedding)   # O(N) BLAS calls
```

**Fix:** Stack embeddings into a single matrix and call `np.matmul` once:

```python
embeddings = np.stack([e.embedding for e in cache.values()])   # shape (N, dim)
scores = query_vec @ embeddings.T                               # single BLAS call
top_k = np.argpartition(scores, -k)[-k:]
```

Also: `scan_cap` defaults to 64. With a typical cache of 256 entries only 25% are ever scored. Raising the cap to 256 or adding an L2-distance pre-filter (cheap approximate nearest-neighbors) before the dot-product pass would materially improve retrieval quality.

---

### 7. Topic extraction uses `re.sub` on every `think()` call — replace with `str.translate`

**File:** `myclaw/agent.py`, line 796

```python
cleaned = re.sub(r"[^\w\s]", " ", message.lower())
```

This runs on every user message and recompiles the pattern each invocation. `str.translate` with a pre-built translation table runs in C and avoids the regex engine entirely:

```python
# module scope
_TOPIC_TABLE = str.maketrans(
    {chr(c): " " for c in range(128) if not (chr(c).isalnum() or chr(c).isspace())}
)
# per call
cleaned = message.lower().translate(_TOPIC_TABLE)
```

---

## Medium

### 8. Per-user DB pool fragments SQLite write throughput

**File:** `myclaw/memory.py`, `AsyncSQLitePool`

`Memory()` creates a separate `AsyncSQLitePool` (3 connections) per `user_id.db`. Ten active users = 30 connection slots across 10 files. SQLite's WAL writer serializes at the filesystem level, so per-file pool concurrency does not help writes — it only adds connection-table overhead. The per-user read isolation is sound, but the pool size of 3 per DB is over-provisioned for write-heavy workloads.

**Fix:** Limit each pool to 1 connection for writes, 2 for reads. Detect workload type and resize accordingly.

---

### 9. `_history_cache` not thread-safe across sync/async paths

**File:** `myclaw/memory.py`

`Memory._history_cache` is a plain `dict` mutated from `get_history` (async/event-loop path) and from `close()` / `cleanup()` which can be called via `asyncio.to_thread`. The GIL serializes single opcodes but dict resizes are not atomic under `asyncio.to_thread`. In practice rare, but a `threading.Lock` on the cache mutations would eliminate the race.

---

### 10. `AsyncScheduler` JSONL persistence has no file lock

**File:** `myclaw/async_scheduler.py`

Jobs are persisted to `~/.myclaw/async_scheduler_jobs.jsonl` with no `fcntl`/`msvcrt` advisory lock. If two scheduler instances start simultaneously (e.g., systemd `Restart=on-failure` races two starts), the JSONL can be interleaved and corrupted.

**Fix:** Wrap the JSONL file in an `fcntl.flock` context manager on both read and write paths.

---

### 11. Shell regex duplicated between sync and async functions

**File:** `myclaw/tools/shell.py`, lines 52 and 137

Both `shell_async()` and `shell()` compile the same `dangerous` regex on every call. Extract to a module-level constant:

```python
_DANGEROUS_CHARS_RE = re.compile(r"[\n\r;&|`$(){}\[\]\\]")
```

---

### 12. `LRUCacheWithTTL._cleanup_expired` is O(n) on every Nth access

**File:** `myclaw/provider.py`, `LRUCacheWithTTL`

`_cleanup_expired` rebuilds a list of all expired keys and deletes them in a loop. Called amortized on every N accesses, it is fine for `maxsize=128` but stalls for `maxsize=1000+` (possible with `semantic_cache` at scale). Make it lazy — only clean the specific expired key on `.get()` miss, or run a background cleanup task.

---

## Security

### 13. `deploy.py` hardcoded credentials (see #1 above — primary finding)

---

### 14. `config.py` `_apply_env_overrides` — secrets silently accepted with no startup warning

**File:** `myclaw/config.py`, line 212

Environment variable names are logged at INFO level; values are not. That is correct. However, `save_config` writes plaintext secrets to `config.json` when no encryption key is configured, with no warning. If the operator enables `MYCLAW_SQLITE_KEY` after saving plaintext, a stale unencrypted `config.json` remains on disk.

**Fix:** Log a WARNING on first call to `save_config` when encryption is not active and secrets are present.

---

### 15. CORS fallback to dev origin not guarded against empty configuration

**File:** `myclaw/web/api.py`, line 82

```python
_cors_origins = _cors_origins.cors_origins if _cors_origins else ["http://localhost:5173"]
```

If `security.cors_origins` is set to `[]` (empty list) the fallback kicks in to dev-only. If it accidently contains `"*"` the CORS middleware allows any origin with credentials — a known class of attack (any site can steal sessions). Add an explicit validation in `_validate_config`:

```python
if "*" in config.security.cors_origins:
    logger.warning("CORS wildcard with credentials is a security risk")
```

---

## Reliability / Correctness

### 16. `stream_think` word-splitting corrupts markdown / multi-byte output

**File:** `myclaw/agent.py`

```python
words = final_response.split(" ")
for i, word in enumerate(words):
    prefix = " " if i > 0 else ""
    yield prefix + word
```

Splitting on ASCII space breaks code blocks, tables, and multi-byte Unicode strings. If `final_response` contains backtick-delimited code, `"```" ` becomes a separate token, breaking the stream consumer's reconstruction. Use a fixed-size chunk strategy (e.g., 8-character windows with overlap) or stream the full string per character instead of word-splitting.

---

## Developer Experience

### 17. `requirements.txt` absent; `requirements-lock.txt` underutilized

The project ships `requirements-lock.txt` (14 entries, core only). `install.sh` ignores it and installs from `pyproject.toml` editable. The `Dockerfile` installs from `requirements.txt` but the file does not exist — it silently falls through elsewhere. Generate a fully-pinned `requirements.txt` from the existing `uv.lock` and have both `install.sh` and CI use it to pin environments.

---

### 18. No `.env` / `pydantic-settings` schema validation

`.env.example` documents 50+ variables without types. The app reads them via `os.environ.get` + manual Pydantic overrides in `config.py`. Mistyped environment variable names are silently ignored, producing default values at runtime. Adding `pydantic-settings` `BaseSettings` with explicit field types and a startup validation pass would surface misconfigurations with a clear error before the agent starts.

---

### 19. Ruff + Black competing in pre-commit config

**File:** `.pre-commit-config.yaml`

Both `ruff-format` and `black` are configured as formatters. Running both can silently undo each other's formatting — Ruff formats one way, Black reformats on the next hook, or vice-versa. Use one formatter:

- **Recommended:** Remove `black` and keep `ruff-format` — it is faster and already configured.  
- **Alternative:** Remove `ruff-format` and keep `black`, but run `ruff` (linter-only, not `ruff-format`).

---

## Summary

| # | Issue | Severity | Effort |
|---|---|---|---|
| 1 | Hardcoded SSH credentials in `deploy.py` | 🔴 Critical | Trivial |
| 2 | Unbounded `_history_cache` → OOM | 🔴 Critical | Medium |
| 3 | `create_task` without error guards (27 sites) | 🟠 High | Large |
| 4 | Regex recompiled in hot loops (11+ files) | 🟠 High | Medium |
| 5 | `agent.py` 1,630 lines; class adapters unused | 🟠 High | Large |
| 6 | Semantic cache manual `np.dot` loop | 🟡 Medium | Medium |
| 7 | `re.sub` on every `think()` call | 🟡 Medium | Small |
| 8 | Per-user DB pool fragments write throughput | 🟡 Medium | Medium |
| 9 | `_history_cache` no async/sync lock | 🟡 Medium | Small |
| 10 | AsyncScheduler JSONL no file lock | 🟡 Medium | Medium |
| 11 | Shell regex compiled per-call | 🟡 Medium | Small |
| 12 | `LRUCacheWithTTL` O(n) cleanup | 🟡 Medium | Medium |
| 13 | Hardcoded deploy password (duplicate of #1) | 🔴 Critical | Trivial |
| 14 | Secrets silently accepted, no startup warning | 🟡 Medium | Small |
| 15 | CORS fallback to dev origin unguarded | 🟡 Medium | Small |
| 16 | `stream_think` word-splitting corrupts formatting | 🟡 Medium | Medium |
| 17 | `requirements.txt` absent; lock underused | 🟡 Medium | Medium |
| 18 | No `.env` / `pydantic-settings` schema | 🟡 Medium | Large |
| 19 | Ruff + Black competing in pre-commit | 🟢 Low | Trivial |

**Priority order for remediation:**  
1 → 3 → 2 → 4 → 5 → (6, 7, 11 as a group) → (15, 16) → (8, 9, 10, 12, 14, 17, 18, 19)
