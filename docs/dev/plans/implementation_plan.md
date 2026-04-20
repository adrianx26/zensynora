# MyClaw Implementation Plan - Detailed Code Changes

This document provides detailed code changes needed for the top optimizations.

---

## 1. HTTP Connection Pooling for LLM Providers

### Problem
Currently, `httpx.AsyncClient()` is created fresh for each request in [`myclaw/provider.py`](myclaw/provider.py:470). This creates connection overhead.

### Solution
Create a shared async HTTP client with connection pooling.

#### File: `myclaw/provider.py`

```python
# Add at the module level (after imports, before class definitions)

class HTTPClientPool:
    """Shared HTTP client with connection pooling."""
    
    _instance: Optional[httpx.AsyncClient] = None
    
    @classmethod
    def get_client(cls, timeout: int = DEFAULT_TIMEOUT) -> httpx.AsyncClient:
        if cls._instance is None:
            cls._instance = httpx.AsyncClient(
                timeout=httpx.Timeout(timeout),
                limits=httpx.Limits(
                    max_keepalive_connections=20,
                    max_connections=100,
                    keepalive_expiry=30.0
                ),
                http2=True  # Enable HTTP/2 for better multiplexing
            )
        return cls._instance
    
    @classmethod
    async def close(cls):
        if cls._instance is not None:
            await cls._instance.aclose()
            cls._instance = None

# Modify OllamaProvider.chat method (around line 462-486)
async def chat(self, messages, model="llama3.2"):
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "tools": TOOL_SCHEMAS,
    }
    try:
        # Use pooled client instead of creating new one
        client = HTTPClientPool.get_client(self.timeout)
        r = await client.post(
            f"{self.base_url}/api/chat",
            json=payload,
        )
        r.raise_for_status()
        msg = r.json()["message"]
        tool_calls = msg.get("tool_calls") or None
        return msg.get("content", ""), tool_calls
    except httpx.TimeoutException:
        raise TimeoutError(f"Ollama request timed out after {self.timeout}s")
    except httpx.ConnectError as e:
        raise ConnectionError(f"Could not connect to Ollama at {self.base_url}") from e
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"Ollama HTTP error: {e}") from e

# Add cleanup function for application shutdown
async def cleanup_http_pool():
    """Call this on application shutdown."""
    await HTTPClientPool.close()
```

---

## 2. Retry Logic with Exponential Backoff

### Problem
No retry mechanism for transient LLM failures.

### Solution
Add retry logic with exponential backoff to provider chat methods.

#### File: `myclaw/provider.py`

```python
# Add retry utility after imports (around line 34)

import asyncio
from functools import wraps

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exponential_base: float = 2.0,
    retriable_exceptions: tuple = (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPStatusError)
):
    """Decorator for retrying async functions with exponential backoff."""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except retriable_exceptions as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (exponential_base ** attempt), max_delay)
                        logger.warning(
                            f"Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                            f"Retrying in {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(f"All {max_retries + 1} attempts failed")
            raise last_exception
        return wrapper
    return decorator

# Apply to OllamaProvider.chat
@retry_with_backoff(max_retries=3, base_delay=1.0)
async def chat(self, messages, model="llama3.2"):
    # ... existing implementation

# Apply to OpenAICompatProvider.chat
class OpenAICompatProvider(BaseLLMProvider):
    
    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def chat(self, messages, model="gpt-4o-mini"):
        # ... existing implementation
```

---

## 3. SQLite Connection Pool for Memory

### Problem
Each Memory instance creates its own connection without pooling.

### Solution
Implement a connection pool for SQLite databases.

#### File: `myclaw/memory.py`

```python
# Add at module level (after imports)

from contextlib import contextmanager
import threading

class SQLitePool:
    """Simple connection pool for SQLite databases."""
    
    _pools: dict[str, sqlite3.Connection] = {}
    _locks: dict[str, threading.Lock] = {}
    _refcounts: dict[str, int] = {}
    _pool_lock = threading.Lock()
    
    @classmethod
    def get_connection(cls, db_path: Path) -> sqlite3.Connection:
        """Get or create a pooled connection."""
        key = str(db_path)
        
        with cls._pool_lock:
            if key not in cls._locks:
                cls._locks[key] = threading.Lock()
                cls._refcounts[key] = 0
            
            refcount = cls._refcounts[key]
        
        # Use lock for this specific DB
        cls._locks[key].acquire()
        
        if key not in cls._pools:
            conn = sqlite3.connect(db_path, check_same_thread=False)
            conn.execute("PRAGMA journal_mode=WAL")  # Enable WAL for better concurrency
            conn.execute("PRAGMA synchronous=NORMAL")  # Balance safety/speed
            cls._pools[key] = conn
        
        with cls._pool_lock:
            cls._refcounts[key] += 1
            
        return cls._pools[key]
    
    @classmethod
    def release_connection(cls, db_path: Path):
        """Release a connection back to the pool."""
        key = str(db_path)
        with cls._pool_lock:
            cls._refcounts[key] -= 1
        
        if key in cls._locks:
            cls._locks[key].release()
    
    @classmethod
    def close_all(cls):
        """Close all pooled connections."""
        with cls._pool_lock:
            for conn in cls._pools.values():
                try:
                    conn.close()
                except Exception:
                    pass
            cls._pools.clear()
            cls._refcounts.clear()

# Modify Memory class constructor
def __init__(self, user_id: str = "default", auto_cleanup_days: int = 30):
    db_path = Path.home() / ".myclaw" / f"memory_{user_id}.db"
    self.db = db_path
    self.db.parent.mkdir(parents=True, exist_ok=True)
    self.auto_cleanup_days = auto_cleanup_days
    
    # Use pooled connection
    self.conn = SQLitePool.get_connection(self.db)
    
    # Schema creation (keep existing)
    self.conn.execute("""CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY,
        role TEXT,
        content TEXT,
        timestamp TEXT
    )""")
    self.conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON messages(timestamp)")
    self.conn.commit()
    self.cleanup(self.auto_cleanup_days)

# Modify Memory.close
def close(self):
    if hasattr(self, 'conn') and self.conn:
        try:
            SQLitePool.release_connection(self.db)
            logger.info(f"Database connection released: {self.db.name}")
        except Exception as e:
            logger.error(f"Error releasing database connection: {e}")
```

---

## 4. Environment Variable Overrides for Config

### Problem
Config only reads from JSON file, no ENV override support.

### Solution
Add environment variable prefix override capability.

#### File: `myclaw/config.py`

```python
# Add after imports (around line 6)
import os

# Configuration key mapping: config_json_key -> env_var_name
ENV_OVERRIDES = {
    # Providers
    "providers.ollama.base_url": "MYCLAW_OLLAMA_BASE_URL",
    "providers.openai.api_key": "MYCLAY_OPENAI_API_KEY",
    "providers.anthropic.api_key": "MYCLAW_ANTHROPIC_API_KEY",
    "providers.gemini.api_key": "MYCLAW_GEMINI_API_KEY",
    "providers.groq.api_key": "MYCLAW_GROQ_API_KEY",
    
    # Telegram
    "channels.telegram.token": "MYCLAW_TELEGRAM_TOKEN",
    
    # Defaults
    "agents.defaults.provider": "MYCLAW_DEFAULT_PROVIDER",
    "agents.defaults.model": "MYCLAW_DEFAULT_MODEL",
    
    # Swarm
    "swarm.enabled": "MYCLAW_SWARM_ENABLED",
    "swarm.max_concurrent_swarms": "MYCLAW_MAX_CONCURRENT_SWARMS",
    "swarm.timeout_seconds": "MYCLAW_SWARM_TIMEOUT",
}

def _apply_env_overrides(config_dict: dict) -> dict:
    """Apply environment variable overrides to config dictionary."""
    def deep_get(d: dict, key: str):
        """Get nested dict value using dot notation."""
        keys = key.split(".")
        for k in keys:
            if isinstance(d, dict):
                d = d.get(k, {})
            else:
                return None
        return d if d else None
    
    def deep_set(d: dict, key: str, value):
        """Set nested dict value using dot notation."""
        keys = key.split(".")
        current = d
        for k in keys[:-1]:
            if k not in current:
                current[k] = {}
            current = current[k]
        current[keys[-1]] = value
    
    for config_key, env_var in ENV_OVERRIDES.items():
        env_value = os.environ.get(env_var)
        if env_value is not None:
            logger.info(f"Applying env override: {env_var} -> {config_key}")
            
            # Try to infer type from current value
            current_value = deep_get(config_dict, config_key)
            if isinstance(current_value, bool):
                # Parse boolean
                deep_set(config_dict, config_key, env_value.lower() in ('true', '1', 'yes'))
            elif isinstance(current_value, int):
                # Parse integer
                try:
                    deep_set(config_dict, config_key, int(env_value))
                except ValueError:
                    logger.warning(f"Cannot convert {env_var} value to int")
            else:
                # String value
                deep_set(config_dict, config_key, env_value)
    
    return config_dict

# Modify load_config function (around line 143)
def load_config() -> AppConfig:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    if not CONFIG_FILE.exists():
        return AppConfig()

    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        
        # Apply environment variable overrides
        raw = _apply_env_overrides(raw)
        
        return AppConfig(**raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"❌ Config error: {CONFIG_FILE} is not valid JSON — {e}")
    except ValidationError as e:
        raise SystemExit(f"❌ Config validation error:\n{e}")
```

---

## 5. Profile Caching for Agents

### Problem
Profile files are read from disk on every agent creation.

### Solution
Add caching for parsed profiles.

#### File: `myclaw/agent.py`

```python
# Add at module level (after imports)
from functools import lru_cache
import hashlib

# Add profile cache
_profile_cache: dict[str, str] = {}
_profile_cache_lock = threading.Lock()

def _get_profile_cache_key(name: str, profile_path: Path) -> str:
    """Generate cache key based on name and file mtime."""
    try:
        mtime = profile_path.stat().st_mtime
        return f"{name}:{mtime}"
    except Exception:
        return f"{name}:0"

def _load_profile_cached(name: str, profile_path: Path) -> str:
    """Load profile with caching based on file modification time."""
    cache_key = _get_profile_cache_key(name, profile_path)
    
    with _profile_cache_lock:
        if cache_key in _profile_cache:
            return _profile_cache[cache_key]
    
    # Load and cache
    content = profile_path.read_text(encoding="utf-8").strip()
    
    with _profile_cache_lock:
        _profile_cache[cache_key] = content
        
        # Limit cache size
        if len(_profile_cache) > 100:
            # Remove oldest entries (simple FIFO)
            keys_to_remove = list(_profile_cache.keys())[:50]
            for key in keys_to_remove:
                del _profile_cache[key]
    
    return content

# Modify Agent.__init__ to use cached loading (around line 69-84)
# Change from:
#   self.system_prompt = profile_path.read_text(encoding="utf-8").strip()
# To:
#   self.system_prompt = _load_profile_cached(self.name, profile_path)
```

---

## 6. Shell Timeout Configuration

### Problem
Shell timeout hardcoded to 30 seconds.

### Solution
Make timeout configurable via config file.

#### File: `myclaw/config.py` - Add to AppConfig

```python
class AppConfig(BaseModel):
    # ... existing fields ...
    
    # Timeout settings
    timeouts: TimeoutConfig = TimeoutConfig()

class TimeoutConfig(BaseModel):
    """Timeout configuration."""
    shell_seconds: int = 30
    llm_seconds: int = 60
    http_seconds: int = 30
```

#### File: `myclaw/tools.py` - Modify shell function

```python
# Add module-level config reference
_config = None

def set_config(config):
    """Called by gateway to provide config."""
    global _config
    _config = config

def shell(cmd: str) -> str:
    # ... existing validation code ...
    
    # Get timeout from config or use default
    timeout = 30
    if _config and hasattr(_config, 'timeouts'):
        timeout = _config.timeouts.shell_seconds
    
    result = subprocess.run(
        parts, shell=False, cwd=WORKSPACE,
        capture_output=True, text=True, timeout=timeout
    )
    return result.stdout + result.stderr
```

---

## 7. Knowledge Sync Optimization

### Problem
Knowledge sync already has incremental detection, but can be optimized further.

### Solution
Add caching for parsed notes and optimize the detect_changes function.

#### File: `myclaw/knowledge/sync.py`

```python
# Add caching at module level
from functools import lru_cache
from datetime import datetime

# Add cache for parsed notes
_parsed_note_cache: dict[str, tuple] = {}  # path -> (note, mtime)

def _get_cached_note(file_path: Path):
    """Get note from cache or parse and cache it."""
    path_str = str(file_path)
    mtime = file_path.stat().st_mtime
    
    if path_str in _parsed_note_cache:
        cached_mtime, cached_note = _parsed_note_cache[path_str]
        if cached_mtime == mtime:
            return cached_note
    
    # Parse and cache
    note = parse_note(file_path)
    _parsed_note_cache[path_str] = (mtime, note)
    return note

def clear_note_cache():
    """Clear the parsed note cache."""
    global _parsed_note_cache
    _parsed_note_cache = {}

# Optimize detect_changes to use cached parsing
def detect_changes(user_id: str = "default") -> Tuple[List[Path], List[str], List[str]]:
    # ... existing code ...
    
    for file_path in files:
        try:
            # Use cached parsing instead of parse_note directly
            note = _get_cached_note(file_path)
            # ... rest unchanged ...
        except Exception as e:
            logger.warning(f"Failed to parse {file_path}: {e}")
```

---

## Summary of Changes

| Optimization | Files to Modify | Complexity |
|-------------|----------------|------------|
| HTTP Connection Pooling | `myclaw/provider.py` | Medium |
| Retry Logic | `myclaw/provider.py` | Low |
| SQLite Connection Pool | `myclaw/memory.py` | Medium |
| Env Variable Overrides | `myclaw/config.py` | Low |
| Profile Caching | `myclaw/agent.py` | Low |
| Shell Timeout Config | `myclaw/config.py`, `myclaw/tools.py` | Low |
| Knowledge Sync Cache | `myclaw/knowledge/sync.py` | Low |

---

## Implementation Order

1. **HTTP Connection Pooling** - Highest impact, start here
2. **Retry Logic** - Quick win for reliability
3. **SQLite Connection Pool** - Good performance improvement
4. **Env Variable Overrides** - Easy to implement, useful for deployment
5. **Profile Caching** - Faster agent creation
6. **Shell Timeout Config** - Flexibility improvement
7. **Knowledge Sync Cache** - Already partially implemented, polish it

---

*Implementation plan created: 2026-03-16*
