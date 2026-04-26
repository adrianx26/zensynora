# Graph Report - zensynora  (2026-04-26)

## Corpus Check
- 242 files · ~406,542 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 5588 nodes · 16745 edges · 111 communities detected
- Extraction: 41% EXTRACTED · 59% INFERRED · 0% AMBIGUOUS · INFERRED: 9894 edges (avg confidence: 0.62)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 96|Community 96]]
- [[_COMMUNITY_Community 97|Community 97]]
- [[_COMMUNITY_Community 98|Community 98]]
- [[_COMMUNITY_Community 100|Community 100]]
- [[_COMMUNITY_Community 101|Community 101]]
- [[_COMMUNITY_Community 102|Community 102]]
- [[_COMMUNITY_Community 103|Community 103]]
- [[_COMMUNITY_Community 104|Community 104]]
- [[_COMMUNITY_Community 106|Community 106]]
- [[_COMMUNITY_Community 107|Community 107]]
- [[_COMMUNITY_Community 108|Community 108]]
- [[_COMMUNITY_Community 109|Community 109]]
- [[_COMMUNITY_Community 110|Community 110]]
- [[_COMMUNITY_Community 112|Community 112]]
- [[_COMMUNITY_Community 115|Community 115]]
- [[_COMMUNITY_Community 116|Community 116]]
- [[_COMMUNITY_Community 117|Community 117]]
- [[_COMMUNITY_Community 118|Community 118]]
- [[_COMMUNITY_Community 119|Community 119]]
- [[_COMMUNITY_Community 120|Community 120]]
- [[_COMMUNITY_Community 121|Community 121]]
- [[_COMMUNITY_Community 122|Community 122]]
- [[_COMMUNITY_Community 123|Community 123]]
- [[_COMMUNITY_Community 124|Community 124]]
- [[_COMMUNITY_Community 125|Community 125]]
- [[_COMMUNITY_Community 126|Community 126]]
- [[_COMMUNITY_Community 127|Community 127]]
- [[_COMMUNITY_Community 128|Community 128]]
- [[_COMMUNITY_Community 129|Community 129]]
- [[_COMMUNITY_Community 130|Community 130]]
- [[_COMMUNITY_Community 131|Community 131]]
- [[_COMMUNITY_Community 132|Community 132]]
- [[_COMMUNITY_Community 133|Community 133]]

## God Nodes (most connected - your core abstractions)
1. `Request` - 376 edges
2. `SessionManager` - 233 edges
3. `Agent` - 208 edges
4. `CrawlStats` - 193 edges
5. `Response` - 168 edges
6. `TextHandler` - 144 edges
7. `info()` - 140 edges
8. `CheckpointData` - 140 edges
9. `TamperEvidentAuditLog` - 134 edges
10. `Selector` - 131 edges

## Surprising Connections (you probably didn't know these)
- `Record a response time sample.` --uses--> `KnowledgeDB`  [INFERRED]
  myclaw\admin_dashboard.py → myclaw\knowledge\db.py
- `Check health of configured LLM providers.` --uses--> `KnowledgeDB`  [INFERRED]
  myclaw\admin_dashboard.py → myclaw\knowledge\db.py
- `Agent` --uses--> `Create a thumbnail of an image.          Args:         path: Source image path`  [INFERRED]
  myclaw\agent.py → myclaw\multimodal.py
- `Agent` --uses--> `Extract frames from a video.          Args:         video_path: Path to video fi`  [INFERRED]
  myclaw\agent.py → myclaw\multimodal.py
- `Agent` --uses--> `Telegram channel with multi-agent routing, scheduling, and dynamic tools.`  [INFERRED]
  myclaw\agent.py → myclaw\channels\telegram.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.01
Nodes (465): CheckpointData, CheckpointManager, Container for checkpoint state., Manages saving and loading checkpoint state to/from disk., Check if a checkpoint exists., Save checkpoint data to disk atomically., Load checkpoint data from disk.          Returns None if no checkpoint exists, Delete checkpoint file after successful completion. (+457 more)

### Community 1 - "Community 1"
Cohesion: 0.01
Nodes (483): get_provider_health(), Check health of configured LLM providers., Record a response time sample., record_response_time(), _browse_alternative_hint(), admin_costs(), chat_websocket(), _ensure_registry() (+475 more)

### Community 2 - "Community 2"
Cohesion: 0.01
Nodes (363): ABC, Make GET HTTP request to a group of URLs and for each URL, return a structured o, Use playwright to open a browser to fetch a URL and return a structured output o, Use playwright to open a browser, then fetch a group of URLs at the same time, a, Use the stealthy fetcher to fetch a URL and return a structured output of the re, Use the stealthy fetcher to fetch a group of URLs at the same time, and for each, Make GET HTTP request to a URL and return a structured output of the result., AsyncSession (+355 more)

### Community 3 - "Community 3"
Cohesion: 0.01
Nodes (320): QuotesSpider, Example 4: Python - Spider (auto-crawling framework)  Scrapes ALL pages of quo, bulk_fetch(), bulk_get(), bulk_stealthy_fetch(), _content_translator(), fetch(), get() (+312 more)

### Community 4 - "Community 4"
Cohesion: 0.01
Nodes (288): _detect_browse_failure(), get_last_active_time(), _get_profile_cache_key(), _is_empty_response(), _load_profile_cached(), _load_profile_cached_async(), Agent - Core AI agent implementation for MyClaw/Zensynora.  This module provid, Generate an alternative-source suggestion for a failed browse. (+280 more)

### Community 5 - "Community 5"
Cohesion: 0.03
Nodes (206): APIKey, APIServer, create_api_server(), RateLimitEntry, REST API Server for External Integration  Provides a comprehensive REST API for, Save API keys to disk., Generate a new API key.                  Args:             name: Name/identifier, Check if request is within rate limit.                  Returns:             Tru (+198 more)

### Community 6 - "Community 6"
Cohesion: 0.02
Nodes (243): Agent, KnowledgeGapCache, KnowledgeSearchResult, admin_dashboard(), api_add_member(), api_create_space(), api_get_space(), api_list_spaces() (+235 more)

### Community 7 - "Community 7"
Cohesion: 0.02
Nodes (162): Tamper-evident audit logging with hash-chain integrity and rotation., Persistent JSONL audit log where each entry includes previous entry hash., Load HMAC secret from env or generate and persist one., Cryptographic HMAC-SHA256 over the entry payload., TamperEvidentAuditLog, audit_status(), Show audit log status and recent entries., clear_hooks() (+154 more)

### Community 8 - "Community 8"
Cohesion: 0.03
Nodes (187): Enum, analyze_logs_deterministic(), check_system_health(), create_backup(), enable_hash_check(), generate_evolution_plan(), get_detailed_health_report(), get_health_report() (+179 more)

### Community 9 - "Community 9"
Cohesion: 0.02
Nodes (173): build_dashboard_data(), get_active_session_count(), get_avg_response_time(), get_kb_stats(), get_recent_routing_decisions(), get_tool_stats(), log_routing_decision(), Admin dashboard data provider for ZenSynora.  Aggregates metrics from multiple s (+165 more)

### Community 10 - "Community 10"
Cohesion: 0.02
Nodes (137): shell(), AdvancedContextManager, ContextSummary, ContextWindow, create_context_manager(), estimate_messages_tokens(), estimate_tokens(), get_model_context_limit() (+129 more)

### Community 11 - "Community 11"
Cohesion: 0.02
Nodes (135): provider(), Web Authentication — API key and admin access control.  Provides FastAPI depen, Validate the admin API key sent in the X-API-Key header.      Raises:, require_admin_api_key(), get_cache_stats(), Return semantic cache statistics., get_metrics(), _NoopMetrics (+127 more)

### Community 12 - "Community 12"
Cohesion: 0.04
Nodes (64): AbstractBackend, AbstractBackend, BackendRegistry, _check_availability(), get_all(), get_available(), get_by_type(), get_type() (+56 more)

### Community 13 - "Community 13"
Cohesion: 0.05
Nodes (86): BaseModel, AgentDefaults, AgentsConfig, AnthropicConfig, _apply_env_overrides(), BackendConfig, ChannelsConfig, _ConfigFileWatcher (+78 more)

### Community 14 - "Community 14"
Cohesion: 0.04
Nodes (38): AudioSegment, create_voice_channel(), GTTSProvider, pyttsx3Provider, Voice Channel Module (TTS/STT)  Provides voice interaction capabilities: - Te, Transcribe speech from audio.                  Args:             audio_data:, Transcribe from audio stream., Google Text-to-Speech provider. (+30 more)

### Community 15 - "Community 15"
Cohesion: 0.04
Nodes (38): emit_plugin_hook(), get_plugin_system(), PluginHook, PluginInfo, PluginManifest, PluginSystem, Plugin System for Third-Party Extensions  Provides a comprehensive plugin archit, Main plugin system controller.          Features:     - Plugin discovery and loa (+30 more)

### Community 16 - "Community 16"
Cohesion: 0.04
Nodes (43): __BuildRequest(), delete(), __Execute(), extract(), fetch(), get(), install(), __ParseExtractArguments() (+35 more)

### Community 17 - "Community 17"
Cohesion: 0.05
Nodes (32): err(), header(), info(), main(), Check if a file/directory name matches exclusion patterns., Recursively upload a local directory to a remote path via SFTP., Run a command on the remote machine, streaming output. Returns exit code., run_remote() (+24 more)

### Community 18 - "Community 18"
Cohesion: 0.09
Nodes (36): AgentDiscovery, AgentMatch, delegate_to_agent(), get_agent_info(), integrate_with_swarm(), list_all_agents_brief(), Agent Discovery and Integration Module  Provides utilities for discovering and, Suggest a swarm composition for a task.          Args:             task: Task (+28 more)

### Community 19 - "Community 19"
Cohesion: 0.06
Nodes (17): test_autoscraper(), Shared utilities for the myclaw.agent package.  This module centralises imports, ContextBuilder, ContextBuilder — assembles conversation context from memory + knowledge base., Builds the full context window for an LLM call., Assemble messages list with system prompt, KB context, and history., MessageRouter, MessageRouter — routes incoming messages to the appropriate handler.  Extracte (+9 more)

### Community 20 - "Community 20"
Cohesion: 0.07
Nodes (14): dashboard_app(), DashboardState, MyClawDashboard, Web Dashboard for MyClaw Admin UI  Provides a web-based administrative interfa, Current state of the dashboard., Web Dashboard for MyClaw administration., Start the dashboard server., Get overview statistics. (+6 more)

### Community 21 - "Community 21"
Cohesion: 0.11
Nodes (15): audit_log(), configure_logging(), get_logger(), log_performance(), LogContext, Standardized logging configuration for MyClaw.  This module provides consisten, Context manager for adding extra fields to log records.          Usage:, Get a configured logger with standardized format.          Args:         name (+7 more)

### Community 22 - "Community 22"
Cohesion: 0.12
Nodes (13): async_fetch(), fetcher(), test_automation(), test_basic_fetch(), test_cdp_url_invalid(), test_cookies_loading(), test_properties(), TestDynamicFetcherAsync (+5 more)

### Community 23 - "Community 23"
Cohesion: 0.11
Nodes (15): OriginalHTMLTranslator, OriginalXPathExpr, Protocol, css_to_xpath(), from_xpath(), HTMLTranslator, Most of this file is an adapted version of the parsel library's translator with, Return the translated XPath version of a given CSS query (+7 more)

### Community 24 - "Community 24"
Cohesion: 0.2
Nodes (1): loadMessages()

### Community 25 - "Community 25"
Cohesion: 0.47
Nodes (8): err(), header(), info(), main(), ok(), Run a command on the remote machine via SSH.     Returns (exit_code, stdout_text, run_cmd(), warn()

### Community 26 - "Community 26"
Cohesion: 0.25
Nodes (6): get_model_metadata(), get_optimal_model(), LLM Capability Library for ZenSynora. Tracks model limits, capabilities, and ben, Retrieve metadata for a specific model, with nickname resolution., Find the best model for a given tier and capability set among registry., Intelligent Agent Router for ZenSynora. Analyzes task complexity and intent to s

### Community 27 - "Community 27"
Cohesion: 0.25
Nodes (4): Test harmful default arguments, Test default stealth flags, Test default disabled resources, TestConstants

### Community 28 - "Community 28"
Cohesion: 0.5
Nodes (3): get_session(), Shared :class:`requests.Session` used throughout the codebase.  Having a single, Return a lazily‑created, thread‑safe ``requests.Session``.      The first call c

### Community 30 - "Community 30"
Cohesion: 1.0
Nodes (1): Example 1: Python - FetcherSession (persistent HTTP session with Chrome TLS fing

### Community 31 - "Community 31"
Cohesion: 1.0
Nodes (1): Example 2: Python - DynamicSession (Playwright browser automation, visible)  S

### Community 32 - "Community 32"
Cohesion: 1.0
Nodes (1): Example 3: Python - StealthySession (Patchright stealth browser, visible)  Scr

### Community 40 - "Community 40"
Cohesion: 1.0
Nodes (1): Reconstruct a Job from serialised metadata (requires func reference).

### Community 41 - "Community 41"
Cohesion: 1.0
Nodes (1): Lazy-load tiktoken encoder if available.

### Community 42 - "Community 42"
Cohesion: 1.0
Nodes (1): Estimate token count for text.          Uses tiktoken for OpenAI models when a

### Community 43 - "Community 43"
Cohesion: 1.0
Nodes (1): Estimate tokens for a message list.          OpenAI uses approximately:

### Community 44 - "Community 44"
Cohesion: 1.0
Nodes (1): Get the fix pattern for an error message.

### Community 45 - "Community 45"
Cohesion: 1.0
Nodes (1): Store the agent registry (names only are synced; objects stay local).

### Community 46 - "Community 46"
Cohesion: 1.0
Nodes (1): Return registered agent names (suitable for multi-worker discovery).

### Community 47 - "Community 47"
Cohesion: 1.0
Nodes (1): Return the name of the default agent, if any.

### Community 48 - "Community 48"
Cohesion: 1.0
Nodes (1): Record that a hook has been registered (metadata only).

### Community 49 - "Community 49"
Cohesion: 1.0
Nodes (1): Return map of event_type -> list of registered callback names.

### Community 50 - "Community 50"
Cohesion: 1.0
Nodes (1): Initialise or update rate-limit bucket config for a tool.

### Community 51 - "Community 51"
Cohesion: 1.0
Nodes (1): Token-bucket check. Returns True if call is allowed.

### Community 52 - "Community 52"
Cohesion: 1.0
Nodes (1): Remaining calls in current window.

### Community 53 - "Community 53"
Cohesion: 1.0
Nodes (1): Store user_id -> chat_id mapping.

### Community 54 - "Community 54"
Cohesion: 1.0
Nodes (1): Retrieve chat_id for user_id.

### Community 55 - "Community 55"
Cohesion: 1.0
Nodes (1): Store notification callback (local only; Redis backend logs a warning).

### Community 56 - "Community 56"
Cohesion: 1.0
Nodes (1): Retrieve notification callback.

### Community 58 - "Community 58"
Cohesion: 1.0
Nodes (1): Execute a command and return output.                  Args:             command:

### Community 59 - "Community 59"
Cohesion: 1.0
Nodes (1): Upload a file to the remote environment.                  Args:             loca

### Community 60 - "Community 60"
Cohesion: 1.0
Nodes (1): Download a file from the remote environment.                  Args:

### Community 61 - "Community 61"
Cohesion: 1.0
Nodes (1): Get the backend type identifier.                  Returns:             String li

### Community 62 - "Community 62"
Cohesion: 1.0
Nodes (1): Internal check for backend availability.                  Implementations should

### Community 63 - "Community 63"
Cohesion: 1.0
Nodes (1): Register a backend.                  Args:             backend: Backend instance

### Community 64 - "Community 64"
Cohesion: 1.0
Nodes (1): Get all registered backends.                  Returns:             List of backe

### Community 65 - "Community 65"
Cohesion: 1.0
Nodes (1): Get all available backends.                  Returns:             List of availa

### Community 66 - "Community 66"
Cohesion: 1.0
Nodes (1): Get a backend by type identifier.                  Args:             backend_typ

### Community 67 - "Community 67"
Cohesion: 1.0
Nodes (1): Clear all registered backends.

### Community 68 - "Community 68"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 69 - "Community 69"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 70 - "Community 70"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 71 - "Community 71"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 72 - "Community 72"
Cohesion: 1.0
Nodes (1): Create from dictionary.

### Community 74 - "Community 74"
Cohesion: 1.0
Nodes (1): Convert current attributes to JSON bytes if the attributes are JSON serializable

### Community 75 - "Community 75"
Cohesion: 1.0
Nodes (1): Generate a CSS selector for the current element         :return: A string of th

### Community 76 - "Community 76"
Cohesion: 1.0
Nodes (1): Generate a complete CSS selector for the current element         :return: A str

### Community 77 - "Community 77"
Cohesion: 1.0
Nodes (1): Generate an XPath selector for the current element         :return: A string of

### Community 78 - "Community 78"
Cohesion: 1.0
Nodes (1): Generate a complete XPath selector for the current element         :return: A s

### Community 79 - "Community 79"
Cohesion: 1.0
Nodes (1): Using the identifier, we search the storage and return the unique properties of

### Community 80 - "Community 80"
Cohesion: 1.0
Nodes (1): If you want to hash identifier in your storage system, use this safer

### Community 81 - "Community 81"
Cohesion: 1.0
Nodes (1): Support selecting attribute values using ::attr() pseudo-element

### Community 82 - "Community 82"
Cohesion: 1.0
Nodes (1): Support selecting text nodes using ::text pseudo-element

### Community 86 - "Community 86"
Cohesion: 1.0
Nodes (1): Get a copy of all configured proxies.

### Community 88 - "Community 88"
Cohesion: 1.0
Nodes (1): Get the total number of pages

### Community 89 - "Community 89"
Cohesion: 1.0
Nodes (1): Get the number of busy pages

### Community 91 - "Community 91"
Cohesion: 1.0
Nodes (1): True if the crawl completed normally (not paused).

### Community 95 - "Community 95"
Cohesion: 1.0
Nodes (1): Fixture to set up URLs for testing.

### Community 96 - "Community 96"
Cohesion: 1.0
Nodes (1): Fixture to set up URLs for testing.

### Community 97 - "Community 97"
Cohesion: 1.0
Nodes (1): Test that proxy-related errors are detected

### Community 98 - "Community 98"
Cohesion: 1.0
Nodes (1): Test that non-proxy errors are not detected as proxy errors

### Community 100 - "Community 100"
Cohesion: 1.0
Nodes (1): Test doing a basic fetch request with multiple statuses

### Community 101 - "Community 101"
Cohesion: 1.0
Nodes (1): Test if automation breaks the code or not

### Community 102 - "Community 102"
Cohesion: 1.0
Nodes (1): Test if different arguments break the code or not

### Community 103 - "Community 103"
Cohesion: 1.0
Nodes (1): Test if invalid CDP URLs raise appropriate exceptions

### Community 104 - "Community 104"
Cohesion: 1.0
Nodes (1): Test if different arguments break the code or not

### Community 106 - "Community 106"
Cohesion: 1.0
Nodes (1): Fixture to create a StealthyFetcher instance for the entire test class

### Community 107 - "Community 107"
Cohesion: 1.0
Nodes (1): Fixture to set up URLs for testing

### Community 108 - "Community 108"
Cohesion: 1.0
Nodes (1): Test if different arguments break the code or not

### Community 109 - "Community 109"
Cohesion: 1.0
Nodes (1): Fixture to create a Fetcher instance for the entire test class

### Community 110 - "Community 110"
Cohesion: 1.0
Nodes (1): Fixture to set up URLs for testing

### Community 112 - "Community 112"
Cohesion: 1.0
Nodes (1): Test relocating element after structure change in async mode

### Community 115 - "Community 115"
Cohesion: 1.0
Nodes (1): Test that successful calls don't trigger retries.

### Community 116 - "Community 116"
Cohesion: 1.0
Nodes (1): Test that retries happen on retriable exceptions.

### Community 117 - "Community 117"
Cohesion: 1.0
Nodes (1): Test that exception is raised after max retries.

### Community 118 - "Community 118"
Cohesion: 1.0
Nodes (1): Test that backoff delay increases exponentially.

### Community 119 - "Community 119"
Cohesion: 1.0
Nodes (1): Test retry on httpx.ConnectError.

### Community 120 - "Community 120"
Cohesion: 1.0
Nodes (1): Test retry on httpx.TimeoutException.

### Community 121 - "Community 121"
Cohesion: 1.0
Nodes (1): Test retry on httpx.HTTPStatusError (5xx).

### Community 122 - "Community 122"
Cohesion: 1.0
Nodes (1): Test that 4xx client errors are not retried.

### Community 123 - "Community 123"
Cohesion: 1.0
Nodes (1): Test that timeout errors return structured guidance with Wayback suggestion.

### Community 124 - "Community 124"
Cohesion: 1.0
Nodes (1): Test that connection errors return structured guidance.

### Community 125 - "Community 125"
Cohesion: 1.0
Nodes (1): Test that 404 errors return structured guidance.

### Community 126 - "Community 126"
Cohesion: 1.0
Nodes (1): Test that 403 errors return structured guidance.

### Community 127 - "Community 127"
Cohesion: 1.0
Nodes (1): Test that other HTTP errors return structured guidance.

### Community 128 - "Community 128"
Cohesion: 1.0
Nodes (1): Test that generic request exceptions return structured guidance.

### Community 129 - "Community 129"
Cohesion: 1.0
Nodes (1): Test that unexpected exceptions return structured guidance.

### Community 130 - "Community 130"
Cohesion: 1.0
Nodes (1): Test that successful browse returns expected format.

### Community 131 - "Community 131"
Cohesion: 1.0
Nodes (1): Test that long content is truncated.

### Community 132 - "Community 132"
Cohesion: 1.0
Nodes (1): Test that errors still contain 'Error' prefix.

### Community 133 - "Community 133"
Cohesion: 1.0
Nodes (1): Test async waiting for token availability.

## Knowledge Gaps
- **931 isolated node(s):** `Check if a file/directory name matches exclusion patterns.`, `Recursively upload a local directory to a remote path via SFTP.`, `Run a command on the remote machine, streaming output. Returns exit code.`, `Set up request routing, task timer, and guardrails.          Returns:`, `Build the message context: knowledge search + system prompt + hooks.          Re` (+926 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 24`** (10 nodes): `clearHistory()`, `getApiBase()`, `getSavedTheme()`, `getWsBase()`, `loadMessages()`, `saveMessages()`, `sendMessage()`, `setThemeDoc()`, `App.tsx`, `main.tsx`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 30`** (2 nodes): `Example 1: Python - FetcherSession (persistent HTTP session with Chrome TLS fing`, `01_fetcher_session.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 31`** (2 nodes): `Example 2: Python - DynamicSession (Playwright browser automation, visible)  S`, `02_dynamic_session.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 32`** (2 nodes): `Example 3: Python - StealthySession (Patchright stealth browser, visible)  Scr`, `03_stealthy_session.py`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 40`** (1 nodes): `Reconstruct a Job from serialised metadata (requires func reference).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 41`** (1 nodes): `Lazy-load tiktoken encoder if available.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 42`** (1 nodes): `Estimate token count for text.          Uses tiktoken for OpenAI models when a`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 43`** (1 nodes): `Estimate tokens for a message list.          OpenAI uses approximately:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 44`** (1 nodes): `Get the fix pattern for an error message.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 45`** (1 nodes): `Store the agent registry (names only are synced; objects stay local).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 46`** (1 nodes): `Return registered agent names (suitable for multi-worker discovery).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 47`** (1 nodes): `Return the name of the default agent, if any.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 48`** (1 nodes): `Record that a hook has been registered (metadata only).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 49`** (1 nodes): `Return map of event_type -> list of registered callback names.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 50`** (1 nodes): `Initialise or update rate-limit bucket config for a tool.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 51`** (1 nodes): `Token-bucket check. Returns True if call is allowed.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 52`** (1 nodes): `Remaining calls in current window.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 53`** (1 nodes): `Store user_id -> chat_id mapping.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 54`** (1 nodes): `Retrieve chat_id for user_id.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 55`** (1 nodes): `Store notification callback (local only; Redis backend logs a warning).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 56`** (1 nodes): `Retrieve notification callback.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 58`** (1 nodes): `Execute a command and return output.                  Args:             command:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 59`** (1 nodes): `Upload a file to the remote environment.                  Args:             loca`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 60`** (1 nodes): `Download a file from the remote environment.                  Args:`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 61`** (1 nodes): `Get the backend type identifier.                  Returns:             String li`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 62`** (1 nodes): `Internal check for backend availability.                  Implementations should`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 63`** (1 nodes): `Register a backend.                  Args:             backend: Backend instance`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 64`** (1 nodes): `Get all registered backends.                  Returns:             List of backe`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 65`** (1 nodes): `Get all available backends.                  Returns:             List of availa`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 66`** (1 nodes): `Get a backend by type identifier.                  Args:             backend_typ`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 67`** (1 nodes): `Clear all registered backends.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 68`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 69`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 70`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 71`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 72`** (1 nodes): `Create from dictionary.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 74`** (1 nodes): `Convert current attributes to JSON bytes if the attributes are JSON serializable`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 75`** (1 nodes): `Generate a CSS selector for the current element         :return: A string of th`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 76`** (1 nodes): `Generate a complete CSS selector for the current element         :return: A str`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 77`** (1 nodes): `Generate an XPath selector for the current element         :return: A string of`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 78`** (1 nodes): `Generate a complete XPath selector for the current element         :return: A s`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 79`** (1 nodes): `Using the identifier, we search the storage and return the unique properties of`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 80`** (1 nodes): `If you want to hash identifier in your storage system, use this safer`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 81`** (1 nodes): `Support selecting attribute values using ::attr() pseudo-element`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 82`** (1 nodes): `Support selecting text nodes using ::text pseudo-element`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 86`** (1 nodes): `Get a copy of all configured proxies.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 88`** (1 nodes): `Get the total number of pages`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 89`** (1 nodes): `Get the number of busy pages`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 91`** (1 nodes): `True if the crawl completed normally (not paused).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 95`** (1 nodes): `Fixture to set up URLs for testing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 96`** (1 nodes): `Fixture to set up URLs for testing.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 97`** (1 nodes): `Test that proxy-related errors are detected`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 98`** (1 nodes): `Test that non-proxy errors are not detected as proxy errors`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 100`** (1 nodes): `Test doing a basic fetch request with multiple statuses`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 101`** (1 nodes): `Test if automation breaks the code or not`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 102`** (1 nodes): `Test if different arguments break the code or not`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 103`** (1 nodes): `Test if invalid CDP URLs raise appropriate exceptions`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 104`** (1 nodes): `Test if different arguments break the code or not`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 106`** (1 nodes): `Fixture to create a StealthyFetcher instance for the entire test class`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 107`** (1 nodes): `Fixture to set up URLs for testing`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 108`** (1 nodes): `Test if different arguments break the code or not`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 109`** (1 nodes): `Fixture to create a Fetcher instance for the entire test class`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 110`** (1 nodes): `Fixture to set up URLs for testing`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 112`** (1 nodes): `Test relocating element after structure change in async mode`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 115`** (1 nodes): `Test that successful calls don't trigger retries.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 116`** (1 nodes): `Test that retries happen on retriable exceptions.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 117`** (1 nodes): `Test that exception is raised after max retries.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 118`** (1 nodes): `Test that backoff delay increases exponentially.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 119`** (1 nodes): `Test retry on httpx.ConnectError.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 120`** (1 nodes): `Test retry on httpx.TimeoutException.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 121`** (1 nodes): `Test retry on httpx.HTTPStatusError (5xx).`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 122`** (1 nodes): `Test that 4xx client errors are not retried.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 123`** (1 nodes): `Test that timeout errors return structured guidance with Wayback suggestion.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 124`** (1 nodes): `Test that connection errors return structured guidance.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 125`** (1 nodes): `Test that 404 errors return structured guidance.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 126`** (1 nodes): `Test that 403 errors return structured guidance.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 127`** (1 nodes): `Test that other HTTP errors return structured guidance.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 128`** (1 nodes): `Test that generic request exceptions return structured guidance.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 129`** (1 nodes): `Test that unexpected exceptions return structured guidance.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 130`** (1 nodes): `Test that successful browse returns expected format.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 131`** (1 nodes): `Test that long content is truncated.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 132`** (1 nodes): `Test that errors still contain 'Error' prefix.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 133`** (1 nodes): `Test async waiting for token availability.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `Agent` connect `Community 6` to `Community 1`, `Community 10`, `Community 4`, `Community 7`?**
  _High betweenness centrality (0.073) - this node is a cross-community bridge._
- **Why does `Request` connect `Community 0` to `Community 16`, `Community 1`, `Community 2`, `Community 4`?**
  _High betweenness centrality (0.072) - this node is a cross-community bridge._
- **Why does `SessionManager` connect `Community 0` to `Community 1`, `Community 2`, `Community 4`?**
  _High betweenness centrality (0.064) - this node is a cross-community bridge._
- **Are the 364 inferred relationships involving `Request` (e.g. with `CheckpointData` and `CheckpointManager`) actually correct?**
  _`Request` has 364 INFERRED edges - model-reasoned connections that need verification._
- **Are the 219 inferred relationships involving `SessionManager` (e.g. with `CrawlerEngine` and `Orchestrates the crawling process.`) actually correct?**
  _`SessionManager` has 219 INFERRED edges - model-reasoned connections that need verification._
- **Are the 179 inferred relationships involving `Agent` (e.g. with `Memory` and `TaskStatus`) actually correct?**
  _`Agent` has 179 INFERRED edges - model-reasoned connections that need verification._
- **Are the 187 inferred relationships involving `CrawlStats` (e.g. with `CrawlerEngine` and `Orchestrates the crawling process.`) actually correct?**
  _`CrawlStats` has 187 INFERRED edges - model-reasoned connections that need verification._