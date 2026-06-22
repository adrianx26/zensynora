# ZenSynora Full Application Analysis

**Project:** ZenSynora (MyClaw)  
**Version:** 0.4.1  
**Language:** Python 3.11+ (backend) + TypeScript/React (frontend)  
**License:** AGPL-3.0  
**Author:** Adrian Petrescu  
**Test Count:** 456 collected tests  
**Corpus:** 242 files, ~406k words (per graphify)

---

## 1. Executive Summary

ZenSynora is a privacy-first, multi-tenant AI agent framework designed for local or cloud deployment. It provides a complete production stack: LLM abstraction with 6+ providers, persistent per-user SQLite memory with FTS5 search, a 137-agent swarm registry, dynamic tool sandboxing, plugin marketplace with HMAC-signed manifests, OpenTelemetry tracing, circuit breaker resilience, and multi-channel gateways (Telegram, WhatsApp, Discord, Web UI, CLI).

The codebase reflects 12 structured development sprints with clear module ownership and progressive feature layering.

---

## 2. Architecture Overview

### 2.1 High-Level Flow

```
User (Telegram/WhatsApp/Discord/Web/CLI)
  |
  v
Gateway Adapter (channels/)
  |
  v
Auth Layer (JWT / OAuth2 PKCE / UserContext via contextvars)
  |
  v
Agent.think() ─────────────────────────────────────┐
  |-- _route_message()      (task timer, guardrails) |
  |-- _build_context()      (memory + knowledge RAG) |
  |-- _provider_chat()      (LLM call via provider.py)
  |      |-- semantic_cache lookup                    |
  |      |-- retry / backoff                          |
  |      |-- circuit breaker (resilience/)            |
  |      |-- fallback chain (resilience/)             |
  |-- _execute_tools()      (parallel + sequential)  |
  |-- _handle_summarization() (memory cleanup)        |
  v                                                   |
Response Stream ──────────────────────────────────────┘
```

### 2.2 Module Map

| Module | Purpose | Sprint |
|---|---|---|
| `agent.py` | Core orchestrator (`think`, `complete_structured`, `stream_think`) | Core |
| `agent_internals/` | Phase helpers: `ResponseHandler`, context building, routing | 5, 9 |
| `agents/` | 137-agent YAML registry + loader | 12 |
| `auth/` | JWT verification (HS256/RS256/ES256) + OAuth2 PKCE callback | 6 |
| `backends/` | Execution targets: local, Docker, SSH, WSL2 | Core |
| `caching/` | `BaseTTLCache` + `PersistentCacheMixin` | 11 |
| `channels/` | Telegram, WhatsApp, Discord adapters | 6 |
| `config.py` | Pydantic config with hot-reload (watchdog) | Core |
| `cost_tracker.py` | SQLite cost accumulator + dashboard queries | 3 |
| `defaults.py` | Single source of truth for all tunable constants | 10 |
| `evals/` | Dataset-driven eval harness (5 metrics) | 7 |
| `knowledge/` | FTS5 notes, path reasoning, batched parallel reads | 1, 7, 8 |
| `logging_config.py` | Structured logger + PII scrubber | 2 |
| `marketplace/` | Multi-source plugin discovery + HMAC-signed manifests | 9 |
| `memory.py` | Per-tenant SQLite memory (UserContext-aware) | Core |
| `messaging/` | Inter-agent `AgentMessage` + `InProcessBroker` | 7 |
| `observability/` | OpenTelemetry tracing (no-op when SDK absent) | 2, 10 |
| `prompts/` | Versioned Jinja2 prompt registry (JSONL-backed) | 3 |
| `provider.py` | LLM provider abstraction + `Message` TypedDict | Core |
| `resilience/` | Circuit breaker + fallback chain | 2, 10 |
| `scheduler_features.py` | Complexity-driven decomposition, retry, checkpoints | 6.3 |
| `structured_output/` | `extract_json` / `validate` / `repair_json` | 3 |
| `swarm/` | Multi-agent orchestrator (Parallel/Sequential/Hierarchical/Voting) | Core |
| `tenancy/` | `UserContext` + scoping helpers | 6, 11 |
| `tools/` | Tool system: shell, files, browser (Playwright), KB, swarm, scheduler | Core |
| `vector/` | Pluggable backends: memory / sqlite / qdrant | 4 |
| `webui/` | FastAPI + React dashboard (TypewriterText, CostDashboard) | 3, 11 |

---

## 3. Core Systems Deep Dive

### 3.1 Agent Engine (`agent.py` ~71KB)

The monolithic heart of the system. Responsibilities:
- **Message routing:** Chooses model based on task complexity and agent profile
- **Context building:** Assembles `system_prompt + memory_history + knowledge_context`
- **Provider chat:** Delegates to `provider.py` with semantic cache pre-check
- **Tool execution:** Dispatches tool calls in parallel where safe, sequential where dependent
- **Streaming:** `think_stream()` yields tokens via async iterator
- **Structured output:** `complete_structured(messages, MyPydanticModel)` with auto-repair
- **Task timer:** 300s timeout with progressive status updates at 60s intervals

**Observation:** At 71KB, `agent.py` is large. The `agent_internals/` directory was created in Sprints 5+9 to decompose it, but the main class still carries significant surface area.

### 3.2 Provider Layer (`provider.py` ~53KB)

Abstract base `LLMProvider` with concrete implementations for:
- **Local:** Ollama, LM Studio, llama.cpp
- **Cloud:** OpenAI, Anthropic, Google Gemini, Groq, OpenRouter

Key features:
- `Message` TypedDict for type-safe chat envelopes
- Per-provider HTTP connection pooling
- Token counting (where provider SDK supports it)
- Semantic cache integration (embedding-based dedup)

### 3.3 Memory System (`memory.py` ~26KB)

- **Per-tenant isolation:** `memory_{user_id}.db` files
- **FTS5 full-text search** with BM25 ranking
- **Composite index:** `(role, timestamp)` for fast role-filtered queries
- **WAL mode** for concurrent read/write
- **Auto-cleanup:** Configurable retention (default 90 days)
- **Knowledge extraction:** Automatic entity/relation extraction from messages

### 3.4 Knowledge Base (`knowledge/`)

- `db.py`: FTS5 notes + dedicated thread-pool executor for queries
- `storage.py`: Batched parallel note reads (7-10x faster searches)
- `path_reasoning.py`: `find_paths(a, b, max_hops=3)` for graph traversal queries

### 3.5 Tool Ecosystem (`tools/`)

| Tool | Description | Security |
|---|---|---|
| `read_file` / `write_file` / `download_file` | Filesystem ops | Path validation |
| `shell` | Sandboxed command execution | Allow-list + newline-injection blocked |
| `hardware` | CPU/GPU/NPU telemetry | Read-only |
| `search_knowledge` / `write_to_knowledge` | KB management | -- |
| `delegate` / `swarm_create` / `swarm_assign` | Multi-agent | -- |
| `schedule` / `list_schedules` / `cancel_schedule` | Task scheduling | -- |
| `nlp_schedule` | NL scheduling ("in 5 minutes") | -- |
| `auto_schedule` / `estimate_complexity` | Complexity scoring + auto-decomposition | -- |
| `browser_navigate` / `browser_screenshot` / ... | Playwright headless browser | Sandboxed |
| `register_tool` | **Dynamic tool creation at runtime** | AST-validated sandbox |

**Dynamic tool sandbox (`toolbox.py` ~41KB):**
- AST parsing forbids: `os`, `sys`, `subprocess`, `pathlib`, `ctypes`, `cffi`, `mmap`, `importlib`, `__builtins__`
- Tools are registered at runtime and persisted

### 3.6 Resilience (`resilience/`)

- **CircuitBreaker:** Per-provider failure threshold (default 5 failures) with 60s reset timeout
- **FallbackChain:** Cascades through provider list on failure
- Both are wired into `Agent.think()` and `provider.chat()`

### 3.7 Auth & Multi-Tenancy (`auth/`, `tenancy/`)

- **JWT:** HS256 (secret) or RS256/ES256 (JWKS endpoint) with issuer/audience/scope validation
- **OAuth2:** Authorization-code flow with PKCE; pre-baked for GitHub & Google
- **UserContext:** Propagated via `contextvars.ContextVar` for per-request tenant isolation
- **Memory** transparently picks up active user from `UserContext`

### 3.8 Observability (`observability/`, `logging_config.py`)

- **OpenTelemetry:** `@traced` decorator and `span()` context manager; no-op when SDK absent
- **PII Scrubber:** Redacts emails, phones, JWTs, API keys; hashes user IDs. Default ON.
- **CostTracker:** Per-call provider accounting with SQLite backend + React dashboard

### 3.9 Plugin Marketplace (`marketplace/`)

- **Sources:** OpenClaw, GitHub Releases, HTTP registries, Local hub
- **Security:** HMAC-signed manifests; rejects unsigned by default
- **Client:** `MarketplaceClient` aggregates all sources

### 3.10 Vector Store (`vector/`)

Pluggable backends via factory:
- `memory`: In-memory (tests)
- `sqlite`: Zero-dependency
- `qdrant`: Production HNSW (requires `qdrant-client`)

### 3.11 Web UI (`webui/`)

- **FastAPI** backend serving REST + WebSocket
- **React + Vite** frontend
- **Components:** `CostDashboard.tsx` (Sprint 3), `TypewriterText.tsx` (Sprint 11 token-level streaming)

---

## 4. Security Analysis

### 4.1 Strengths

| Control | Implementation | Status |
|---|---|---|
| Command sandboxing | Allow-list + per-token re-validation + newline blocked | Fixed 2026-04-29 |
| Dynamic tool sandbox | AST validation with forbidden module list | Active |
| PII scrubber | Regex-based redaction + user ID hashing | Default ON |
| JWT verification | HS256/RS256/ES256 with claim validation | Active |
| OAuth2 PKCE | Standard RFC 7636 flow | Active |
| HMAC-signed plugins | Marketplace manifest verification | Active |
| MFA | TOTP + QR code for admin endpoints | Optional |
| CORS | Explicit allow-list | Active |
| Admin gating | Permission checks on sensitive endpoints | Active |
| Audit logs | HMAC-signed log records | Active |
| SSRF protection | URL validation on fetch tools | Active |

### 4.2 Recent Security Fixes (2026-04-29)

1. **Infinite recursion** in `Agent._track_preload` (critical)
2. **Shell injection** via newline characters bypassing regex (critical)
3. **AST sandbox bypass** via `__builtins__` manipulation (critical)
4. **Path traversal** in file tools via `../` sequences (high)
5. **SSRF** in web fetch tools via DNS rebinding (high)
6. **Timing attack** in API key comparison (medium)
7. **Information disclosure** in error messages leaking stack traces (medium)

All fixes have corresponding regression tests.

### 4.3 Risks

- **Large attack surface:** Dynamic code execution (`register_tool`) and shell execution are inherently high-risk. The AST sandbox is good but not formally verified.
- **PII scrubber is regex-based:** May miss novel formats or obfuscated data.
- **No row-level security in SQLite:** Multi-tenancy relies on file isolation (`memory_{user_id}.db`), which is effective but lacks database-enforced RLS.
- **Config file contains secrets:** `config.json` stores API keys; encryption module exists but is optional.

---

## 5. Code Quality & Testing

### 5.1 Test Coverage

- **456 tests** across 26 test files
- Tests cover: agent internals, auth, caching, cost tracker, Discord channel, evals, marketplace, memory, messaging, observability, prompts, registry, resilience, scheduler features, structured output, tenancy, vector backends, sprint integrations

### 5.2 Code Organization

**Strengths:**
- Clear module boundaries with single responsibilities
- `defaults.py` consolidates all tunable constants
- Optional dependencies degrade gracefully (no crash, logged warning)
- Type hints throughout (TypedDicts for message envelopes)
- Sprint-based development with changelog tracking

**Concerns:**
- `agent.py` at 71KB is still overweight despite decomposition efforts
- `toolbox.py` at 41KB for dynamic tool sandboxing is complex
- Some modules (`provider.py` at 53KB) are large monoliths
- Mixed async/sync patterns (SQLite uses `aiosqlite` but KB uses thread pool)

### 5.3 Documentation

- `README.md`: Comprehensive with quick recipes
- `docs/ARCHITECTURE.md`: Module map + request lifecycle
- `docs/SPRINTS_SUMMARY.md`: Condensed per-sprint narrative
- `docs/SECURITY_FIXES_2026_04_29.md`: Audit trail with root causes
- `CHANGELOG.md`: Full release history
- `AGENTS.md`: Agent-facing conventions

---

## 6. Deployment & Operations

### 6.1 Docker

- `docker-compose.yml` with profiles: `default`, `full` (Redis + Ollama), `dev`
- Persistent volumes for data, logs, Redis, Ollama models
- Health checks built in

### 6.2 CLI Commands

| Command | Purpose |
|---|---|
| `zensynora agent` | Interactive CLI chat |
| `zensynora gateway` | Start Telegram/WhatsApp/Discord bots |
| `zensynora webui` | Launch browser dashboard |
| `zensynora benchmark` | Test model latency/accuracy |
| `zensynora onboard` | Setup wizard |

### 6.3 Configuration

- Primary: `~/.myclaw/config.json` (Pydantic-validated, hot-reload)
- Overrides: `MYCLAW_*` environment variables
- 50+ configurable variables in `.env.example`

---

## 7. Strengths

1. **Production-grade resilience:** Circuit breakers, fallback chains, retry policies, and task timers are first-class citizens, not afterthoughts.
2. **Privacy-first design:** Local-first SQLite, per-tenant isolation, PII scrubbing, and optional cloud providers.
3. **LLM agnostic:** 6+ providers with intelligent routing and semantic caching.
4. **Dynamic tool ecosystem:** Runtime tool creation via AST-validated sandbox is a genuine differentiator.
5. **Structured output with repair:** `complete_structured()` auto-fixes invalid JSON via LLM-driven repair loops.
6. **Comprehensive observability:** OpenTelemetry spans, cost tracking, and PII-aware logging.
7. **Plugin marketplace:** Multi-source discovery with cryptographic manifest verification.
8. **Eval harness:** Dataset-driven testing with 5 built-in metrics and latency percentiles.
9. **Agent swarms:** 137 specialized agents across 10 categories with 4 orchestration strategies.
10. **Security-conscious development:** Dedicated security audit document with regression tests for every finding.

---

## 8. Weaknesses & Areas for Improvement

### 8.1 Architecture

- **Monolithic `agent.py`:** Despite `agent_internals/`, the main orchestrator is still 71KB. Consider extracting `Agent.think()` into a state machine or pipeline framework.
- **Tight coupling between gateway and agent:** Direct method calls (`Agent.think()`) rather than message bus for intra-process communication.

### 8.2 Scalability

- **SQLite bottleneck:** Per-tenant SQLite is fine for personal use but will struggle under high concurrency. The WAL mode helps, but there's no pooling or read-replica strategy.
- **In-process caching:** `BaseTTLCache` is local only; no distributed cache option.
- **No horizontal scaling story:** The architecture assumes a single Python process.

### 8.3 Security

- **Dynamic execution risk:** `register_tool` sandbox is good but not bulletproof. Consider WebAssembly (Wasmtime) or seccomp-bpf for stronger isolation.
- **Secret management:** API keys in JSON config file; consider integration with OS keyrings or external secret managers.
- **No CSP headers:** Web UI may be vulnerable to XSS if user-generated content is rendered.

### 8.4 Maintainability

- **Large files:** `agent.py` (71KB), `provider.py` (53KB), `toolbox.py` (41KB) exceed comfortable single-file complexity thresholds.
- **Mixed async patterns:** Some modules use `asyncio.to_thread`, others use `aiosqlite`, others use dedicated thread pools. A unified concurrency strategy would help.
- **Test duplication:** Some sprint integration tests may overlap with unit tests.

### 8.5 Missing Features

- **Slack channel adapter:** Mentioned in optional deps but not yet implemented.
- **Enterprise RBAC:** Basic scopes exist but no fine-grained resource-level permissions.
- **LoRA adapter loading:** Mentioned in roadmap but not present.
- **Agent internals DI:** Roadmap mentions explicit dependency injection.

---

## 9. Recommendations

### Short-term (next 2-4 weeks)

1. **Add CSP headers** to the FastAPI webui to mitigate XSS
2. **Implement Slack channel adapter** (dependency already declared)
3. **Add pre-commit hooks** for security linting (`bandit`, `safety`)
4. **Increase test coverage** for `agent.py` core logic (currently tested via integration tests mostly)

### Medium-term (next 2-3 months)

1. **Decompose `agent.py`** further: extract `ThinkPipeline`, `ContextAssembler`, and `ToolDispatcher` into separate classes
2. **Add PostgreSQL backend** for memory/knowledge as an alternative to SQLite for multi-user deployments
3. **Implement WebAssembly sandbox** for `register_tool` as an optional high-security mode
4. **Add load testing** with `locust` or `k6` to identify SQLite concurrency bottlenecks
5. **Add CSP and security headers** middleware to `api_server.py`

### Long-term (6+ months)

1. **Horizontal scaling:** Message bus (Redis/RabbitMQ) for multi-process agent swarms
2. **Enterprise RBAC:** Resource-level permissions with audit logging
3. **Model serving:** Integrate vLLM/TGI for self-hosted model inference
4. **Formal verification:** Consider TLA+ or similar for the circuit breaker and fallback chain state machines

---

## 10. Technology Stack Summary

| Layer | Technology |
|---|---|
| Language | Python 3.11+ |
| Web Framework | FastAPI |
| Frontend | React 18 + Vite + TypeScript |
| Database | SQLite (FTS5), optional Qdrant |
| LLM Providers | Ollama, OpenAI, Anthropic, Gemini, Groq, OpenRouter |
| Async | asyncio, httpx, aiosqlite |
| Messaging | python-telegram-bot, discord.py |
| Testing | pytest |
| Observability | OpenTelemetry (optional) |
| Deployment | Docker + Docker Compose |
| Build | hatchling |

---

*Analysis generated 2026-05-06. Based on commit `fad40f5` on `main` branch.*
