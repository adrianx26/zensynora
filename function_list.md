**Function inventory (top‑level files in the repository root)**

| File | Function | Brief description |
|------|----------|-------------------|
| **cli.py** | `cli()` | Entry point for the MyClaw command‑line interface; parses args and dispatches sub‑commands. |
|  | `onboard()` | Runs the onboarding flow to configure a new user/account. |
|  | `agent()` | Starts the interactive agent REPL. |
|  | `gateway()` | Launches the API gateway server. |
|  | `mcp_server()` | Starts the MCP (MyClaw Control Plane) server. |
|  | `knowledge()` | Provides a CLI wrapper around knowledge‑base operations. |
|  | `search(query)` | Executes a knowledge‑base search and prints results. |
|  | `write()` | CLI for creating a new knowledge entry. |
|  | `read(permalink)` | Retrieves and displays a knowledge entry by its permalink. |
|  | `list()` | Lists all knowledge entries. |
|  | `sync()` | Synchronises local knowledge store with remote backend. |
|  | `tags()` | Lists or modifies tags on knowledge entries. |
|  | `memory()` | Shows memory usage statistics for the agent. |
|  | `list_sessions()` | Lists active agent sessions. |
|  | `clear(user_id)` | Clears stored data for a given user. |
|  | `swarm()` | Starts the swarm mode for coordinating multiple agents. |
|  | `status()` | Prints the current status of the MyClaw system. |
|  | `skills()` | Lists available skill modules. |
|  | `list_skills()` | Detailed listing of skill names and descriptions. |
|  | `webui(port)` | Runs the web UI on the specified port. |
|  | `benchmark(model, provider)` | Executes a benchmark for a given model/provider. |
|  | `hardware()` | Shows detected hardware resources (CPU, GPU, RAM). |
|  | `config_cmd()` | Sub‑command to view/edit configuration. |
|  | `config_encrypt()` | Encrypts the configuration file. |
|  | `config_decrypt()` | Decrypts the configuration file. |
|  | `config_status()` | Displays encryption status of the config. |
|  | `audit()` | Runs an audit of system integrity. |
|  | `audit_verify()` | Verifies audit signatures. |
|  | `audit_export(output_path)` | Exports audit logs to a file. |
|  | `audit_status()` | Shows current audit state. |
|  | `gdpr()` | CLI entry for GDPR‑related commands. |
|  | `gdpr_delete(user_id, dry_run)` | Deletes user data per GDPR; optional dry‑run. |
|  | `gdpr_export(user_id, output)` | Exports a user’s data for GDPR requests. |
|  | `mfa()` | Top‑level MFA management command. |
|  | `mfa_setup(user_id)` | Registers MFA for a user. |
|  | `mfa_verify(user_id, code)` | Verifies an MFA code. |
|  | `mfa_disable(user_id)` | Disables MFA for a user. |
|  | `mfa_status(user_id)` | Shows MFA status for a user. |
|  | `metering()` | CLI entry for metering/usage tracking. |
|  | `metering_status(user_id)` | Shows usage quota for a user. |
|  | `metering_set_quota(user_id, quota_name, limit_value)` | Sets a usage quota. |
|  | `spaces()` | CLI group for workspace/space management. |
|  | `spaces_create(name, owner, description)` | Creates a new knowledge space. |
|  | `spaces_list(user_id)` | Lists spaces accessible to a user. |
|  | `spaces_members(space_id)` | Lists members of a space. |
|  | `spaces_add_member(space_id, user_id, role, added_by)` | Adds a member to a space. |
|  | `spaces_remove_member(space_id, user_id, removed_by)` | Removes a member from a space. |
|  | `spaces_delete(space_id, owner)` | Deletes a space. |
| **onboard.py** | `run_onboard()` | Orchestrates the interactive onboarding wizard. |
| **_write_fixplan.py** | `write_fixplan()` | Helper script that writes a fixing plan to a markdown file. |
| **benchmark_runner.py** | `BenchmarkRunner.__init__` | Initializes benchmark runner with config. |
|  | `BenchmarkRunner._load_results` | Loads previous benchmark results from disk. |
|  | `BenchmarkRunner._save_results` | Persists benchmark results to disk. |
|  | `BenchmarkRunner.run_model_benchmark` | Runs a benchmark for a specific model/provider. |
|  | `BenchmarkRunner.get_comparison_table` | Generates a markdown table comparing benchmark outcomes. |
| **logging.py** | `configure_logging(level, fmt, datefmt)` | Sets up the library‑wide logger with optional format. |
| **http_session.py** | `get_session()` | Returns a thread‑safe `requests.Session` instance for HTTP calls. |
| **tools/toolbox.py** | `list_tools()` | Returns a string listing all registered custom tools. |
|  | `register_mcp_tool(name, server_name, func, documentation)` | Registers a tool for the MCP server. |
|  | `register_tool(name, code, documentation)` | Registers a generic custom tool. |
| **agent/context_builder.py** | `ContextBuilder` methods | Constructs the prompt context for the agent, handling system messages and conversation history. |
| **agent/message_router.py** | `MessageRouter` methods | Routes incoming messages to the appropriate tool or LLM handler. |
| **agent/response_handler.py** | `ResponseHandler` methods | Post‑processes LLM responses, handling tool execution results. |
| **agent/tool_executor.py** | `ToolExecutor` methods | Executes registered tools and returns formatted output to the agent. |
| **agent/_common.py** | Utility functions (e.g., `truncate`, `sanitize`) used across agent modules. |
| **knowledge/db.py** | `KnowledgeDB` methods | Simple SQLite‑based storage for knowledge entries (create, read, update, delete). |
| **exceptions.py** | Custom exception classes (`MyClawError`, `ConfigError`, etc.). |
| **context_window.py** | `trim_context(messages, max_tokens)` | Trims conversation history to fit token limits. |
| **config_encryption.py** | `encrypt_config(data, key)`, `decrypt_config(ciphertext, key)` | Helpers for encrypting/decrypting the config file. |
| **config.py** | `load_config()`, `save_config()` | Loads and persists the application configuration. |
| **audit_log.py** | Functions for writing and verifying audit logs. |
| **async_scheduler.py** | `AsyncScheduler` class – schedules periodic async tasks. |
| **metering.py** | Functions for recording usage metrics per user. |
| **knowledge_spaces.py** | Functions to create, list, and manage knowledge spaces. |
| **cost_tracker.py** | Functions that track cost usage of LLM calls. |
| **admin_dashboard.py** | Flask/Starlette view functions for the admin UI. |
| **gdpr.py** | Functions implementing GDPR export/delete operations. |
| **knowledge/advanced_search.py** | Implements semantic search over the knowledge store. |
| **offline.py** | Utilities for offline mode (caching model responses). |
| **metrics.py** | Prometheus metrics instrumentation helpers. |
| **worker_pool.py** | `WorkerPool` class – manages a pool of async workers for parallel tasks. |
| **gateway.py** | FastAPI routes that expose the MyClaw API gateway. |
| **api_server.py** | Starts the main API server process. |
| **tools/__init__.py** | Exposes tool entry points (e.g., `list_tools`). |
| **tools/web.py**, **tools/shell.py**, **tools/core.py**, **tools/scheduler.py**, **tools/session.py**, **tools/swarm.py**, **tools/files.py**, **tools/ssh.py** | Various helper functions for web interactions, shell execution, scheduling, session handling, swarm coordination, file management, and SSH operations. |
| **voice_channel.py** | Functions handling voice‑based interactions. |
| **dashboard_server.py**, **dashboard.py** | Backend for the web UI dashboard. |
| **knowledge/researcher.py**, **knowledge/graph.py**, **knowledge/storage.py**, **knowledge/parser.py**, **knowledge/sync.py** | Modules for knowledge graph construction, storage, parsing, and synchronization. |
| **plugin_system.py** | Plugin loading and registration utilities. |
| **multimodal.py** | Functions to handle multimodal inputs (images, audio). |
| **web_search.py** | Wrapper around web‑search APIs. |
| **logging_config.py** | Pre‑configured logging settings used by the app. |
| **channels/whatsapp.py**, **channels/telegram.py** | Bot integrations for WhatsApp and Telegram. |
| **profiles/__init__.py** | Profile management utilities. |
