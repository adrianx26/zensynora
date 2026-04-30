# ZenSynora Architecture

> **Status:** reflects v0.4.1 + Sprints 1–12 (2026-04-29 → 2026-04-30)
> **Audience:** contributors and operators. For end-user docs see
> [`README.md`](../README.md). For per-sprint narrative see
> [`docs/SPRINTS_SUMMARY.md`](SPRINTS_SUMMARY.md).

This document is a high-level map of the codebase and how the modules
fit together. It complements [`docs/architecture_diagram.md`](architecture_diagram.md)
(visual flow) by explaining *why* each layer exists.

---

## 1. Top-level layout

```
myclaw/
├── agent.py                 # Agent — orchestrator (think, complete_structured, stream_think)
├── agent_internals/         # Phase helpers — Sprint 5 (free fns) + Sprint 9 (classes + ResponseHandler)
├── agents/
│   ├── data/agents.yaml     # 137 agent defs — canonical source [Sprint 12]
│   └── registry.py          # YAML loader + literal fallback
├── api_server.py            # FastAPI app: REST + WebSocket + cost + auth endpoints
├── auth/                    # JWT verification + OAuth2 PKCE callback [Sprint 6]
├── backends/                # local / docker / ssh / wsl2 execution
├── caching/                 # Shared BaseTTLCache + PersistentCacheMixin [Sprint 11]
├── channels/                # telegram / whatsapp / discord / web adapters [Sprint 6]
├── config.py                # Pydantic config + hot-reload (watchdog)
├── cost_tracker.py          # SQLite cost accumulator + dashboard queries
├── defaults.py              # Single source of truth for tunable constants [Sprint 10]
├── evals/                   # Dataset-driven eval harness [Sprint 7]
├── knowledge/
│   ├── db.py                # FTS5 notes + composite (role, timestamp) index
│   ├── path_reasoning.py    # find_paths / shortest_path [Sprint 7]
│   ├── storage.py           # Batched parallel note reads [Sprint 1 + 8]
│   └── ...
├── logging_config.py        # Structured logger + PII scrubber [Sprint 2]
├── marketplace/             # Multi-source plugin discovery + HMAC manifests [Sprint 9]
├── memory.py                # Per-tenant SQLite memory (UserContext-aware)
├── messaging/               # AgentMessage envelope + InProcessBroker [Sprint 7]
├── observability/           # OpenTelemetry tracing — wired Sprint 10
├── prompts/                 # Versioned Jinja2 prompt registry [Sprint 3]
├── provider.py              # LLM providers + Message TypedDict
├── resilience/              # Circuit breaker + fallback chain — wired Sprint 10
├── structured_output/       # extract_json / validate / repair_json [Sprint 3]
├── swarm/                   # Multi-agent orchestrator + strategies
├── tenancy/                 # UserContext + scoping helpers [Sprint 6 + 11]
├── tools/
│   ├── browser.py           # Playwright headless browser [Sprint 3]
│   ├── shell.py             # Sandboxed allow-list (newline injection blocked)
│   └── ...
├── vector/                  # Pluggable backends: memory / sqlite / qdrant [Sprint 4]
└── ... (~50 utility modules)

webui/src/components/
├── CostDashboard.tsx        # Cost dashboard [Sprint 3]
└── TypewriterText.tsx       # Token-level streaming UI [Sprint 11]

tests/                       # 415 tests across all sprint modules
```

---

## 2. Request lifecycle

The "happy path" for a single user message:

```
gateway (telegram/whatsapp/web/cli)
  → Agent.think()
      → _route_message()      task timer, guardrails, model selection
      → _build_context()      memory + knowledge retrieval
      → _provider_chat()      LLM call (provider.py)
                              ─ semantic_cache lookup
                              ─ retry/backoff
                              ─ metrics (logged on failure since Sprint 1)
      → _execute_tools()      parallel + sequential tool dispatch
      → _handle_summarization() background memory cleanup
  ← response string
gateway sends reply
```

### Where Sprint 2-4 modules plug in

* **`observability.span("agent.think", ...)`** — wrap any of the steps
  above to get distributed traces. The decorator/context-manager is a
  no-op when the OTel SDK isn't installed, so wrapping is free.
* **`resilience.FallbackChain`** — wrap `_provider_chat` in a chain of
  providers; failed providers get their own `CircuitBreaker` so the next
  request isn't slowed down by a flapping endpoint. (Wiring is up to the
  operator — the framework now ships the building blocks.)
* **`structured_output.repair_json`** — used by `Agent.complete_structured()`
  to coerce free-form LLM responses into Pydantic-validated payloads,
  with one or more LLM-driven repair rounds on failure.
* **`prompts.PromptRegistry`** — agent profiles can be promoted from
  hard-coded strings to versioned, JSONL-backed templates. Future
  sprints will route `_load_system_prompt` through this.
* **`vector.make_backend("qdrant", ...)`** — knowledge.db's BM25 search
  can be paired with semantic search by populating a `VectorBackend`
  alongside it. The choice of backend is a config string.

---

## 3. New modules — quick reference

### `myclaw/observability/`
* `init_tracing(...)` — idempotent global setup; reads `ZENSYNORA_TRACING_ENABLED`.
* `@traced` / `@traced_async` — decorators; no-op when disabled.
* `with span("name", **attrs):` — context manager; no-op when disabled.
* Optional dep: `opentelemetry-sdk` (`pip install zensynora[tracing]`).

### `myclaw/resilience/`
* `CircuitBreaker(name, failure_threshold, reset_timeout, ...)` — async-safe.
  Three states: CLOSED → OPEN → HALF_OPEN. `excluded_exceptions` lets you
  exempt user-input errors that shouldn't trip the breaker.
* `FallbackChain([(name, async_fn), ...])` — each provider gets its own
  breaker; OPEN providers are skipped without invocation.

### `myclaw/logging_config.py` (Sprint 2 additions)
* `PIIScrubFilter` — redacts emails, phones, API keys, JWTs from log
  messages, args, and `extra_fields`. User IDs hashed to `user:<sha256[:10]>`.
* Default-on; opt out with `MYCLAW_LOG_SCRUB_PII=false`.

### `myclaw/tools/browser.py`
* Playwright-backed: `browser_navigate`, `browser_screenshot`,
  `browser_fill_form`, `browser_extract_text`, `browser_wait_for`,
  `browser_close_session`.
* Persistent contexts keyed by `session_id`; module-level pool capped at 10.
* Optional dep: `playwright` (`pip install zensynora[browser]`).
* Returns `{"ok": False, "error": "..."}` if Playwright isn't installed —
  no crash.

### `myclaw/prompts/`
* `PromptTemplate` — name, version, body, description, tags, variables.
* `PromptRegistry` — append-only JSONL at `~/.myclaw/prompts.jsonl`.
* `register()` auto-increments version; `get(name)` returns latest.
* Jinja2 when available; `string.Template` fallback otherwise.

### `myclaw/structured_output/`
* `extract_json(text)` — pulls first balanced JSON object/array out of
  free-form text; handles code fences, prose wrappers, nested braces,
  quoted strings.
* `validate_json(text, schema)` — `schema` may be a Pydantic v2 model
  (preferred) or a JSON-schema dict.
* `repair_json(text, schema, llm_call, max_attempts=1)` — provider-agnostic.
* `Agent.complete_structured(messages, schema, ...)` wraps it.

### `myclaw/vector/` (Sprint 4)
* `VectorBackend` — abstract async interface: upsert, search, delete,
  count, clear.
* `InMemoryBackend` — zero-dep, brute-force cosine. Tests + tiny corpora.
* `SQLiteBackend` — JSON blob storage + Python cosine. Persistent, no
  new deps. Good up to ~10–50 K vectors.
* `QdrantBackend` — production HNSW. Optional dep
  (`pip install zensynora[qdrant]`). Run locally with
  `docker run -p 6333:6333 qdrant/qdrant`.
* `make_backend(name, config)` — config-driven factory; falls back to
  SQLite if Qdrant requested but client missing.

### `webui/src/components/CostDashboard.tsx`
* Self-contained React component; no external chart library.
* Fetches `/api/v1/costs/{summary,by-provider,by-model,timeline}` (all
  added in Sprint 3) and refreshes every 60 s.

---

## 4. Optional dependency posture

Every advanced feature with an external dep degrades gracefully:

| Feature | Optional package | Without it... |
|---|---|---|
| Tracing | `opentelemetry-sdk` | All tracing helpers no-op |
| Browser tools | `playwright` | Tools return structured errors |
| Jinja2 templates | `jinja2` | Falls back to `string.Template` |
| JSON-schema dicts | `jsonschema` | Pass Pydantic models instead |
| Qdrant | `qdrant-client` | Factory falls back to SQLite |
| JWT verification | `PyJWT[crypto]` | First `.verify()` raises RuntimeError |
| OAuth token exchange | `httpx` (core dep, present) | n/a |
| Discord channel | `discord.py` | `DiscordChannel.run()` raises RuntimeError |

Install all extras: `pip install zensynora[all]`.

---

## 5. Outstanding deferrals

* **`agent_internals` explicit-DI refactor** — Sprint 5 extracted the three big
  phases as free functions; Sprint 9 wrapped them in classes (`MessageRouter`,
  `ContextBuilder`, `ToolExecutor`, `ResponseHandler`) but those classes still
  take an `agent` reference. Next iteration: replace with explicit per-dependency
  constructor args (timer, memory provider, hooks). See
  [`docs/dev/DECOMPOSITION_PLAN.md`](dev/DECOMPOSITION_PLAN.md).
* **Slack channel adapter** — `[slack]` extra reserved; adapter not yet built.
* **LoRA adapter loader + training-data exporter** — net-new feature; tracked.
* **Removing the embedded literal in `myclaw/agents/registry.py`** — once the
  YAML format has been stable across one minor release; the sync invariant
  test catches drift in the meantime.

---

## 6. Test inventory

| Suite | Count | Module under test |
|---|---|---|
| `test_resilience.py` | 16 | circuit breaker + fallback chain |
| `test_observability.py` | 15 | tracing no-op + PII scrubber |
| `test_registry.py` | 14 | agent registry structural invariants |
| `test_prompts.py` | 12 | prompt versioning, persistence, render |
| `test_structured_output.py` | 18 | extract / validate / repair |
| `test_cost_tracker.py` | 6 | cost queries used by dashboard |
| `test_vector_backends.py` | 40 | memory + sqlite + factory |
| `test_agent_internals.py` | 14 | route_message + medic_proxy |
| `test_auth.py` | 21 | JWT + OAuth (PKCE) |
| `test_tenancy.py` | 11 | UserContext + contextvars isolation |
| `test_tenancy_scoping.py` | 15 | effective_user_id + Memory wiring |
| `test_discord_channel.py` | 10 | chunking + dep-missing graceful path |
| `test_evals.py` | 16 | dataset loader + runner + metrics |
| `test_messaging.py` | 14 | envelope + InProcessBroker |
| `test_sprint8_perf_quality.py` | 6 | graph batch + composite index + numpy scan |
| `test_marketplace.py` | 40 | manifest signing + 4 sources + client |
| `test_agent_classes.py` | 31 | MessageRouter + ContextBuilder + ToolExecutor + ResponseHandler |
| `test_sprint10_integrations.py` | 17 | breaker/tracing/KB-executor wiring + __all__ |
| `test_caching_base.py` | 20 | BaseTTLCache + PersistentCacheMixin |
| `test_registry_yaml.py` | 11 | YAML loader + literal sync invariant |
| **Total new** | **347** | |
| (pre-existing) `test_security.py`, `test_tools.py`, … | 68+ | core surface |
| **Grand total** | **415+** | |

**Run all new suites:**
```
pytest tests/test_resilience.py tests/test_observability.py \
       tests/test_registry.py    tests/test_prompts.py \
       tests/test_structured_output.py tests/test_cost_tracker.py \
       tests/test_vector_backends.py
```

---

## 7. Where to look next

* **Add a new tool** → `CONTRIBUTING.md` walkthrough; tools go in
  `myclaw/tools/` and are auto-discovered.
* **Add a new LLM provider** → subclass the appropriate base in
  `provider.py`; update the factory in the same file.
* **Add a new vector backend** → subclass `VectorBackend` in
  `myclaw/vector/`, then teach `factory.make_backend` about its name.
* **Add a new channel** → match the pattern in `channels/telegram.py`
  (gateway-based dispatch into `Agent.think`).
