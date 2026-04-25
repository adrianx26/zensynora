# Functions Summary

**Last Updated:** 2026-03-29
**Total Functions:** 300+

---

## Core Modules

### myclaw/agent.py

**Classes:**
- `Agent` — Personal AI agent with per-user memory, native tool calling, multi-agent delegation

**Methods:**
- `__init__(self, config, name="default", model=None, system_prompt=None, provider_name=None)` — Initialize agent
- `provider` (property) — Lazy provider initialization
- `close(self)` — Close all memory instances
- `__aenter__`, `__aexit__` — Async context manager support
- `_get_memory(user_id)` — Get or create memory for user
- `_search_knowledge_context(message, user_id, max_results)` — Auto-search knowledge base

---

### myclaw/memory.py

**Classes:**
- `AsyncSQLitePool` — Async connection pool for SQLite databases
- `SQLitePool` — Sync connection pool for SQLite databases
- `Memory` — Async SQLite-backed conversation memory with per-user isolation

**Async Functions:**
- `cleanup_on_shutdown()` — Clean up resources on shutdown

**Memory Methods:**
- `initialize()` — Initialize async connection and schema
- `add(role, content)` — Add message to history
- `get_history()` — Retrieve conversation history
- `get_recent(n)` — Get n most recent messages
- `search(query, limit)` — Search conversation history
- `cleanup_days(days)` — Delete messages older than days
- `close()` — Close database connection

---

### myclaw/tools/

**Main Functions:**
- `get_tools()` — Get list of all available tools
- `register_tool(name, code, documentation, parameters)` — Register new tool
- `list_tools()` — List all registered tools
- `list_toolbox()` — List toolbox tools
- `get_tool_documentation(name)` — Get tool docs
- `trigger_hook(name, *args)` — Trigger hook by name
- `is_tool_independent(name)` — Check if tool is independent

**Async Tool Functions:**
- `shell_async(cmd, timeout)` — Execute shell command async
- `delegate(agent_name, task, _depth)` — Delegate to agent
- `swarm_create(name, strategy, workers, coordinator, aggregation)` — Create swarm
- `swarm_assign(swarm_id, task, user_id)` — Assign task to swarm
- `swarm_status(swarm_id)` — Get swarm status
- `swarm_result(swarm_id)` — Get swarm result
- `swarm_terminate(swarm_id)` — Terminate swarm
- `swarm_list(status)` — List swarms
- `swarm_stats()` — Get swarm statistics
- `swarm_message(swarm_id, message)` — Send message to swarm

**Schedule Tool Functions:**
- `schedule(task, delay, every, user_id)` — Schedule task
- `edit_schedule(job_id, new_task, delay, every)` — Edit scheduled task
- `split_schedule(job_id, sub_tasks_json)` — Split schedule into subtasks
- `suspend_schedule(job_id)` — Suspend scheduled task
- `resume_schedule(job_id)` — Resume scheduled task
- `cancel_schedule(job_id)` — Cancel scheduled task
- `list_schedules()` — List all schedules

**Knowledge Tool Functions:**
- `write_to_knowledge(title, content)` — Write to knowledge base
- `search_knowledge(query)` — Search knowledge base
- `read_knowledge(permalink)` — Read knowledge note
- `get_knowledge_context(permalink, depth)` — Get related context
- `list_knowledge()` — List all knowledge
- `get_related_knowledge(permalink)` — Get related entities
- `sync_knowledge_base()` — Sync knowledge base
- `list_knowledge_tags()` — List all tags

---

### myclaw/provider.py

**Functions:**
- `get_provider(config, name)` — Get provider instance
- `list_providers()` — List available providers

---

### myclaw/config.py

**Functions:**
- `_start_config_watcher()` — Start file watcher for config changes
- `save_config(config)` — Save config to disk
- `load_config(path)` — Load config from disk
- `get_config()` — Get current config

---

### myclaw/context_window.py

**Classes:**
- `ContextWindow` — Configuration for context window
- `MessageToken` — Message with token tracking
- `TokenCounter` — Token counting utility
- `ContextSummary` — Summary of context for compression
- `AdvancedContextManager` — Manages large context windows

**Functions:**
- `create_context_manager(model)` — Create context manager instance
- `get_model_context_limit(model)` — Get context limit for model

**ContextManager Methods:**
- `set_system_prompt(prompt, token_count)` — Set system prompt
- `add_message(role, content)` — Add message
- `get_messages()` — Get messages
- `get_token_count()` — Get total token count
- `get_token_budget()` — Get remaining budget
- `fit_within_limit(additional_tokens)` — Check if fits
- `optimize_context(preserve_recent)` — Summarize context

---

### myclaw/semantic_cache.py

**Classes:**
- `CacheEntry` — Cached LLM response with embedding
- `SemanticCache` — Semantic similarity-based cache

**Methods:**
- `get(messages, model)` — Lookup cached response
- `set(messages, model, response, tool_calls)` — Store cached response
- `invalidate(pattern)` — Invalidate cache entries
- `clear()` — Clear all cache
- `get_stats()` — Get cache statistics
- `save()` — Persist cache to disk
- `load()` — Load cache from disk

---

### myclaw/semantic_memory.py

**Classes:**
- `Preference` — Single user preference
- `UserProfile` — User profile with learned preferences
- `PreferenceLearner` — Learns user preferences from interactions

**Methods:**
- `get_profile(user_id)` — Get user profile
- `learn_from_conversation(user_id, messages, assistant_response)` — Learn preferences
- `get_preference(user_id, key)` — Get specific preference
- `set_preference(user_id, key, value, confidence, source)` — Set preference

---

## Swarm System (myclaw/swarm/)

### myclaw/swarm/models.py

**Enums:**
- `SwarmStrategy` — PARALLEL, SEQUENTIAL, HIERARCHICAL, VOTING
- `AggregationMethod` — CONSENSUS, BEST_PICK, CONCATENATION, SYNTHESIS
- `TaskStatus` — PENDING, RUNNING, COMPLETED, FAILED, TERMINATED
- `MessageType` — TASK, RESULT, QUERY, BROADCAST, STATUS

**Classes:**
- `SwarmConfig` — Swarm configuration
- `SwarmTask` — Task assigned to swarm
- `SwarmResult` — Result from task execution
- `SwarmInfo` — Information about a swarm
- `SwarmMessage` — Inter-agent message
- `ActiveExecution` — Active task execution

---

### myclaw/swarm/orchestrator.py

**Classes:**
- `SwarmOrchestrator` — Coordinates swarm operations

**Methods:**
- `create_swarm(config, user_id)` — Create new swarm
- `execute_task(swarm_id, task)` — Execute task
- `get_status(swarm_id)` — Get swarm status
- `get_result(swarm_id)` — Get swarm result
- `terminate_swarm(swarm_id)` — Terminate swarm
- `list_swarms(status, user_id)` — List swarms
- `get_stats()` — Get swarm statistics

---

### myclaw/swarm/strategies.py

**Classes:**
- `ParallelStrategy` — All agents work simultaneously
- `SequentialStrategy` — Agents work in pipeline
- `HierarchicalStrategy` — Coordinator + workers
- `VotingStrategy` — Consensus-based decision
- `AggregationEngine` — Result aggregation

**Functions:**
- `get_strategy(strategy)` — Get strategy instance

---

### myclaw/swarm/collaboration.py

**Classes:**
- `TeamCollaboration` — Multi-agent team collaboration
- `TeamChat` — Team chat interface
- `SharedTeamContext` — Shared context for team
- `TeamMember` — Team member representation
- `CollaborationEvent` — Collaboration event
- `CollaborationEventType` — Event type enum

---

### myclaw/swarm/storage.py

**Classes:**
- `SwarmStorage` — Persistent swarm storage

---

## Knowledge System (myclaw/knowledge/)

### myclaw/knowledge/db.py

**Classes:**
- `Entity` — Database entity representation
- `EntityWithData` — Entity with observations/relations
- `KnowledgeDB` — SQLite database for knowledge storage

**Methods:**
- `get_entity(permalink)` — Get entity by permalink
- `get_entity_by_name(name)` — Get entity by name
- `search_entities(query, limit)` — Search entities
- `get_relations_from(entity_id)` — Get outgoing relations
- `get_relations_to(entity_id)` — Get incoming relations
- `get_observations(entity_id)` — Get entity observations

---

### myclaw/knowledge/storage.py

**Classes:**
- `Note` — Note representation
- `Observation` — Observation about entity
- `Relation` — Relation between entities

**Functions:**
- `write_note(name, title, observations, relations, tags, user_id, content)` — Write note
- `read_note(permalink, user_id)` — Read note
- `delete_note(permalink, user_id)` — Delete note
- `list_notes(user_id, tags)` — List all notes
- `update_note(permalink, user_id, **kwargs)` — Update note
- `get_knowledge_dir(user_id)` — Get knowledge directory
- `validate_permalink(permalink)` — Validate permalink

---

### myclaw/knowledge/parser.py

**Classes:**
- `Note` — Note data class
- `Observation` — Observation data class
- `Relation` — Relation data class

**Functions:**
- `parse_note(file_path)` — Parse note from file
- `parse_frontmatter(content)` — Parse frontmatter
- `parse_observations(content)` — Parse observations
- `parse_relations(content)` — Parse relations
- `generate_markdown(note)` — Generate markdown

---

### myclaw/knowledge/graph.py

**Functions:**
- `get_related_entities(permalink, user_id, depth, relation_type)` — Get related entities
- `get_entity_network(permalink, user_id, max_depth)` — Get entity network
- `find_path(permalink1, permalink2, user_id)` — Find path between entities
- `get_central_entities(user_id, limit)` — Get central entities
- `build_context(permalink, user_id, depth)` — Build context for entity

---

### myclaw/knowledge/sync.py

**Async Functions:**
- `_background_extraction_loop(user_id, interval_seconds)` — Background sync loop
- `sync_knowledge(user_id)` — Sync knowledge base
- `sync_and_report(user_id)` — Sync and report status
- `verify_sync(user_id)` — Verify sync status

---

## Agent System (myclaw/agents/)

### myclaw/agents/registry.py

**Enums:**
- `AgentCategory` — 10 agent category classifications
- `AgentCapability` — Agent capability tags

**Classes:**
- `AgentDefinition` — Definition of specialized agent

**Functions:**
- `get_agent(name)` — Get agent definition by name
- `list_agents(category, capability, tags, query)` — List filtered agents
- `list_agents_by_category()` — List agents grouped by category
- `get_agent_count()` — Get total agent count
- `get_categories_with_count()` — Get categories with counts

---

### myclaw/agents/discovery.py

**Classes:**
- `AgentMatch` — Matched agent with relevance score
- `AgentDiscovery` — Agent discovery and recommendations

**Methods:**
- `find_agents_for_task(task_description, required_capabilities, category, limit)` — Find best agents
- `get_swarm_composition(task, strategy)` — Suggest swarm composition
- `list_capabilities()` — List all capabilities

**Functions:**
- `integrate_with_swarm(orchestrator)` — Integrate with swarm
- `delegate_to_agent(agent_name, task, context)` — Delegate to agent
- `get_agent_info(name)` — Get detailed agent info
- `list_all_agents_brief()` — Get brief agent list

---

### myclaw/agents/medic_agent.py

**Classes:**
- `MedicAgent` — Health monitoring and self-healing agent

**Async Functions:**
- `recover_file(file_path, source)` — Recover corrupted file

---

### myclaw/agents/newtech_agent.py

**Classes:**
- `NewTechAgent` — New technology tracking agent

---

### myclaw/agents/skill_adapter.py

**Classes:**
- `SkillAdapter` — Adapts external skills to ZenSynora format

**Methods:**
- `parse_external_skill(skill_data, source)` — Parse skill from external format
- `register_skill(skill_dict)` — Register parsed skill

---

## Channels (myclaw/channels/)

### myclaw/channels/telegram.py

**Classes:**
- `TelegramGateway` — Telegram bot gateway

**Methods:**
- `start()` — Start Telegram bot
- `stop()` — Stop bot
- `send_message(chat_id, text)` — Send message
- `handle_update(update)` — Handle Telegram update

---

### myclaw/channels/whatsapp.py

**Classes:**
- `WhatsAppGateway` — WhatsApp Business API gateway

**Methods:**
- `start()` — Start WhatsApp gateway
- `stop()` — Stop gateway
- `send_message(phone, text)` — Send message
- `handle_webhook(data)` — Handle webhook

---

## Backends (myclaw/backends/)

### myclaw/backends/base.py

**Classes:**
- `AbstractBackend` — Abstract base for backends

---

### myclaw/backends/local.py

**Classes:**
- `LocalBackend` — Local terminal execution

---

### myclaw/backends/docker.py

**Classes:**
- `DockerBackend` — Docker container execution

---

### myclaw/backends/ssh.py

**Classes:**
- `SSHBackend` — SSH remote execution

---

### myclaw/backends/wsl2.py

**Classes:**
- `WSL2Backend` — WSL2 backend execution

---

### myclaw/backends/discover.py

**Functions:**
- `discover_backends()` — Discover available backends
- `get_default_backend()` — Get default backend

---

## Other Core Modules

### myclaw/api_server.py

**Classes:**
- `APIKey` — API key information
- `RateLimitEntry` — Rate limit tracking
- `APIServer` — REST API server

**Async Functions:**
- `run_api_server(**kwargs)` — Run API server

**Endpoints:**
- `root()` — Root endpoint
- `health()` — Health check
- `list_agents()` — List agents
- `list_tools()` — List tools
- `list_swarms()` — List swarms
- `create_swarm()` — Create swarm

---

### myclaw/plugin_system.py

**Classes:**
- `PluginManifest` — Plugin manifest/descriptor
- `PluginInfo` — Information about loaded plugin
- `PluginHook` — Hook for integration points
- `PluginSystem` — Main plugin system controller

**Functions:**
- `get_plugin_system()` — Get global plugin system
- `register_plugin_hook(hook_name, handler)` — Register hook
- `emit_plugin_hook(hook_name, *args, **kwargs)` — Emit hook

---

### myclaw/sandbox.py

**Classes:**
- `SecurityPolicy` — Security policy for sandbox
- `ExecutionResult` — Result of sandboxed execution
- `AuditEntry` — Security audit log entry
- `SecuritySandbox` — Security sandbox for code execution
- `SandboxedImporter` — Sandboxed import handler
- `SandboxedFunction` — Decorator for sandboxed functions

**Async Functions:**
- `execute_in_sandbox(code, timeout, max_memory_mb)` — Execute code in sandbox

---

### myclaw/self_healer.py

**Classes:**
- `ErrorInfo` — Information about error
- `FixResult` — Result of error fix attempt
- `RecoveryStrategy` — Recovery strategy

**Functions:**
- `extract_error_info(exception)` — Extract structured error info
- `suggest_fixes(error_message)` — Suggest potential fixes

**Async Functions:**
- `heal_code(code, error)` — Heal code with fixes

---

### myclaw/skill_preloader.py

**Classes:**
- `SkillPredictor` — Analyzes context to predict needed skills
- `SkillPreloader` — Proactive skill preloader with caching

**Methods:**
- `predict_and_preload(messages, current_message)` — Predict and load skills in background
- `is_preloaded(skill_name)` — Check if skill is in cache
- `get_stats()` — Get preloading statistics
- `_load_skill_code(skill_name)` — Load skill script from TOOLBOX into memory

**Async Functions:**
- `start_preloader()` — Start global preloader
- `stop_preloader()` — Stop global preloader
- `get_skill_preloader()` — Get preloader instance

---

### myclaw/skill_generator.py

**Async Functions:**
- `auto_generate_skill(description, name, parameters)` — Auto-generate skill

---

### myclaw/multimodal.py

**Classes:**
- `ImageInfo` — Image metadata
- `VideoInfo` — Video metadata

**Functions:**
- `get_image_info(path)` — Get image info
- `describe_image(path, detail)` — Describe image with vision
- `create_thumbnail(path, output_path, size)` — Create thumbnail
- `get_video_info(path)` — Get video info
- `record_screen(output_path, duration)` — Record screen
- `image_to_base64(path)` — Convert image to base64

**Async Functions:**
- `analyze_image_async(path, prompt)` — Analyze image async
- `extract_video_frames(video_path, output_dir, fps, max_frames)` — Extract frames
- `summarize_video(video_path)` — Summarize video
- `process_screenshot(region)` — Take screenshot

---

### myclaw/voice_channel.py

**Classes:**
- `VoiceConfig` — Voice configuration
- `AudioSegment` — Audio segment data
- `TranscriptionResult` — Speech transcription result
- `SynthesisResult` — TTS synthesis result
- `TTSProvider` — Text-to-Speech provider interface
- `STTProvider` — Speech-to-Text provider interface
- `GTTSProvider` — Google TTS provider
- `pyttsx3Provider` — Offline TTS provider
- `WhisperSTTProvider` — Whisper-based STT
- `VoskSTTProvider` — Vosk offline STT
- `VoiceChannel` — Main voice controller with VAD and streaming
- `VoiceActivityDetector` — VAD for detecting speech in audio chunks

**Methods:**
- `speak(text, provider)` — Synthesize text to speech
- `listen(audio_data, provider)` — Transcribe audio to text
- `listen_stream(stream_id, audio_chunk)` — Process real-time audio stream

**Functions:**
- `create_voice_channel(config)` — Create voice channel

---

### myclaw/web_search.py

**Async Functions:**
- `_fetch_url(url, headers, timeout)` — Fetch URL content
- `search_web(query, num_results, source)` — Search web
- `search_wikipedia(query)` — Search Wikipedia
- `search_news(query, num_results)` — Search news
- `get_webpage_content(url)` — Get webpage content
- `search_multiple(query, sources)` — Search multiple sources

---

### myclaw/dashboard.py

**Classes:**
- `MyClawDashboard` — Web-based administrative interface

**Methods:**
- `start()` — Start the dashboard server (via dashboard_server)
- `_log(message, level)` — Internal dashboard logging

**Async Functions:**
- `dashboard_app(dashboard)` — Create FastAPI app context (async manager)

---

### myclaw/dashboard_server.py

**Functions:**
- `create_dashboard_app(dashboard)` — Factory to create the FastAPI web application

---

### myclaw/logging_config.py

**Functions:**
- `setup_logging(level, log_file)` — Setup logging configuration

---

### myclaw/gateway.py

**Classes:**
- `Gateway` — Base gateway class

---

### myclaw/exceptions.py

**Classes:**
- Custom exception classes for MyClaw

---

## Hub System (myclaw/hub/)

**Functions:**
- Hub-related utilities (see `myclaw/hub/__init__.py`)

---

## Agent Profiles (myclaw/agent_profiles/)

### Core Development
- `backend-developer.md` — Backend development specialist
- `frontend-developer.md` — Frontend development specialist

### Language Specialists
- `python-pro.md` — Python ecosystem master

### Infrastructure
- `devops-engineer.md` — DevOps specialist

### Quality & Security
- `code-reviewer.md` — Code quality guardian

### Data & AI
- `llm-architect.md` — LLM system architect

### Meta & Orchestration
- `multi-agent-coordinator.md` — Multi-agent orchestration

---

## Swarm Strategies (myclaw/swarm/strategies.py)

- `ParallelStrategy.execute()` — Execute tasks in parallel
- `SequentialStrategy.execute()` — Execute tasks sequentially
- `HierarchicalStrategy.execute()` — Execute with coordinator
- `VotingStrategy.execute()` — Execute with voting
- `AggregationEngine.aggregate()` — Aggregate results

---

## Agent Categories (136+ agents)

| Category | Count |
|----------|-------|
| Core Development | 12 |
| Language Specialists | 27 |
| Infrastructure | 16 |
| Quality & Security | 16 |
| Data & AI | 12 |
| Developer Experience | 13 |
| Specialized Domains | 12 |
| Business & Product | 11 |
| Meta & Orchestration | 12 |
| Research & Analysis | 7 |
| **Total** | **136** |

---

## Statistics

| Metric | Count |
|--------|-------|
| Total Python Modules | 40+ |
| Total Functions | 300+ |
| Total Classes | 100+ |
| Total Agent Definitions | 136+ |

---

*Generated from codebase analysis*
