# ZenSynora Architecture — Visual Guide

> **Version:** 0.7.0 · **Last updated:** 2026-10-13
> Rewritten for 0.7.0. The previous version showed planning phases and
> modules that no longer match the code. Every diagram below reflects the
> current source tree; file names are given so you can jump to the code.
> Text-oriented companion: [ARCHITECTURE.md](ARCHITECTURE.md).

**Contents**

1. [System context](#1-system-context)
2. [Component map](#2-component-map)
3. [A chat message, end to end](#3-a-chat-message-end-to-end)
4. [Provider selection and fallback](#4-provider-selection-and-fallback)
5. [Tool execution](#5-tool-execution)
6. [Swarms](#6-swarms)
7. [Settings from the Web UI](#7-settings-from-the-web-ui)
8. [Where data lives](#8-where-data-lives)
9. [Safe updates](#9-safe-updates)
10. [Doctor and medic](#10-doctor-and-medic)
11. [Install layout on disk](#11-install-layout-on-disk)

---

## 1. System context

Who talks to ZenSynora, and what ZenSynora talks to.

```mermaid
flowchart LR
    user(["You"])
    subgraph entry["Entry points"]
        webui["Web UI<br/>browser · port 8000"]
        cli["Terminal<br/>zensynora agent"]
        tg["Telegram bot"]
        wa["WhatsApp Cloud"]
        mcpc["MCP clients<br/>zensynora mcp-server"]
    end
    core["ZenSynora core<br/>agents · tools · memory · knowledge"]
    subgraph llm["LLM providers"]
        local["Local: llama.cpp · Ollama · LM Studio"]
        cloud["Cloud: OpenAI · Anthropic · Gemini · Groq · OpenRouter"]
    end
    subgraph ext["Outside services (optional)"]
        web["Web pages / search"]
        mcps["External MCP servers"]
        vt["VirusTotal"]
        gh["GitHub (updates, file recovery)"]
    end
    disk[("~/.myclaw<br/>config · databases · knowledge")]

    user --> webui & cli & tg & wa
    mcpc --> core
    webui & cli & tg & wa --> core
    core --> local & cloud
    core --> web & mcps & vt & gh
    core <--> disk
```

---

## 2. Component map

The main packages under `myclaw/`, grouped by layer. Arrows point from a
layer to the layer it uses.

```mermaid
flowchart TB
    subgraph L1["Interfaces"]
        direction LR
        cli2["cli.py<br/>click commands"]
        webapi["web/api.py + web/settings_api.py<br/>Web UI backend · WebSocket chat"]
        gw["gateway.py<br/>Telegram / WhatsApp / console"]
        channels["channels/<br/>telegram · whatsapp · discord"]
        mcpsrv["mcp/server.py<br/>tools over MCP (stdio)"]
        apisrv["api_server.py<br/>REST API library (/api/v1)"]
    end

    subgraph L2["Agents"]
        direction LR
        agent["agent.py<br/>Agent: think · stream_think"]
        internals["agent_internals/<br/>router · context_builder · tool_executor"]
        swarm["swarm/<br/>orchestrator · strategies"]
        registry["agents/registry.py<br/>agents.yaml profiles"]
        special["agents/<br/>medic · newtech · skill_adapter"]
    end

    subgraph L3["Capabilities"]
        direction LR
        tools["tools/<br/>shell · files · web · browser · kb · scheduler · swarm · toolbox"]
        provider["provider.py<br/>8 LLM providers"]
        resilience["resilience/ + offline.py<br/>circuit breaker · offline fallback"]
        prompts["prompts/ · structured_output/"]
        sandbox["sandbox.py · worker_pool.py · backends/"]
    end

    subgraph L4["State"]
        direction LR
        memory["memory.py<br/>conversation history (aiosqlite pool)"]
        knowledge["knowledge/<br/>notes · FTS5 · graph"]
        cache["semantic_cache.py · caching/"]
        vector["vector/<br/>sqlite · qdrant · memory"]
        config["config.py<br/>pydantic · hot reload"]
        stats["cost_tracker.py · metering.py · audit_log.py"]
    end

    subgraph L5["Operations"]
        direction LR
        doctor["doctor/<br/>health checks · integrity"]
        updater["updater/ (repo root)<br/>safe updates · migrations"]
        obs["observability/ · metrics.py · logging_config.py"]
    end

    L1 --> L2 --> L3 --> L4
    L5 -.-> L1 & L2 & L3 & L4
```

---

## 3. A chat message, end to end

What happens when you type in the Web UI (Telegram and the terminal follow
the same path from `Agent` onwards).

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant UI as Web UI (React)
    participant WS as web/api.py<br/>WebSocket /ws/chat/{agent}
    participant A as Agent.stream_think
    participant R as router + medic loop guard
    participant C as context_builder
    participant M as memory.py
    participant K as knowledge/
    participant P as provider.py
    participant T as tools/

    U->>UI: type a message
    UI->>WS: send text
    WS->>WS: quota + optional MFA check
    WS->>A: stream_think(text, user="webui")
    A->>R: start task timer, depth + loop limits
    A->>M: add(user message)
    A->>C: build context
    C->>M: get_history() (flushes buffered turns)
    C->>K: search notes for relevant knowledge
    C-->>A: system prompt + history + knowledge
    A->>P: chat(messages, stream=True)
    P-->>A: token stream (+ tool calls)
    A-->>WS: tokens
    WS-->>UI: __STREAM_START__ … tokens … __STREAM_END__
    opt the model asked for tools
        A->>T: execute (parallel when independent)
        T-->>A: results
        A->>P: chat again with tool results
        P-->>A: final answer stream
        A-->>WS: tokens
    end
    A->>M: add(assistant reply)
    UI-->>U: rendered reply
```

---

## 4. Provider selection and fallback

How a request reaches a model, and what happens when that fails. Changed in
0.5.0: the agent never silently switches to another provider.

```mermaid
flowchart TD
    start(["Agent needs a reply"]) --> pick["Provider = agent's own, else agents.defaults.provider"]
    pick --> init{"Provider can start?<br/>(SDK installed, API key set)"}
    init -- no --> err["ProviderUnavailableError<br/>clear message shown in chat"]
    init -- yes --> breaker{"Circuit breaker<br/>open?"}
    breaker -- closed --> llmcall["provider.chat()"]
    llmcall -- ok --> replied(["Stream reply"])
    llmcall -- "server rejects tool list<br/>(llama.cpp without --jinja)" --> notools["Retry once without tools<br/>remember for next calls"]
    notools --> replied
    llmcall -- "connection error" --> offline{"intelligence.offline_mode?"}
    breaker -- open --> offline
    offline -- no --> fail(["Error shown in chat"])
    offline -- yes --> fb["Try local providers in order<br/>Ollama → LM Studio → llama.cpp<br/>(skipping the one that just failed)"]
    fb -- ok --> replied
    fb -- none reachable --> fail
```

| Provider | Talks to | Python package |
|---|---|---|
| `llamacpp` | `llama-server` (OpenAI API, default `:8080/v1`) | `openai` |
| `ollama` | Ollama native API (`:11434/api/chat`) | — (httpx) |
| `lmstudio` | LM Studio (OpenAI API, `:1234/v1`) | `openai` |
| `openai`, `groq`, `openrouter` | OpenAI-compatible cloud APIs | `openai` |
| `anthropic` | Anthropic Messages API | `anthropic` (optional) |
| `gemini` | Google Gemini | `google-generativeai` (optional) |

---

## 5. Tool execution

```mermaid
flowchart LR
    llm["Model returns tool calls"] --> exec["agent_internals/tool_executor.py"]
    exec --> indep{"Independent<br/>of each other?"}
    indep -- yes --> par["Run in parallel"]
    indep -- no --> seq["Run in order"]
    par & seq --> hooks["pre/post hooks<br/>tools/core.py"]
    hooks --> kind{"Tool type"}
    kind -- shell --> allow["Allow-list + blocked commands<br/>tools/shell.py"]
    kind -- generated / external skill --> sb["Sandbox<br/>sandbox.py · worker_pool.py"]
    kind -- web / browser --> ssrf["URL safety checks<br/>tools/web.py · tools/browser.py"]
    kind -- other --> direct["Run function"]
    allow & sb & ssrf & direct --> audit["audit_log.py · metrics"]
    audit --> back["Results back to the model"]
```

---

## 6. Swarms

A swarm splits one task across several agents (`swarm/orchestrator.py`).

```mermaid
flowchart LR
    task(["Task"]) --> orch["SwarmOrchestrator"]
    orch --> strat{"Strategy"}
    strat --> p["Parallel<br/>all agents at once"]
    strat --> s["Sequential<br/>each builds on the last"]
    strat --> h["Hierarchical<br/>coordinator delegates"]
    strat --> v["Voting<br/>agents vote on answers"]
    p & s & h & v --> agg["Aggregation<br/>synthesis · best · concatenate"]
    agg --> store[("swarm/storage.py<br/>SQLite")]
    agg --> result(["Result"])
```

---

## 7. Settings from the Web UI

Added in 0.5.0 (`web/settings_api.py`).

```mermaid
sequenceDiagram
    actor U as You
    participant S as Settings page
    participant API as /api/settings
    participant G as access guard
    participant F as ~/.myclaw/config.json
    participant R as agent registry

    U->>S: change model / keys / agents
    S->>API: PUT {changes, clear_secrets}
    API->>G: from localhost, or X-API-Key = admin key?
    G-->>API: allowed
    API->>API: validate against AppConfig schema<br/>(empty secret = keep stored value)
    API->>F: save (encrypted if a key exists)
    API->>R: rebuild agents, clear provider cache
    API-->>S: saved (+ which changes need a restart)
    Note over S,API: GET never returns secrets — only which ones are set
```

---

## 8. Where data lives

Everything personal is under `~/.myclaw`, shared by every installed version.

```mermaid
flowchart TB
    subgraph data["~/.myclaw"]
        direction TB
        cfgf["config.json · .config_key<br/>settings + API keys (mode 600)"]
        mem["memory_&lt;user&gt;.db<br/>conversation history"]
        kdb["knowledge/ + knowledge_&lt;user&gt;.db<br/>Markdown notes + FTS5 index"]
        tlb["tools/ · skills/ · plugins/ · hub/<br/>generated tools and installed skills"]
        misc["cost · metering · audit · swarm · state DBs"]
        med["medic/<br/>integrity baseline · file backups · task log"]
        logs["logs/zensynora.log<br/>rotating JSON log (since 0.7.0)"]
        mig[".migrations.json<br/>applied data migrations"]
    end
    app["ZenSynora (any version)"] <--> data
    upd["updater"] -- "backup before every update" --> data
    doc["doctor / medic"] -- "reads + safe fixes" --> data
```

---

## 9. Safe updates

`zensynora update` (`updater/`, added in 0.6.0). Steps 1–9 never touch the
running version; any failure undoes the completed steps in reverse order.

```mermaid
flowchart TD
    s1["1 Preflight<br/>Python, disk space, service state"] --> s2["2 Fetch<br/>GitHub, folder or archive"]
    s2 --> s3["3 Hand-off<br/>newer updater takes over"]
    s3 --> s4["4 Back up ~/.myclaw + .env"]
    s4 --> s5["5 Carry over .env, data/"]
    s5 --> s6["6 Build side by side<br/>venv · packages · extras · Web UI"]
    s6 --> s7["7 Verify<br/>imports new code, loads YOUR config"]
    s7 --> s8["8 Stop service"]
    s8 --> s9["9 Data migrations"]
    s9 --> s10["10 Activate<br/>atomic symlink switch"]
    s10 --> s11["11 Start service, confirm up"]
    s11 --> s12(["12 Record, prune old releases"])

    s2 & s6 & s7 & s9 & s10 & s11 -. "failure" .-> rb["Roll back<br/>old version stays active,<br/>data restored if migrated"]
```

---

## 10. Doctor and medic

`zensynora doctor`, the Web UI **Doctor** page and the medic agent's tools
share one set of checks (`myclaw/doctor/`, rewritten in 0.7.0).

```mermaid
flowchart LR
    subgraph callers["Callers"]
        c1["zensynora doctor [--fix]"]
        c2["Web UI → Doctor"]
        c3["Medic tools<br/>check_system_health · get_health_report"]
        c4["Gateway startup scan"]
        c5["Agent error diagnosis"]
    end
    callers --> run["run_checks()"]
    subgraph checks["Checks (doctor/checks.py)"]
        e["environment<br/>Python · packages · provider SDKs · disk · RAM · CPU"]
        cf["configuration<br/>file valid · providers · API keys"]
        l["llm (network)<br/>server reachable · model exists"]
        d["data<br/>folders · SQLite quick_check · migrations"]
        sec["security<br/>file permissions · Web UI access"]
        i["install · runtime · integrity<br/>layout · Web UI build · log errors · service · hashes"]
    end
    run --> checks --> rep["Report<br/>ok / warn / fail + how to fix"]
    rep -- "--fix" --> fx["Safe fixes<br/>folders · permissions · migrations"]

    subgraph medicx["Other medic tools"]
        m1["scan_files · verify_file_integrity · recover_file"]
        m2["create_backup · list_backups"]
        m3["validate_modification · detect_errors_in_file · check_file_virustotal"]
        m4["record_task_execution · get_task_analytics"]
        m5["prevent_infinite_loop<br/>called by the router on every step"]
    end
```

---

## 11. Install layout on disk

```mermaid
flowchart TB
    base["~/.local/share/zensynora/"]
    base --> src["source → releases/0.7.0-…<br/>(symlink: the active version)"]
    base --> rel["releases/"]
    rel --> r1["0.7.0-20261013T…<br/>code + venv/ + webui/dist"]
    rel --> r2["0.6.0-…<br/>kept for rollback"]
    base --> bk["backups/<br/>~/.myclaw snapshots (mode 700)"]
    base --> lg["logs/update-*.log"]
    base --> st["updater-state.json"]
    svc["systemd myclaw.service<br/>and source/venv/bin/activate"] -. "use the path through" .-> src
```

---

*Diagrams are Mermaid; GitHub renders them directly. Standalone dark-theme
pages are generated into [`diagrams/`](../diagrams/index.html) with
`python diagrams/generate.py`.*
