# Remaining Optimizations Implementation List

> Last Updated: 2026-03-17  
> Status: 18 of 42 optimizations implemented

This document lists all pending optimization items organized by category, each with a clear implementation approach.

---

## ✅ Already Implemented (18 items)

| # | Category | Item | File |
|---|----------|------|------|
| 1 | Provider | HTTP Connection Pooling | [`myclaw/provider.py`](myclaw/provider.py) |
| 2 | Provider | Retry Logic with Exponential Backoff | [`myclaw/provider.py`](myclaw/provider.py) |
| 3 | Memory | SQLite Connection Pool | [`myclaw/memory.py`](myclaw/memory.py) |
| 4 | Config | Environment Variable Overrides | [`myclaw/config.py`](myclaw/config.py) |
| 5 | Agent | Profile Caching | [`myclaw/agent.py`](myclaw/agent.py) |
| 6 | Tools | Shell Timeout Configuration | [`myclaw/tools.py`](myclaw/tools.py) |
| 7 | Knowledge | Knowledge Sync Cache | [`myclaw/knowledge/sync.py`](myclaw/knowledge/sync.py) |
| 8 | Async | Async subprocess for shell | [`myclaw/tools.py`](myclaw/tools.py) |
| 9 | Tools | Tool execution rate limiting | [`myclaw/tools.py`](myclaw/tools.py) |
| 10 | Tools | Runtime allowlist updates | [`myclaw/tools.py`](myclaw/tools.py) |
| 11 | Tools | Tool execution audit logging | [`myclaw/tools.py`](myclaw/tools.py) |
| 12 | Memory | Optional memory cleanup | [`myclaw/memory.py`](myclaw/memory.py) |
| 13 | Config | Config caching with file watcher | [`myclaw/config.py`](myclaw/config.py) |
| 14 | CLI | Graceful shutdown handling | [`cli.py`](cli.py) |
| 15 | Swarm | Swarm execution timeout enforcement | [`myclaw/swarm/orchestrator.py`](myclaw/swarm/orchestrator.py) |
| 16 | Telegram | Configurable ThreadPoolExecutor | [`myclaw/gateway.py`](myclaw/gateway.py) |
| 17 | Telegram | Message queue with backpressure | [`myclaw/channels/telegram.py`](myclaw/channels/telegram.py) |
| 18 | Telegram | Optimized typing indicator | [`myclaw/channels/telegram.py`](myclaw/channels/telegram.py) |

---

## 📋 Pending Optimizations by Category

### 1. Memory Management

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **1.1** | Optimize VACUUM frequency | Reduced I/O during cleanup | Only run VACUUM periodically (e.g., every 100 cleanups or weekly) instead of on every cleanup. Add a counter to track cleanups. |
| **1.2** ✅ | Column selection in history queries | Reduced memory usage | Modify `get_history()` to accept column list parameter, only fetch needed columns for summarization vs display. |

#### Implementation Detail for 1.1 - [`myclaw/memory.py`](myclaw/memory.py:150)

```python
# Add to Memory class
_cleanup_count: int = 0

def cleanup(self, days: int = 30):
    # ... existing deletion code ...
    Memory._cleanup_count += 1
    
    # Only VACUUM every 100 cleanups
    if Memory._cleanup_count % 100 == 0:
        self.conn.execute("VACUUM")
```

---

### 2. Agent & LLM Provider

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **2.1** | Configurable context summarization threshold | Faster responses, reduced API calls | Add `config.agents.summarization_threshold` (default: 10). Modify summarization trigger in [`myclaw/agent.py`](myclaw/agent.py) to check threshold before running. |
| **2.2** | Request caching for repeated queries | Reduced latency | Add LRU cache decorator to provider chat methods. Cache key = hash(messages). TTL = 5 minutes. |
| **2.3** | Lazy provider initialization | Faster startup | Move provider creation from `Agent.__init__` to first `Agent.chat()` call. Use `@property` with lazy loading. |
| **2.4** | Streaming response support | Better UX | Add `stream=True` parameter to provider chat methods. Yield chunks in [`myclaw/agent.py`](myclaw/agent.py) for real-time display. |
| **2.5** ✅ | Consolidate tool schemas | DRY principle | Move `TOOL_SCHEMAS` to [`myclaw/tools.py`](myclaw/tools.py). Import from both provider.py and tools.py. |

#### Implementation Detail for 2.3 - Lazy Provider Loading in [`myclaw/agent.py`](myclaw/agent.py:50)

```python
class Agent:
    def __init__(self, name: str = "default", ...):
        # Don't initialize provider here
        self._provider = None
        self._provider_name = provider or config.agents.defaults.provider
    
    @property
    def provider(self):
        if self._provider is None:
            self._provider = create_provider(self._provider_name)
        return self._provider
```

---

### 3. Knowledge System

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **3.1** ✅ | FTS5 BM25 ranking optimization | More relevant search results | Implemented: Added observations FTS table, combined BM25 scoring, rank_bm25() helper |
| **3.2** | Composite indexes for graph queries | Faster entity/relation queries | Add composite indexes: `CREATE INDEX idx_entity_type_name ON entities(type, name)` in [`myclaw/knowledge/db.py`](myclaw/knowledge/db.py). |
| **3.3** | Background knowledge extraction | Automatic knowledge capture | Add background task using `asyncio.create_task()` in [`myclaw/knowledge/sync.py`](myclaw/knowledge/sync.py). Make configurable via `config.knowledge.auto_extract`. |

#### Implementation Detail for 3.1 - BM25 Ranking in [`myclaw/knowledge/db.py`](myclaw/knowledge/db.py:80)

```python
def search_notes(self, query: str, limit: int = 10):
    # Use BM25 ranking
    cursor = self.conn.execute("""
        SELECT path, content, bm25(messages_fts) as rank
        FROM messages_fts
        WHERE messages_fts MATCH ?
        ORDER BY rank
        LIMIT ?
    """, (query, limit))
    return cursor.fetchall()
```

---

### 4. Agent Swarms

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **4.1** ✅ | Swarm execution timeout enforcement | Prevent hung executions | Add timeout parameter to `SwarmOrchestrator.execute()`. Use `asyncio.wait_for()` with cancellation in [`myclaw/swarm/orchestrator.py`](myclaw/swarm/orchestrator.py). |
| **4.2** | Shared connection pool for swarm storage | Reduced file handles | Pass `SQLitePool` reference to swarm storage instead of creating new connections. |
| **4.3** | Swarm result caching | Faster result retrieval | Add cache in [`myclaw/swarm/storage.py`](myclaw/swarm/storage.py) keyed by swarm_id + input_hash. TTL = 1 hour. |
| **4.4** | Persistent active execution tracking | Crash recovery | Save execution state to SQLite on start/update. Load on restart in [`myclaw/swarm/orchestrator.py`](myclaw/swarm/orchestrator.py). |
| **4.5** | Semaphore-based concurrency control | Proper resource limiting | Add `asyncio.Semaphore(max_concurrent)` in `SwarmOrchestrator.__init__`. Acquire before each swarm start. |

#### Implementation Detail for 4.1 - Timeout Enforcement in [`myclaw/swarm/orchestrator.py`](myclaw/swarm/orchestrator.py:100)

```python
async def execute(self, agents: List[Agent], task: str, timeout: int = 300):
    async def _execute_with_timeout():
        return await self._execute_internal(agents, task)
    
    try:
        result = await asyncio.wait_for(_execute_with_timeout(), timeout=timeout)
        return result
    except asyncio.TimeoutError:
        # Cancel and log
        logger.error(f"Swarm execution timed out after {timeout}s")
        raise TimeoutError(f"Swarm execution exceeded {timeout}s timeout")
```

---

### 5. Tools & Security

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **5.1** ✅ | Tool execution rate limiting | Prevent abuse | Add `RateLimiter` class in [`myclaw/tools.py`](myclaw/tools.py). Apply per-tool limits (e.g., shell: 10/min, http: 60/min). |
| **5.2** ✅ | Dynamic tool validation | Security improvement | Add AST-based code validation before `exec()` in tool builder. Block dangerous patterns. |
| **5.3** ✅ | Runtime allowlist updates | More flexible operation | Add `update_allowlist()` function in [`myclaw/tools.py`](myclaw/tools.py). Store in config, watch for changes. |
| **5.4** ✅ | Tool execution audit logging | Better debugging/security | Add structured logging for all tool executions: tool name, user, duration, success/failure in [`myclaw/tools.py`](myclaw/tools.py). |

#### Implementation Detail for 5.1 - Rate Limiter in [`myclaw/tools.py`](myclaw/tools.py:50)

```python
from collections import defaultdict
import time

class RateLimiter:
    def __init__(self):
        self._limits = defaultdict(lambda: ([], 10, 60))  # (timestamps, max_calls, window_seconds)
    
    def check(self, tool_name: str, max_calls: int = 10, window: int = 60) -> bool:
        now = time.time()
        timestamps, _, _ = self._limits[tool_name]
        # Remove old timestamps
        self._limits[tool_name] = ([t for t in timestamps if now - t < window], max_calls, window)
        
        if len(timestamps) >= max_calls:
            return False
        timestamps.append(now)
        return True

_rate_limiter = RateLimiter()

def shell(cmd: str) -> str:
    if not _rate_limiter.check("shell", max_calls=10, window=60):
        raise PermissionError("Rate limit exceeded for shell tool")
    # ... existing code
```

---

### 6. Configuration & System

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **6.1** ✅ | Config caching with file watcher | Faster subsequent imports | Add `watchdog` dependency. Cache config after first load. Invalidate on file change in [`myclaw/config.py`](myclaw/config.py). |
| **6.2** ✅ | Optional memory cleanup | Faster startup | Add `config.memory.auto_cleanup = False` to disable cleanup on init. Add manual `mem.cleanup()` call option. |
| **6.3** ✅ | Graceful shutdown handling | Prevent data loss | Add signal handlers (SIGINT, SIGTERM) in [`cli.py`](cli.py). Call `SQLitePool.close_all()` and `HTTPClientPool.close()` on shutdown. |

#### Implementation Detail for 6.3 - Graceful Shutdown in [`cli.py`](cli.py:200)

```python
import signal
import atexit

def _setup_shutdown_handlers():
    def shutdown_handler(signum, frame):
        logger.info("Shutdown signal received, cleaning up...")
        # Close HTTP pool
        import myclaw.provider
        asyncio.run(myclaw.provider.HTTPClientPool.close())
        # Close SQLite pool
        import myclaw.memory
        myclaw.memory.SQLitePool.close_all()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)
    atexit.register(lambda: asyncio.run(myclaw.provider.HTTPClientPool.close()))
```

---

### 7. Telegram Integration

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **7.1** ✅ | Configurable ThreadPoolExecutor size | Resource optimization | Add `config.channels.telegram.max_workers`. Default 20, make adjustable in [`myclaw/gateway.py`](myclaw/gateway.py). |
| **7.2** ✅ | Message queue with backpressure | Better burst handling | Add `asyncio.Queue` with max size. Reject new messages when full in [`myclaw/channels/telegram.py`](myclaw/channels/telegram.py). |
| **7.3** ✅ | Optimized typing indicator | Subtle improvement | Only send typing indicator for messages > 500ms expected processing time. Cache last indicator sent time. |
| **7.4** ✅ | Webhook mode for production | Production deployment | Add webhook support using `webhook_adapter` in [`myclaw/channels/telegram.py`](myclaw/channels/telegram.py). Configurable via `config.channels.telegram.webhook_url`. |

#### Implementation Detail for 7.2 - Message Queue in [`myclaw/channels/telegram.py`](myclaw/channels/telegram.py:100)

```python
class TelegramGateway:
    def __init__(self, ...):
        self._message_queue = asyncio.Queue(maxsize=100)  # Backpressure limit
        self._worker_task = None
    
    async def start(self):
        self._worker_task = asyncio.create_task(self._process_messages())
    
    async def _process_messages(self):
        while True:
            try:
                update = await self._message_queue.get()
                await self._handle_update(update)
            except asyncio.CancelledError:
                break
    
    async def on_update(self, update):
        # Non-blocking put with backpressure
        try:
            self._message_queue.put_nowait(update)
        except asyncio.QueueFull:
            logger.warning("Message queue full, rejecting update")
            raise HTTPException(status_code=503, detail="Service busy")
```

---

### 8. Async & Concurrency

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **8.1** | Standardize async patterns | Better concurrency | Convert remaining sync methods in [`myclaw/agent.py`](myclaw/agent.py) to async. Use `async/await` consistently. |
| **8.2** | Async knowledge operations | Non-blocking operations | Add async methods to [`myclaw/knowledge/db.py`](myclaw/knowledge/db.py), [`myclaw/knowledge/sync.py`](myclaw/knowledge/sync.py). Use `asyncio.to_thread()` for SQLite calls. |
| **8.3** ✅ | Async subprocess for shell | Better async performance | Replace `subprocess.run()` with `asyncio.create_subprocess_shell()` in [`myclaw/tools.py`](myclaw/tools.py). |

#### Implementation Detail for 8.3 - Async Shell in [`myclaw/tools.py`](myclaw/tools.py:80)

```python
async def shell_async(cmd: str, timeout: int = 30) -> str:
    # ... validation code ...
    
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=WORKSPACE
    )
    
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        return stdout.decode() + stderr.decode()
    except asyncio.TimeoutError:
        process.kill()
        raise TimeoutError(f"Shell command timed out after {timeout}s")
```

---

### 9. Code Quality

| # | Item | Expected Benefit | Implementation Approach |
|---|------|------------------|-------------------------|
| **9.1** | Specific exception handling | Better error messages | Replace broad `except:` with specific exceptions. Add custom exception classes in each module. |
| **9.2** | Comprehensive type annotations | Better maintainability | Add type hints to all function signatures. Use `mypy` for checking. Start with [`myclaw/config.py`](myclaw/config.py). |
| **9.3** | Standardized logging format | Easier debugging | Add `logging.basicConfig()` with consistent format in [`cli.py`](cli.py). Use `%(name)s - %(levelname)s - %(message)s`. |
| **9.4** | Comprehensive test suite | Code reliability | Add `pytest` tests. Focus on: config loading, memory operations, provider basics. |

---

## Priority Matrix

| Priority | Count | Items |
|----------|-------|-------|
| **High** | 9 | 2.1, 2.2, 4.1, 4.5, 5.1, 6.3, 7.4, 8.1, 8.2 |
| **Medium** | 12 | 1.1, 2.3, 2.4, 3.1, 3.2, 4.2, 4.3, 5.3, 6.1, 7.1, 7.2, 8.3 |
| **Low** | 14 | 1.2, 2.5, 3.3, 4.4, 5.2, 5.4, 6.2, 7.3, 9.1, 9.2, 9.3, 9.4 |

---

## Quick Reference: File Locations

| Module | File Path | Priority Items |
|--------|-----------|----------------|
| Agent | [`myclaw/agent.py`](myclaw/agent.py) | 2.1, 2.3, 8.1 |
| Provider | [`myclaw/provider.py`](myclaw/provider.py) | 2.2 |
| Memory | [`myclaw/memory.py`](myclaw/memory.py) | 1.1, 1.2 |
| Knowledge DB | [`myclaw/knowledge/db.py`](myclaw/knowledge/db.py) | 3.1, 3.2, 8.2 |
| Knowledge Sync | [`myclaw/knowledge/sync.py`](myclaw/knowledge/sync.py) | 3.3 |
| Swarm | [`myclaw/swarm/orchestrator.py`](myclaw/swarm/orchestrator.py) | 4.1, 4.4, 4.5 |
| Swarm Storage | [`myclaw/swarm/storage.py`](myclaw/swarm/storage.py) | 4.2, 4.3 |
| Tools | [`myclaw/tools.py`](myclaw/tools.py) | 5.1, 5.2, 5.3, 5.4, 8.3 |
| Config | [`myclaw/config.py`](myclaw/config.py) | 6.1, 6.2, 9.2 |
| CLI | [`cli.py`](cli.py) | 6.3, 9.3 |
| Telegram | [`myclaw/channels/telegram.py`](myclaw/channels/telegram.py) | 7.1, 7.2, 7.3, 7.4 |

---

## Dependencies Required

```txt
# For 6.1 - Config file watching
watchdog>=3.0.0

# For 9.2 - Type checking
mypy>=1.0.0

# For 9.4 - Testing
pytest>=7.0.0
pytest-asyncio>=0.21.0
```

---

*Document generated: 2026-03-16*
