# ZenSynora Architecture

> **Status:** reflects **v0.7.5** (2026-10-14). Updated from the v0.4.1
> version: adds the Web UI settings API, provider routing changes, the safe
> updater and the doctor/medic rewrite (0.7.0); chat channels, agent-to-agent
> events, jobs, backups, model rotation, custom providers and approval-gated
> self-update (0.7.1); test inventory recounted. **Unreleased:** Telegram bot
> fixed and able to run inside the Web UI (`web/telegram_api.py`).
> **Audience:** contributors and operators. For end-user docs see
> [`README.md`](../README.md). Sprint history:
> [`archive/SPRINTS_SUMMARY.md`](archive/SPRINTS_SUMMARY.md).

This document is a high-level map of the codebase and how the modules
fit together. It complements [`docs/architecture_diagram.md`](architecture_diagram.md)
(visual flow) by explaining *why* each layer exists.

---

## 1. Top-level layout

```
myclaw/
├── agent.py                 # Agent — orchestrator (think, complete_structured, stream_think)
├── agent_skills.py          # Skills: SKILL.md files, per-agent lists, use_skill tool [0.7.5]
├── agent_team.py            # Team prompt section, create_agent tool, delegation depth [0.7.5]
├── skills_builtin/          # Built-in skills, one folder with SKILL.md each [0.7.5]
├── agent_internals/         # Phase helpers — Sprint 5 (free fns) + Sprint 9 (classes + ResponseHandler)
├── agents/
│   ├── data/agents.yaml     # 137 agent defs — canonical source [Sprint 12]
│   └── registry.py          # YAML loader + literal fallback
├── aiohttp_session.py      # Shared aiohttp.ClientSession singleton (connection pooling) [2026-05-17]
├── api_server.py            # FastAPI app: REST + WebSocket + cost + auth endpoints
├── auth/                    # JWT verification + OAuth2 PKCE callback [Sprint 6]
├── backends/                # local / docker / ssh / wsl2 execution
├── backup.py                # Backups of settings, API keys, knowledge, chat, jobs [0.7.1]
├── caching/                 # Shared BaseTTLCache + PersistentCacheMixin [Sprint 11]
├── channels/                # telegram / whatsapp / discord / web adapters [Sprint 6]
│   └── telegram.py          # Telegram bot: allowFrom, per-chat queue + workers, @agent
│                            #   routing, commands + / menu, reply splitting [Unreleased]
├── chat/                    # Web UI chat channels: store (chat.db) + multi-agent engine [0.7.1]
├── config.py                # Pydantic config + hot-reload (watchdog)
├── context_budget.py        # Per-agent context: estimate, model maximum, trimming, num_ctx [Unreleased]
├── gateway.py               # `zensynora gateway`: Telegram / WhatsApp without the Web UI
├── cost_tracker.py          # SQLite cost accumulator + dashboard queries
├── defaults.py              # Single source of truth for tunable constants [Sprint 10]
├── doctor/                  # Health checks, integrity baseline, safe fixes [0.7.0]
│                            #   + checks_more.py (restarts, custom tools, knowledge index, skills, MCP, chat),
│                            #   logscan.py (log grouping, redaction), report.py (diagnostics report) [Unreleased]
├── evals/                   # Dataset-driven eval harness [Sprint 7]
├── events.py                # Agent-to-agent activity events (delegation, tools, jobs) [0.7.1]
├── jobs/                    # Jobs: store (jobs.db) + runner (schedule, evaluate, benchmark) [0.7.1]
│                            #   + medic.py: diagnose / fix failed runs [0.7.5]
├── llm_errors.py            # Readable model-server errors + rotation cooldowns [0.7.3]
├── knowledge/               # scope.py: one shared knowledge base for internal ids [0.7.5]
│   ├── db.py                # FTS5 notes + composite (role, timestamp) index
│   ├── path_reasoning.py    # find_paths / shortest_path [Sprint 7]
│   ├── storage.py           # Batched parallel note reads [Sprint 1 + 8]; write_note keeps text + tags + facts + links [Unreleased]
│   ├── extract.py           # Model output → notes with tags, facts, links; ✨ enrich [Unreleased]
│   └── ...
├── logging_config.py        # Structured logger + PII scrubber [Sprint 2]
├── marketplace/             # Multi-source plugin discovery + HMAC manifests [Sprint 9]
├── memory.py                # Per-tenant SQLite memory (UserContext-aware)
├── messaging/               # AgentMessage envelope + InProcessBroker [Sprint 7]
├── observability/           # OpenTelemetry tracing — wired Sprint 10
├── prompts/                 # Versioned Jinja2 prompt registry [Sprint 3]
├── provider.py              # LLM providers (+ your own, providers.custom) + Message TypedDict
├── provider_keys.py         # Several API keys per provider: lookup, key fallback order, who uses which key [0.7.5]
├── resilience/              # Circuit breaker + fallback chain — wired Sprint 10
├── selfupdate.py            # Agents' code-change proposals → approval → safe updater [0.7.1]
├── structured_output/       # extract_json / validate / repair_json [Sprint 3]
├── swarm/                   # Multi-agent orchestrator + strategies
├── tenancy/                 # UserContext + scoping helpers [Sprint 6 + 11]
├── tools/
│   ├── browser.py           # Playwright headless browser [Sprint 3]
│   ├── jobs.py              # job_create / job_list / job_run / job_status / job_update [Unreleased]
│   ├── shell.py             # Allow-list, no shell; chains with | && || ; of allowed commands [Unreleased]
│   └── ...
├── vector/                  # Pluggable backends: memory / sqlite / qdrant [Sprint 4]
├── web/
│   ├── api.py               # Web UI backend: wiring, agents, legacy chat socket, static SPA
│   ├── settings_api.py      # Settings, health, doctor, costs, history [0.5.0]
│   ├── chat_api.py          # Channels REST + /ws/channels/{id} [0.7.1]
│   ├── models_api.py        # Default model, rotation, prompt generator [0.7.1]
│   ├── provider_keys_api.py # Add / rename / delete / test a provider's API keys [0.7.5]
│   ├── jobs_api.py          # Jobs CRUD, run, evaluate, benchmark [0.7.1]
│   ├── backup_api.py        # Backups list/create/restore/download/import [0.7.1]
│   ├── medic_api.py         # Change proposals, problems, lessons, self-review [0.7.1]
│   ├── doctor_api.py        # Doctor report, errors viewer, report download [Unreleased]
│   ├── update_api.py        # Check / update / roll back / restart from the Web UI [0.7.4]
│   ├── telegram_api.py      # Telegram status/test/restart for the Web UI [Unreleased]
│   ├── knowledge_api.py     # Knowledge page: graph, notes, edit, trash, search [Unreleased]
│   └── skills_api.py        # Skills page: list, edit, import, assign [0.7.5]
└── ... (~50 utility modules)

updater/                     # Safe updates: side-by-side releases, backups,
                             # migrations, rollback (stdlib only) [0.6.0];
                             # app_backup.py: full program-folder archive [0.7.1];
                             # nodejs.py: finds Node.js 20.19+ or installs Node 22
                             # into BASE/node for the web UI build [Unreleased]

webui/src/
├── App.tsx                  # Shell: channels + agents sidebar, navigation, live health
├── api.ts                   # Backend helpers and shared types
├── colors.ts                # Stable colour per agent [0.7.1]
├── updates.ts               # Update status type, restart-and-wait helper [0.7.4]
├── latency.ts               # Latency levels of the model test (green/amber/red) [Unreleased]
├── contextLength.ts         # Context slider helpers: formatting, snapping, levels [Unreleased]
└── components/
    ├── ChatView.tsx         # Channel chat, @mentions, activity panel [0.7.1]
    ├── ModelsView.tsx       # Default model, rotation, your providers [0.7.1]
    ├── JobsView.tsx         # Jobs, runs, evaluation, benchmark [0.7.1]
    ├── BackupsView.tsx      # Backups [0.7.1]
    ├── ProposalsPanel.tsx   # Change proposals + lessons (Doctor page) [0.7.1]
    ├── ChannelsView.tsx     # Create / rename / manage channels [0.7.3]
    ├── UpdatesView.tsx      # Check for updates, update, roll back, restart [0.7.4]
    ├── SkillsView.tsx       # Skills page [0.7.5]
    ├── KnowledgeView.tsx    # Knowledge page: graph, notes, tags, note panel [Unreleased]
    ├── KnowledgeGraph.tsx   # Canvas force-directed graph (no extra libraries) [Unreleased]
    ├── ModelPicker.tsx      # Model list per provider + key, ⚡ latency test [Unreleased]
    ├── ContextLength.tsx    # Per-agent context slider with min / recommended / max marks [Unreleased]
    ├── SettingsView.tsx     # Settings page [0.5.0]
    ├── DoctorView.tsx       # Doctor page [0.7.0]
    ├── DoctorReport.tsx     # Doctor → Report: errors viewer, report view, download [Unreleased]
    ├── ToolsView.tsx        # Tool list [0.5.0]
    ├── CostDashboard.tsx    # Cost dashboard [Sprint 3]
    └── TypewriterText.tsx   # Token-level streaming UI [Sprint 11]

tests/                       # 730 tests (pytest --collect-only, 2026-10-18)
├── fixtures/fake_telegram_api.py # Local fake Telegram Bot API server [Unreleased]
├── test_chat_message_actions.py # Copy/edit/delete/regenerate chat messages [Unreleased]
├── test_doctor_report.py    # Doctor checks, report, errors viewer, downloads [Unreleased]
├── test_provider_keys.py    # Several API keys per provider [Unreleased]
├── test_telegram_channel.py # Telegram bot, incl. end-to-end via the fake server [Unreleased]
└── test_telegram_webui.py   # Telegram bot inside the Web UI [Unreleased]
```

---

## 2. Request lifecycle

The "happy path" for a single user message:

```
channel (web chat / telegram / whatsapp / cli)
  telegram only: allowFrom check → per-chat queue → worker → @agent routing
  → Agent.think()
      → _route_message()      task timer, guardrails, model selection,
                              medic loop guard (working since 0.7.0)
      → _build_context()      memory + knowledge retrieval
      → _provider_chat()      LLM call (provider.py)
                              ─ semantic_cache lookup
                              ─ retry/backoff, circuit breaker
                              ─ offline fallback to another local provider
                                (never the one that just failed) [0.5.0]
                              ─ retry without tools if the server rejects them
                              ─ metrics (logged on failure since Sprint 1)
      → _execute_tools()      parallel + sequential tool dispatch
      → _handle_summarization() background memory cleanup
  ← response string
channel sends reply (Telegram: split into ≤ 4096-character messages)
```

The Telegram bot runs either inside `zensynora webui` (started by
`web/telegram_api.py` when `channels.telegram.run_in_webui` is on) or in
`zensynora gateway` (`gateway.py`) — never both, because Telegram allows only
one copy of a bot to receive updates.

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
* Optional dep: `opentelemetry-sdk` (`python -m pip install 'zensynora[tracing]'`).

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
* `myclaw/logging.py` is deprecated as of 2026-05-18 — delegates to this module.

### `myclaw/http_session.py` + `myclaw/aiohttp_session.py` (2026-05-17)
* `http_session.py` — shared `requests.Session` singleton for sync HTTP with
  connection pooling and TCP keep-alive.
* `aiohttp_session.py` — shared `aiohttp.ClientSession` singleton for async
  HTTP with connection pooling. Lazily created, auto-closed at exit.
* Both modules reduce latency for frequent outbound calls (web search,
  MCP HTTP transport, knowledge fetching).

### `myclaw/tools/browser.py`
* Playwright-backed: `browser_navigate`, `browser_screenshot`,
  `browser_fill_form`, `browser_extract_text`, `browser_wait_for`,
  `browser_close_session`.
* Persistent contexts keyed by `session_id`; module-level pool capped at 10.
* Optional dep: `playwright` (`python -m pip install 'zensynora[browser]'`).
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
  (`python -m pip install 'zensynora[qdrant]'`). Run locally with
  `docker run -p 6333:6333 qdrant/qdrant`.
* `make_backend(name, config)` — config-driven factory; falls back to
  SQLite if Qdrant requested but client missing.

### `webui/src/components/CostDashboard.tsx`
* Self-contained React component; no external chart library.
* Fetches `/api/v1/costs/{summary,by-provider,by-model,timeline}` (all
  added in Sprint 3) and refreshes every 60 s.

---

## 4. Optional dependency posture

Install optional packages into the activated project virtual environment;
see the [README installation guide](../README.md#quick-start). Do not run
these commands against Debian/Ubuntu system Python (PEP 668).

Every advanced feature with an external dep degrades gracefully:

Since 0.5.0 the `openai` SDK is a **core** dependency: llama.cpp, LM Studio,
OpenAI, Groq and OpenRouter all need it, and a missing SDK used to make chat
silently fall back to Ollama.

| Feature | Optional package | Without it... |
|---|---|---|
| Anthropic / Gemini providers | `anthropic` / `google-generativeai` | Provider raises a clear `ProviderUnavailableError`; the doctor and Settings → Test connection report it |
| Tracing | `opentelemetry-sdk` | All tracing helpers no-op |
| Browser tools | `playwright` | Tools return structured errors |
| Jinja2 templates | `jinja2` | Falls back to `string.Template` |
| JSON-schema dicts | `jsonschema` | Pass Pydantic models instead |
| Qdrant | `qdrant-client` | Factory falls back to SQLite |
| JWT verification | `PyJWT[crypto]` | First `.verify()` raises RuntimeError |
| OAuth token exchange | `httpx` (core dep, present) | n/a |
| Discord channel | `discord.py` | `DiscordChannel.run()` raises RuntimeError |

Install all extras: `python -m pip install 'zensynora[all]'`.

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

Counted with `pytest --collect-only` on 2026-10-13 (v0.7.2).

| Suite | Tests |
|---|---|
| `test_agent.py` | 26 |
| `test_agent_classes.py` | 20 |
| `test_agent_internals.py` | 10 |
| `test_auth.py` | 18 |
| `test_caching_base.py` | 20 |
| `test_cost_tracker.py` | 6 |
| `test_discord_channel.py` | 9 |
| `test_doctor_medic.py` | 10 |
| `test_evals.py` | 17 |
| `test_knowledge.py` | 19 |
| `test_marketplace.py` | 26 |
| `test_memory.py` | 6 |
| `test_messaging.py` | 13 |
| `test_observability.py` | 12 |
| `test_prompts.py` | 12 |
| `test_provider_selection.py` | 4 |
| `test_registry.py` | 64 |
| `test_registry_yaml.py` | 11 |
| `test_resilience.py` | 8 |
| `test_scheduler_features.py` | 30 |
| `test_security.py` | 8 |
| `test_selfupdate.py` | 8 |
| `test_sprint10_integrations.py` | 16 |
| `test_sprint8_perf_quality.py` | 6 |
| `test_structured_output.py` | 20 |
| `test_tenancy.py` | 11 |
| `test_tenancy_scoping.py` | 15 |
| `test_tools.py` | 35 |
| `test_updater.py` | 9 |
| `test_v071_features.py` | 17 |
| `test_v073_reliability.py` | 11 |
| `test_v074_update_and_prompt.py` | 8 |
| `test_chat_message_actions.py` | 8 |
| `test_doctor_report.py` | 21 |
| `test_knowledge_api.py` | 5 |
| `test_log_fixes_20261018.py` | 12 |
| `test_context_budget.py` | 8 |
| `test_tool_step_limit.py` | 5 |
| `test_agent_jobs.py` | 9 |
| `test_updater_node.py` | 13 |
| `test_report_fixes_20261023.py` | 26 |
| `test_knowledge_structure.py` | 9 |
| `test_provider_keys.py` | 18 |
| `test_telegram_channel.py` | 19 |
| `test_telegram_webui.py` | 19 |
| `test_v075_channels_medic_skills.py` | 25 |
| `test_vector_backends.py` | 24 |
| `test_web_settings.py` | 4 |
| `test_kb_phase0_index.py` | 36 |
| `test_kb_phase0_notes.py` | 41 |
| `test_kb_phase0_agent.py` | 19 |
| `test_kb_phase0_surfaces.py` | 16 |
| `test_kb_phase0_migration.py` | 2 |
| `test_kb_phase2_quality.py` | 98 |
| `test_kb_phase3_semantic.py` | 54 |
| `test_kb_phase4_distill.py` | 42 |
| `test_kb_phase5_ingest.py` | 51 |
| `test_kb_phase5_wiring.py` | 1 |
| `test_kb_phase6_robust.py` | 39 |
| **Total** | **1129** |

Run everything (the coverage gate in `pyproject.toml` is skipped with `-o addopts=`):

```bash
pytest -q -o addopts=""
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
  (access check, per-chat queue and workers, dispatch into `Agent.think`).
* **Add a health check** → one `@check` function in `myclaw/doctor/checks.py`
  ([guide](medic_agent_guide.md#adding-a-check)).
* **Change the data format** → add a migration in `updater/migrations/`
  ([guide](UPDATING.md#add-a-data-migration)).
* **Change how updates work** → add a `Step` in `updater/steps.py`.
* **Add a Web UI page** → a component in `webui/src/components/`, a
  `NavButton` in `App.tsx`, a router in `myclaw/web/` included in `api.py`.
* **Emit agent activity** → `myclaw.events.emit({"type": …})` from code that
  runs for an agent; it reaches the channel automatically.
* **Add a provider preset** → `PROVIDER_PRESETS` in `myclaw/web/settings_api.py`.
