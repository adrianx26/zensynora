# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.1] - 2026-04-14

### Added
- **Dashboard Server Refactoring** (`myclaw/dashboard_server.py`)
  - Extracted FastAPI app factory to a dedicated module to fix import errors and improve separation of concerns.
- **Vosk STT Implementation** (`myclaw/voice_channel.py`)
  - Completed `VoskSTTProvider.transcribe` for offline speech-to-text.
  - Implemented `VoiceChannel.listen_stream` with chunk buffering and VAD triggers.
- **Skill Preloading Execution** (`myclaw/skill_preloader.py`)
  - Implemented `_load_skill_code` to proactively cache tool scripts into memory.

### Fixed
- **Dashboard Startup**: Resolved circular dependency and missing module errors in `myclaw/dashboard.py`.
- **Documentation Accuracy**: Audited and synchronized `../code_analysis_summary.md` and `../FUNCTIONS_SUMMARY.md` with the actual codebase implementation.

## [Unreleased]

### Added

- **WhatsApp Business Cloud API Channel** (`myclaw/channels/whatsapp.py`)
  - New communication channel using the official WhatsApp Business Cloud API (Meta Graph API)
  - FastAPI webhook server for receiving and responding to WhatsApp messages
  - Webhook verification endpoint (GET /webhook) for Meta's challenge-response flow
  - Message handling with sender allowlist filtering (`allowFrom` by phone number)
  - Full command support: `/remind`, `/jobs`, `/cancel`, `/agents`, all `/knowledge_*` commands
  - Agent routing via `@agentname` prefix (same as Telegram)
  - Automatic message splitting for responses exceeding WhatsApp's 4096-character limit
  - Channel-agnostic notification callback for scheduled job results

- **WhatsApp Configuration** (`myclaw/config.py`)
  - New `WhatsAppConfig` Pydantic model with `phone_number_id`, `business_account_id`, `access_token`, `verify_token`, and `allowFrom`
  - Added `whatsapp` field to `ChannelsConfig`
  - Environment variable overrides: `MYCLAW_WHATSAPP_PHONE_NUMBER_ID`, `MYCLAW_WHATSAPP_BUSINESS_ACCOUNT_ID`, `MYCLAW_WHATSAPP_ACCESS_TOKEN`, `MYCLAW_WHATSAPP_VERIFY_TOKEN`

- **Channel-Agnostic Notification System** (`myclaw/tools/`)
  - New `set_notification_callback()` function for registering async notification handlers
  - Updated `_create_job_internal()` to try notification callback before falling back to Telegram bot
  - Updated `schedule()` error message to be channel-agnostic

- **Gateway WhatsApp Support** (`myclaw/gateway.py`)
  - Added `WhatsAppChannel` import and startup logic
  - Gateway now starts WhatsApp channel when `channels.whatsapp.enabled` is true

- **New Dependencies** (`requirements.txt`)
  - `fastapi>=0.100.0` — ASGI web framework for WhatsApp webhook server
  - `uvicorn>=0.23.0` — ASGI server to run FastAPI

- **Documentation**
  - New `whatsapp_implementation_plan.md` — comprehensive implementation plan with architecture diagrams, setup guide, and remaining work items
  - Updated `README.md` — WhatsApp in features, architecture, config, commands, and project structure
  - Updated `../how to run.md` — WhatsApp gateway instructions

### Optimized

- **Swarm Result Caching** (`myclaw/swarm/storage.py`)
  - Optimization 4.3: Added in-memory result caching with TTL (1 hour)
  - New `ResultCache` class with thread-safe operations using `threading.RLock`
  - Cache key format: `swarm_id:input_hash` (SHA256 of input data)
  - Modified `SwarmStorage.__init__()` to accept `enable_cache` parameter (default: True)
  - Modified `save_result()` to accept optional `input_hash` parameter for caching
  - Modified `get_result()` to check cache first before database lookup
  - Added `invalidate_result_cache()` method to manually invalidate cached results
  - Added `get_cache_stats()` method to retrieve cache statistics
  - Cache automatically expires entries after 1 hour (3600 seconds)
  - Thread-safe for concurrent access
  - Can be disabled by passing `enable_cache=False` to constructor

- **Shared Connection Pool for Swarm Storage** (`myclaw/swarm/storage.py`, `myclaw/swarm/orchestrator.py`)
  - Optimization 4.2: Added `pool` parameter to `SwarmStorage.__init__()` method
  - Modified `_get_connection()` to use pooled connections when available
  - Uses `SQLitePool` from `myclaw.memory` for connection management
  - Falls back to creating new connections if pool unavailable (backward compatible)
  - Enables WAL mode and synchronous=NORMAL for better concurrency
  - Reduces connection overhead when swarm storage is used alongside memory storage
  - Orchestrator now passes SQLitePool to storage by default

- **Swarm Execution Timeout Enforcement** (`myclaw/swarm/orchestrator.py`)
   - Added optional `timeout` parameter to `SwarmOrchestrator.execute_task()` method
   - Added optional `timeout` parameter to `SwarmOrchestrator.execute_task_async()` method
   - Uses `asyncio.wait_for()` with cancellation for timeout enforcement
   - When timeout is specified, overrides the default timeout from config
   - Proper error handling for `asyncio.TimeoutError` with descriptive error message
   - Returns `SwarmResult` with timeout error message and zero confidence score on timeout

- **Persistent Active Execution Tracking** (`myclaw/swarm/models.py`, `myclaw/swarm/storage.py`, `myclaw/swarm/orchestrator.py`)
   - Optimization 4.4: Added persistent active execution tracking using SQLite
   - New `ActiveExecution` model to represent async execution state
   - Added `active_executions` table to swarm database
   - Added storage methods: `save_execution_state()`, `update_execution_state()`, `remove_execution_state()`, `load_active_executions()`, `recover_stale_executions()`
   - Updated orchestrator to save execution state on async task start and remove on completion
   - Added `load_active_executions()` method to orchestrator for restart recovery
   - Added `recover_stale_executions()` to mark crashed executions as terminated on startup
   - Enables swarm executions to survive orchestrator restarts

- **Background Knowledge Extraction** (`myclaw/knowledge/sync.py`, `myclaw/config.py`)
  - Added background task for automatic knowledge extraction using `asyncio.create_task()`
  - Configurable via `knowledge.auto_extract` in config (default: `false`)
  - New functions: `start_background_extraction()`, `stop_background_extraction()`, `is_background_extraction_running()`
  - Runs periodic sync in background with configurable interval (default: 60 seconds)
  - Uses `asyncio.to_thread()` to run sync without blocking the event loop
  - Can be enabled via config file or `MYCLAW_KNOWLEDGE_AUTO_EXTRACT` environment variable

- **Composite Indexes for Graph Queries** (`myclaw/knowledge/db.py`)
  - Added `idx_entity_type_name` on entities(name) for entity lookups
  - Added `idx_relations_from_type` on relations(from_entity_id, relation_type) for filtering outgoing relations by type
  - Added `idx_relations_to_type` on relations(to_entity_id, relation_type) for filtering incoming relations by type
  - Added `idx_observations_entity_category` on observations(entity_id, category) for category-filtered observation queries
  - Added `idx_relations_type` on relations(relation_type) for type-only lookups
  - These indexes significantly improve graph traversal and relation query performance

- **FTS5 BM25 Ranking** (`myclaw/knowledge/db.py`)
  - Added `rank_bm25()` optimization for more relevant search results
  - Created separate `observations_fts` FTS5 table to index observation content
  - Combined BM25 scoring from both entities and observations for better relevance
  - Added BM25 parameters configuration (`BM25_DEFAULT_K1`, `BM25_DEFAULT_B`)
  - Added `rebuild_fts_index()` method to populate FTS tables for existing databases
  - Backward compatible - falls back to entities-only search if observations FTS unavailable
  - Triggers added to keep observations FTS in sync with changes

- **Consolidate Tool Schemas** (`myclaw/tools/`, `myclaw/provider.py`)
  - Moved `TOOL_SCHEMAS` definition from `provider.py` to `tools.py`
  - `provider.py` now imports `TOOL_SCHEMAS` from `tools` module
  - Single source of truth for tool schema definitions
  - Reduces code duplication and improves maintainability

- **Streaming Response Support** (`myclaw/provider.py`, `myclaw/agent.py`)
  - Added `stream` parameter to all provider chat methods
  - Providers: Ollama, OpenAI-compatible (LMStudio, LlamaCpp, OpenAI, Groq, OpenRouter), Anthropic, Gemini
  - When `stream=True`, returns async iterator yielding content chunks
  - Added `stream_chat()` method to each provider for dedicated streaming
  - Added `stream_think()` method to Agent class for real-time response display
  - Uses SSE (Server-Sent Events) for Ollama streaming
  - Uses OpenAI SDK streaming for compatible providers
  - Uses Anthropic beta streaming API
  - Backward compatible - existing code works unchanged with `stream=False` (default)

- **Lazy Provider Initialization** (`myclaw/agent.py`)
  - Provider is now initialized on first access rather than in `__init__`
  - Improves startup performance by deferring provider initialization
  - Added `@property provider` method with lazy initialization logic
  - Falls back to "ollama" if primary provider fails to initialize
  - Backward compatible - `self.provider` still accessible as before

## [0.1.1] - 2026-03-16

### Added

- **Request Caching** (`myclaw/provider.py`)
  - Added in-memory request caching for all LLM providers using LRU cache decorator
  - 5-minute TTL (300 seconds) with automatic eviction (max 128 entries)
  - Cache key based on hash of messages and model parameters
  - Implemented `@lru_cache_with_ttl` decorator for async functions
  - Supported providers: Ollama, OpenAI-compatible, Anthropic, Gemini
  - Replaced manual caching code with decorator-based approach

- **Lazy Provider Loading** (`myclaw/provider.py`)
  - Added provider instance caching in `get_provider()`
  - Providers are only initialized when first requested
  - Added `clear_provider_cache()` function for testing/config changes

- **Runtime Command Allowlist** (`myclaw/tools/`)
  - Added mutable command allowlist (`_allowed_commands_set`)
  - Added `add_allowed_command()` function
  - Added `remove_allowed_command()` function
  - Added `get_allowed_commands()` function
  - Added `is_command_allowed()` function
  - Commands can now be added/removed at runtime

- **Tool Execution Audit Trail** (`myclaw/agent.py`)
  - Added audit logging for tool execution
  - Logs: tool start, success with duration, and failures
  - Format: `[AUDIT] Tool execution started/finished/failed`

- **FTS5 BM25 Ranking** (`myclaw/knowledge/db.py`)
  - Changed from `ORDER BY rank` to `ORDER BY bm25(entities_fts)`
  - BM25 provides better relevance scoring
  - Considers term frequency and document frequency

- **Telegram Webhook Mode** (`myclaw/channels/telegram.py`)
  - Added `run_webhook()` method for production deployments
  - More efficient than polling for high-load scenarios
  - Configurable webhook URL and port

- **Database Indexes** (`myclaw/knowledge/db.py`)
  - Added indexes on: `entities.file_path`, `entities.created_at`
  - Added indexes on: `observations.entity_id`, `observations.created_at`
  - Added indexes on: `tags.entity_id`, `tags.name`

- **Telegram ThreadPool Configuration** (`myclaw/channels/telegram.py`)
  - Added `set_threadpool_size()` function
  - Configurable thread pool for concurrent message handling
  - Default: 20 workers

- **Swarm Concurrency Control** (`myclaw/swarm/orchestrator.py`)
  - Added semaphore-based concurrency limiting
  - Configurable max concurrent swarms
  - Added result caching for faster retrieval

### Added

- **HTTP Connection Pooling** (`myclaw/provider.py`)
  - Added `HTTPClientPool` class for shared HTTP client with connection pooling
  - Supports up to 100 concurrent connections with 20 keepalive connections
  - HTTP/2 support for better multiplexing
  - Added `cleanup_http_pool()` function for graceful shutdown

- **Retry Logic** (`myclaw/provider.py`)
  - Added `@retry_with_backoff` decorator for automatic retry on failures
  - 3 retries with exponential backoff (1s, 2s, 4s)
  - Retries on: `TimeoutException`, `ConnectError`, `HTTPStatusError`

- **SQLite Connection Pool** (`myclaw/memory.py`)
  - Added `SQLitePool` class with reference counting
  - WAL mode enabled for better concurrency
  - Synchronous=NORMAL for balanced safety/speed

- **Environment Variable Overrides** (`myclaw/config.py`)
  - Added `ENV_OVERRIDES` mapping with support for 15+ config keys
  - Supports `MYCLAW_*` environment variables
  - Added `TimeoutConfig` class for configurable timeouts
  - Automatic type inference (bool, int, string)

- **Profile Caching** (`myclaw/agent.py`)
  - Added `_load_profile_cached()` with mtime-based invalidation
  - Thread-safe with `_profile_cache_lock`
  - FIFO cache eviction (max 100 entries)

- **Shell Timeout Configuration** (`myclaw/tools/`, `myclaw/config.py`)
  - Added `set_config()` function in tools.py
  - Configurable via `config.timeouts.shell_seconds`
  - Default: 30 seconds
  - Updated `myclaw/gateway.py` to call `tool_module.set_config(config)`

- **Knowledge Sync Cache** (`myclaw/knowledge/sync.py`)
  - Added `_get_cached_note()` function
  - Caches parsed notes with mtime validation
  - Added `clear_note_cache()` function

### Environment Variables Added

| Variable | Description |
|----------|-------------|
| `MYCLAW_OLLAMA_BASE_URL` | Override Ollama base URL |
| `MYCLAW_OPENAI_API_KEY` | Override OpenAI API key |
| `MYCLAW_ANTHROPIC_API_KEY` | Override Anthropic API key |
| `MYCLAW_GEMINI_API_KEY` | Override Gemini API key |
| `MYCLAW_GROQ_API_KEY` | Override Groq API key |
| `MYCLAW_TELEGRAM_TOKEN` | Override Telegram bot token |
| `MYCLAW_DEFAULT_PROVIDER` | Set default LLM provider |
| `MYCLAW_DEFAULT_MODEL` | Set default model |
| `MYCLAW_SWARM_ENABLED` | Enable/disable agent swarms |
| `MYCLAW_MAX_CONCURRENT_SWARMS` | Max concurrent swarms |
| `MYCLAW_SWARM_TIMEOUT` | Swarm timeout in seconds |
| `MYCLAW_SHELL_TIMEOUT` | Shell command timeout |
| `MYCLAW_LLM_TIMEOUT` | LLM request timeout |
| `MYCLAW_HTTP_TIMEOUT` | HTTP request timeout |

### Configuration Changes

- Added `TimeoutConfig` class to `myclaw/config.py`:
  ```python
  class TimeoutConfig(BaseModel):
      shell_seconds: int = 30
      llm_seconds: int = 60
      http_seconds: int = 30
  ```

## [0.0.1] - 2026-03-08

### Added

- Initial release
- Personal AI agent with flexible LLM providers
- SQLite-backed persistent memory
- Multi-agent support with delegation
- Agent Swarms system
- Knowledge base with FTS5 search
- Telegram gateway integration
- Task scheduling system

---

[Unreleased]: https://github.com/adrianx26/zensynora/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/adrianx26/zensynora/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/adrianx26/zensynora/releases/tag/v0.0.1
