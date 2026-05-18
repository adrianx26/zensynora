# Security & Stability Fixes — 2026-04-29 (+ 2026-05-17/18)

> **Audit rounds:** post-0.4.1 critical-bug sweep (04-29) + optimization audit (05-17/18)
> **Scope:** seven defects (04-29) + eight security/performance improvements (05-17/18)
> **Outcome:** all P0 items closed; 18/20 optimization items resolved

This document describes each fix with root cause, exact change, and test plan.
Use it to review the diff or to onboard a new contributor to the touched code.

---

## Fixes from 2026-04-29

### 1. Infinite recursion in `Agent._track_preload`

**File:** `myclaw/agent.py:340-356`
**Severity:** Critical (every preload call crashed)

### Root cause
The method that should record a preload `Task` in `self._pending_preloads`
ended with `self._track_preload(task)` — calling itself unconditionally.
Hits Python's recursion limit (~1000) on the first call and raises
`RecursionError`.

### Fix
```python
# before
self._track_preload(task)

# after
self._pending_preloads.add(task)
```

### Test plan
- Direct unit call must not raise `RecursionError`.
- After filling the set to `_max_pending_preloads`, the next call must keep
  the size at `_max_pending_preloads` (eviction path).
- `agent.think()` integration test must complete without recursion.

---

## 2. Shell injection via newline characters

**Files:** `myclaw/tools/shell.py` — `shell` and `shell_async`
**Severity:** Critical (bypass of dangerous-character regex)

### Root cause
The regex `r"[;&|`$(){}\[\]\\]"` did not include `\n` or `\r`. An attacker
could submit `ls\nwhoami` and pass validation. While `subprocess` is invoked
with `shell=False` so the OS shell does not interpret the newline, the
defensive regex's intent was to block the entire family of injection
characters. In addition, only `parts[0]` was re-validated against the
allow/blocklist — dangerous characters embedded in later arguments were not
re-checked after `shlex.split`.

### Fix
- Added `\n` and `\r` to the dangerous-character class.
- Added a loop after `shlex.split(cmd)` that re-checks every token
  (`parts[1:]`) against the same regex.

```python
dangerous = re.compile(r"[\n\r;&|`$(){}\[\]\\]")
...
parts = shlex.split(cmd)
for part in parts[1:]:
    if dangerous.search(part):
        return "Error: Command contains dangerous characters"
```

### Test plan
- Parametrized tests for `"ls\nwhoami"`, `"ls\rwhoami"`, mixed payloads
  asserting "Error" / "dangerous" in the response.
- Async version covered separately with `pytest.mark.asyncio`.

---

## 3. Missing auth on `/api/v1/keys` endpoints

**File:** `myclaw/api_server.py` — `list_api_keys`, `create_api_key`, `revoke_key`
**Severity:** Critical (unauthenticated admin operations)

### Root cause
Each endpoint declared `api_key: Optional[str] = Depends(...)` and either
performed no permission check at all (`list_api_keys`, `revoke_key`) or
performed one only when `api_key` was truthy (`create_api_key`). Result:
unauthenticated requests succeeded.

### Fix
Introduced a single guard inside `create_app()`:

```python
def _require_admin(api_key: Optional[str]) -> None:
    if not api_key:
        raise HTTPException(status_code=401, detail="Authentication required")
    key_obj = self._api_keys.get(api_key)
    if key_obj is None or "admin" not in key_obj.permissions:
        raise HTTPException(status_code=403, detail="Admin permission required")
```

All three endpoints now call `_require_admin(api_key)` as their first line.
Also fixed the latent `APIKey("", "")` `TypeError` (`created_at` is required)
by removing the buggy default-construction code path entirely.

### Test plan
- `TestClient` GET/POST/DELETE on `/api/v1/keys` without `Authorization`
  header → `401`.
- With a non-admin key → `403`.
- With an admin key → success path returns the expected payload.

---

## 4. CORS misconfiguration

**File:** `myclaw/api_server.py:238-247`
**Severity:** Critical (CSRF from any origin)

### Root cause
`allow_origins=["*"]` combined with `allow_credentials=True` is a textbook
CSRF setup. Browsers will attach the user's cookies/auth headers to
cross-origin requests from any site.

### Fix
- Added `cors_origins: Optional[List[str]]` to `APIServer.__init__` (defaults
  to `["http://localhost:5173"]` for development).
- Replaced `allow_origins=["*"]` with `allow_origins=self._cors_origins`.
- Narrowed `allow_methods` to `["GET","POST","PUT","DELETE","OPTIONS"]` and
  `allow_headers` to `["Authorization","X-API-Key","Content-Type"]`.

### Operator action required
Production deployments **must** pass `cors_origins=config.security.cors_origins`
when constructing `APIServer`. Audit the instantiation site before the next
deploy.

### Test plan
- Preflight from listed origin → `Access-Control-Allow-Origin` echoes the origin.
- Preflight from unlisted origin → header omitted (no `*`).

---

## 5. `AsyncSQLitePool` race condition + unsafe fallback

**File:** `myclaw/memory.py:122-164`
**Severity:** High (silent data corruption under concurrency)

### Root cause
The check-and-create block was correctly guarded by `cls._get_lock()`, but a
fallback at the end of the block returned `pool[0]` unconditionally — even
when that connection was already checked out by another caller. Any code path
that reached the fallback would hand the same `aiosqlite.Connection` to two
coroutines simultaneously, corrupting transactions.

### Fix
Replaced the silent fallback with an explicit `RuntimeError`:

```python
raise RuntimeError(
    f"AsyncSQLitePool invariant violated for {key}: "
    f"semaphore acquired but no free connection available "
    f"(pool={len(pool)}, checked_out={len(checked)})"
)
```

If the semaphore is correct, this branch is unreachable. If the semaphore
ever drifts, the bug now surfaces immediately instead of silently corrupting
data.

### Test plan
- Existing `test_pool_concurrent_access` continues to pass (verifies no
  regression in the happy path).
- Stress test with 50 concurrent acquire/release pairs on a pool of size 3
  — no connection-id is double-checked-out.
- Monkey-patched `_pool_size = 0` test asserting `RuntimeError` is raised
  with the expected message.

---

## 6. AST sandbox bypass in dynamic tools

**File:** `myclaw/tools/toolbox.py` — `register_tool` and `improve_skill`
**Severity:** High (dynamic-tool sandbox escape)

### Root cause
Three independent gaps:

1. `pathlib` was not in `forbidden_imports` for `register_tool`. An agent
   could register a tool that did `Path("/etc/passwd").read_text()`.
2. `improve_skill` had a *different, shorter* forbidden list than
   `register_tool` — missing `importlib`, `pathlib`, and `getattr`. Two code
   paths, two policies, drift over time guaranteed.
3. Native-code escapes (`ctypes`, `cffi`, `mmap`) were not on either list.

### Fix
Centralized validation in a single helper at the top of `toolbox.py`:

```python
_FORBIDDEN_IMPORTS = frozenset({
    "os", "sys", "subprocess", "shutil", "socket", "urllib", "http", "pty",
    "commands", "importlib",
    "pathlib", "ctypes", "cffi", "mmap",   # newly added
})
_FORBIDDEN_CALLS = frozenset({
    "eval", "exec", "__import__", "globals", "locals", "compile", "getattr",
})
_RESTRICTED_CALLS = frozenset({"open"})

def _validate_tool_ast(code: str) -> Optional[str]:
    ...
```

Both `register_tool` and `improve_skill` now do:

```python
ast_error = _validate_tool_ast(code)
if ast_error:
    return ast_error
```

The helper additionally checks `ast.Attribute` nodes so that
`pathlib.Path(...)` (attribute access on a forbidden module name) is rejected
even if the `import pathlib` line slips through unusual aliasing.

### Test plan
- Parametrized tests rejecting: `import pathlib`, `import ctypes`,
  `import importlib`, `__builtins__['eval']`, `getattr(__builtins__, 'eval')`,
  `open(...)`, `eval(...)`, `pathlib.Path(...)`.
- Positive test: a docstring + try/except + `logger.error` tool passes
  validation.

### Note for operators
Existing dynamically registered tools that import `pathlib` (rare) will be
rejected on next reload. Audit `~/.myclaw/toolbox/` before merging.

---

## 7. Broken `stream_chat` across all four providers

**File:** `myclaw/provider.py` — Ollama, OpenAI-compat, Anthropic, Gemini
**Severity:** High (streaming has never worked)

### Root cause
`chat(messages, model, stream=True)` returns a tuple
`(async_generator, tool_calls_collector)`, not an async iterable. The code

```python
async for chunk in await self.chat(messages, model, stream=True):
    yield chunk
```

iterates over the tuple itself, silently yielding the generator object
followed by the empty `tool_calls_collector` list, then terminating. No text
chunks are ever streamed.

### Fix (applied to all four providers)
```python
iterator, _ = await self.chat(messages, model, stream=True)
async for chunk in iterator:
    yield chunk
```

### Test plan
For each provider, mock `chat` to return `(fake_async_generator(), [])` and
assert that `stream_chat` yields the generator's chunks in order.

---

## Files changed

| File | Lines touched | Change |
|---|---|---|
| `myclaw/agent.py` | 1 | Recursion fix |
| `myclaw/tools/shell.py` | ~14 | Newline regex + token re-validation (×2 functions) |
| `myclaw/api_server.py` | ~30 | Auth guards, CORS allow-list, init param |
| `myclaw/memory.py` | ~10 | Replace unsafe fallback with `RuntimeError` |
| `myclaw/tools/toolbox.py` | +95 / −95 | Extract `_validate_tool_ast`, expand forbidden set |
| `myclaw/provider.py` | 4×3 | Destructure `chat(...)` tuple |
| `archive/tools_backup.py` | (deleted) | Dead code removal |

## Files NOT changed (verified already-fixed in this branch)

| Item | Evidence |
|---|---|
| SSH `AutoAddPolicy` | `myclaw/backends/ssh.py:46` already uses `RejectPolicy()` |
| OpenAI sync client in async | `myclaw/provider.py:660,668` already uses `AsyncOpenAI` |
| MFA secret exposure | `myclaw/mfa.py:96-131` already redacts the raw secret |

---

## Fixes from 2026-05-17 (Changeset 1)

### 8. URL injection via string interpolation in web_search.py

**File:** `myclaw/web_search.py` — `search_web`, `search_wikipedia`, `search_news`
**Severity:** Medium (potential URL injection through query parameters)

**Root cause:** URLs were constructed with raw f-strings:
`f"https://html.duckduckgo.com/html/?q={encoded_query}"`. While `urllib.parse.quote()` was used on the query, the base URL was hardcoded in the f-string and could be manipulated if the function signature changed.

**Fix:** Switched to `urllib.parse.urlencode({"q": query}, quote_via=urllib.parse.quote_plus)` + `urllib.parse.urljoin(_DDG_BASE, "/html/")`. Defined `_DDG_BASE`, `_WIKI_BASE`, `_NEWS_BASE` as trusted host constants.

**Test plan:** URLs are validated at construction time; all existing web search tests pass.

### 9. Missing Fernet key validation in config_encryption.py

**File:** `myclaw/config_encryption.py` — `_get_or_create_key`, `_load_key`
**Severity:** Medium (corrupted/invalid keys silently breaking config)

**Root cause:** `ZENSYNORA_CONFIG_KEY` env var was used as-is without checking whether it actually was a valid Fernet key (must be 43 url-safe base64 chars + `=`). A typo in the env var would cause silent decryption failures.

**Fix:** Added `_validate_key_format()` with regex `^[A-Za-z0-9_\-]{43}=$` and entropy heuristic. Env var, keyring, and file keys all validated on load. Invalid keys logged with clear warning and fallback to next source.

### 10. No security linters in pre-commit pipeline

**Severity:** Low (vulnerabilities accumulate over time)

**Fix:** Added `bandit` (v1.7.10) SAST hook and `safety` (v3.2.14) dependency vulnerability scanner to `.pre-commit-config.yaml`. Both scoped to application source files only.

---

## Fixes from 2026-05-18 (Changeset 2)

### 11. Timing attack on API key comparison in web/auth.py

**File:** `myclaw/web/auth.py:27` — `require_admin_api_key`
**Severity:** Medium (timing side-channel on API key validation)

**Root cause:** `if x_api_key != expected:` performs byte-by-byte comparison, leaking key length and prefix information through response timing.

**Fix:** Replaced with `secrets.compare_digest(x_api_key, expected)` — constant-time comparison regardless of key content.

**Test plan:** Functionally identical; all existing auth tests pass.

### 12. Billion-laughs XML attack vector in search_news()

**File:** `myclaw/web_search.py` — `search_news`
**Severity:** Medium (DoS via XML entity expansion)

**Root cause:** `search_news()` parsed Google News RSS with `xml.etree.ElementTree.fromstring()` which is vulnerable to exponential entity expansion (billion laughs attack).

**Fix:** Added `_safe_parse_xml()` that uses `defusedxml.ElementTree` when installed. Falls back to standard `ElementTree` with clear logging. Defusedxml blocks entity expansion and raises `EntitiesForbidden`.

### 13. Missing rate limiting on public web search functions

**File:** `myclaw/web_search.py` — all public search functions
**Severity:** Low (potential for API abuse)

**Root cause:** `search_web()`, `search_wikipedia()`, `search_news()`, and `get_webpage_content()` had no rate limiting, allowing unbounded calls to external services.

**Fix:** Added `_rate_limit_check()` token-bucket limiter with per-function limits: 30/min (DuckDuckGo), 60/min (Wikipedia), 20/min (Google News), 30/min (webpage fetch). Returns structured error on limit exceeded.

### 14. Deprecated logging module retained as footgun

**File:** `myclaw/logging.py`
**Severity:** Low (duplicate logging configuration path)

**Root cause:** Two logging modules (`logging.py` with simple `basicConfig`, `logging_config.py` with structured formatting + PII scrubbing) caused confusion. `init_app()` was importing the wrong one.

**Fix:** `logging.py` now emits `DeprecationWarning` and delegates to `logging_config.py`. `init_app()` updated to import the structured logger directly. All new code should import from `logging_config`.


## Files changed (2026-05-17/18)

| File | Changeset | Change |
|---|---|---|
| `pyproject.toml` | 1 | Dependency upper bounds; coverage enforcement |
| `.pre-commit-config.yaml` | 1 | Bandit + Safety hooks |
| `myclaw/web_search.py` | 1 + 2 | URL safety; shared session; safe XML; rate limiting |
| `myclaw/aiohttp_session.py` | 1 | **New file** — shared async HTTP session singleton |
| `myclaw/config_encryption.py` | 1 | Fernet key format validation |
| `myclaw/__init__.py` | 1 | `init_app()` centralized bootstrapper |
| `myclaw/cli.py` | 1 | Use `init_app()` instead of inline shutdown handlers |
| `myclaw/context_window.py` | 1 | Consolidated model limits dictionary |
| `myclaw/logging.py` | 2 | Deprecated; delegates to `logging_config.py` |
| `myclaw/web/auth.py` | 2 | `secrets.compare_digest()` for API key comparison |
| `myclaw/defaults.py` | 2 | `MAX_DELEGATION_DEPTH`, `TASK_TIMER_STEPS_TOTAL`, `DEFAULT_SUMMARIZATION_THRESHOLD` |
| `myclaw/agent_internals/router.py` | 2 | Named constants replacing magic numbers |
| `tests/test_swarm_integration.py` | 1 | `pytest.mark.xfail` for Windows |
| `tests/test_memory_pool_concurrency.py` | 2 | `asyncio.Event` replacing `asyncio.sleep()` |
| `docs/review01.md` | 1 + 2 | Comprehensive application review (new) |
| Various `.md` files | 2 | Documentation updates and stale reference resolutions |
