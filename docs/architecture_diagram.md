# ZenSynora Architecture — Visual Guide

> **Version:** 0.7.1 · **Last updated:** 2026-10-13
> 0.7.1: chat channels and agent-to-agent events (§12), jobs (§13),
> approval-gated self-update (§14), backups, model rotation and custom
> providers (§4), full program-folder backup in updates (§9).
> Unreleased: the Telegram bot can run inside the Web UI process, message
> flow through queue and workers, shared knowledge base (§1, §2, §15).
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
12. [Chat channels and agent-to-agent talk](#12-chat-channels-and-agent-to-agent-talk)
13. [Jobs](#13-jobs)
14. [Self-update proposals](#14-self-update-proposals)
15. [Telegram bot](#15-telegram-bot)

---

## 1. System context

Who talks to ZenSynora, and what ZenSynora talks to.

```mermaid
flowchart LR
    user(["You"])
    subgraph entry["Entry points"]
        webui["Web UI<br/>browser · port 8000"]
        cli["Terminal<br/>zensynora agent"]
        tg["Telegram bot<br/>inside zensynora webui<br/>or zensynora gateway"]
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
        webapi["web/<br/>api · settings · models · chat · jobs · backup · medic · telegram"]
        gw["gateway.py<br/>zensynora gateway: Telegram / WhatsApp / console"]
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
        chatx["chat/<br/>channels · multi-agent turns"]
        jobsx["jobs/<br/>store · runner · evaluate · benchmark"]
    end

    subgraph L3["Capabilities"]
        direction LR
        tools["tools/<br/>shell · files · web · browser · kb · scheduler · swarm · toolbox"]
        provider["provider.py<br/>8 built-in + your own providers · rotation"]
        events["events.py<br/>agent-to-agent activity"]
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
        updater["updater/ (repo root)<br/>safe updates · app backup · migrations"]
        selfupd["selfupdate.py<br/>agent proposals → updater"]
        backupx["backup.py<br/>settings · keys · knowledge"]
        obs["observability/ · metrics.py · logging_config.py"]
    end

    L1 --> L2 --> L3 --> L4
    L5 -.-> L1 & L2 & L3 & L4
```

---

## 3. A chat message, end to end

What happens inside one agent when it answers (Telegram and the terminal
follow the same path from `Agent` onwards; the Telegram steps before `Agent`
are in [§15](#15-telegram-bot)). Since 0.7.1 the Web UI reaches
`Agent.stream_think` through chat channels — see [§12](#12-chat-channels-and-agent-to-agent-talk);
the per-agent socket shown here still works for scripts.

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
    P-->>A: token stream (+ tool calls, collected at the end)
    A-->>WS: tokens
    opt no text and no tool calls (empty reply)
        A->>P: ask once more without streaming
        P-->>A: reply, or "model returned an empty answer" note
    end
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

## 4. Provider selection, rotation and fallback

How a request reaches a model, and what happens when that fails. Changed in
0.5.0: the agent never silently switches to another provider *unless you
asked for it* — 0.7.1 adds an explicit rotation list (Models page).

```mermaid
flowchart TD
    start(["Agent needs a reply"]) --> rot{"Rotation on and this agent<br/>uses the default model?"}
    rot -- yes --> order["Candidates = default + rotation list<br/>(round-robin: start at the next one)"]
    order --> tryc["provider.chat() with candidate"]
    tryc -- ok --> replied(["Stream reply<br/>(model shown on the message)"])
    tryc -- "error: rate limit, down,<br/>missing model, in-stream error" --> nextc{"more candidates?"}
    nextc -- yes --> tryc
    nextc -- no --> fail(["Error shown in chat"])
    rot -- no --> pick["Provider = agent's own, else agents.defaults.provider<br/>(built-in or one of your providers)"]
    pick --> init{"Provider can start?<br/>(SDK installed, API key set)"}
    init -- no --> err["ProviderUnavailableError<br/>clear message shown in chat"]
    init -- yes --> breaker{"Circuit breaker<br/>open?"}
    breaker -- closed --> llmcall["provider.chat()"]
    llmcall -- ok --> replied
    llmcall -- "server rejects tool list<br/>(llama.cpp without --jinja)" --> notools["Retry once without tools<br/>remember for next calls"]
    notools --> replied
    llmcall -- "connection error" --> offline{"intelligence.offline_mode?"}
    breaker -- open --> offline
    offline -- no --> fail
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
| *your own* (`providers.custom`) | any OpenAI-compatible server, or another Ollama | `openai` / httpx |

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
        med["medic/<br/>integrity baseline · file backups · task log · proposals/"]
        chatdb["chat.db · jobs.db<br/>channels + messages · jobs + runs (0.7.1)"]
        logs["logs/zensynora.log<br/>rotating JSON log (since 0.7.0)"]
        mig[".migrations.json<br/>applied data migrations"]
    end
    app["ZenSynora (any version)"] <--> data
    upd["updater"] -- "backup before every update" --> data
    bkp["~/.myclaw-backups/<br/>your backups (manual / automatic)"] <-- "zensynora backup · Backups page" --> data
    doc["doctor / medic"] -- "reads + safe fixes" --> data
```

---

## 9. Safe updates

`zensynora update` (`updater/`, added in 0.6.0; program-folder backup and
data check added in 0.7.1). Steps 1–10 never touch the running version; any
failure undoes the completed steps in reverse order.

```mermaid
flowchart TD
    s1["1 Preflight<br/>Python, disk space, service state"] --> s2["2 Fetch<br/>GitHub, folder or archive"]
    s2 --> s3["3 Hand-off<br/>newer updater takes over"]
    s3 --> s4["4 Back up ~/.myclaw + .env"]
    s4 --> s5["5 Back up the whole program folder<br/>archive + inventory of every file"]
    s5 --> s6["6 Carry over .env, data/"]
    s6 --> s7["7 Build side by side<br/>venv · packages · extras · Web UI"]
    s7 --> s8["8 Verify<br/>imports new code, loads YOUR config"]
    s8 --> s9["9 Stop service"]
    s9 --> s10["10 Data migrations"]
    s10 --> s11["11 Activate<br/>atomic symlink switch"]
    s11 --> s12["12 Settings, API keys, knowledge<br/>checked, restored if missing"]
    s12 --> s13["13 Start service, confirm up"]
    s13 --> s14(["14 Record, prune old releases"])

    s2 & s7 & s8 & s10 & s11 & s13 -. "failure" .-> rb["Roll back<br/>old version active again · data restored if migrated ·<br/>program folder compared with the inventory and<br/>restored from the archive if anything differs"]
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
        m6["read_source_file · propose_code_change ·<br/>get_recent_problems · record_lesson (0.7.1, §14)"]
    end
```

When an agent turn fails (2026-10-18), errors from the model server are told
apart from real crashes; only the latter start the medic diagnosis.

```mermaid
flowchart LR
    err["exception in the agent turn"] --> kind{"is_provider_error?<br/>(llm_errors.py)"}
    kind -- "rate limit, no rotation" --> retry["wait 5 s, retry once<br/>same model + key"] --> ok(["answer"])
    retry -- "still failing" --> warn
    kind -- "rate limit · timeout · bad key ·<br/>unknown model" --> warn["one WARNING line<br/>+ readable reason in chat"]
    kind -- "anything else" --> crash["ERROR + traceback"] --> diag["medic diagnosis<br/>check_system_health · check_memory_usage ·<br/>get_health_report (from agents.medic_agent)"]
```

---

## 11. Install layout on disk

```mermaid
flowchart TB
    base["~/.local/share/zensynora/"]
    base --> src["source → releases/0.7.1-…<br/>(symlink: the active version)"]
    base --> rel["releases/"]
    rel --> r1["0.7.1-20261013T…<br/>code + venv/ + webui/dist"]
    rel --> r2["0.7.0-…<br/>kept for rollback"]
    base --> bk["backups/<br/>~/.myclaw snapshots + app-folder.tar.gz (mode 700)"]
    base --> stg["staging/<br/>temporary copies for approved proposals"]
    base --> lg["logs/update-*.log"]
    base --> st["updater-state.json"]
    svc["systemd myclaw.service<br/>and source/venv/bin/activate"] -. "use the path through" .-> src
```

---

## 12. Chat channels and agent-to-agent talk

Added in 0.7.1 (`myclaw/chat/`, `myclaw/events.py`, `myclaw/web/chat_api.py`,
`webui/src/components/ChatView.tsx`).

```mermaid
sequenceDiagram
    autonumber
    actor U as You
    participant UI as Chat (React)
    participant WS as /ws/channels/{id}
    participant E as ChatEngine
    participant S as chat.db
    participant A1 as @default
    participant A2 as @writer
    participant EV as events.py

    U->>UI: "@default ask writer for a haiku"
    UI->>WS: {type: message}
    WS->>E: post()
    E->>S: save your message
    E->>E: who answers? @mentions, else reply mode<br/>(lead · everyone in turn · everyone at once)
    E->>A1: stream_think(group context + message)<br/>CURRENT_CHANNEL / CURRENT_AGENT set
    A1->>EV: delegation_start (default → writer)
    EV-->>WS: event → card in chat + Activity panel
    EV->>S: saved as an "event" message
    A1->>A2: delegate tool → think(task)
    A2-->>A1: haiku
    A1->>EV: delegation_end (+ result, ms)
    A1-->>E: answer tokens
    E-->>WS: stream_start · chunk… · stream_end (model, time)
    E->>S: save the answer
```

* Each `(channel, agent)` pair has its own conversation memory
  (`MEMORY_SCOPE`); the knowledge base is shared.
* Tool calls (`tool_start` / `tool_end`), swarm hand-offs and job runs travel
  the same way. Nothing is emitted when no channel is set, so the CLI,
  Telegram and tests are unaffected.

---

## 13. Jobs

Added in 0.7.1 (`myclaw/jobs/`, `myclaw/web/jobs_api.py`, Jobs page).

```mermaid
flowchart LR
    ui["Jobs page / API<br/>add · edit · pause · run now"] --> store[("jobs.db<br/>jobs + runs")]
    tick["runner loop<br/>(every few s, inside zensynora webui)"] -- "due jobs" --> run
    ui -- "Run now" --> run["run_job()<br/>agent.think(prompt)<br/>timeout · retries · clean memory"]
    run --> store
    run -- "post result" --> chan["channel (optional)"]
    run -- "auto-evaluate" --> judge["main model scores 0-10<br/>against success criteria"]
    ui -- "Benchmark" --> bench["same prompt on up to 8 models<br/>time + score side by side"]
    bench --> store
    judge --> store
    tick -- "also" --> ab["automatic backups when due"]
```

---

## 14. Self-update proposals

Added in 0.7.1 (`myclaw/selfupdate.py`, `myclaw/web/medic_api.py`, Doctor page,
`zensynora medic`). Agents never write to the program folder.

```mermaid
flowchart TD
    probs["get_recent_problems<br/>log errors · failed / low-score jobs"] --> agent["Agent (medic self-review job,<br/>or any agent you ask in chat)"]
    agent -- read_source_file --> code[("installed code, read-only")]
    agent -- propose_code_change --> check{"inside program folder? no venv/.git/.env?<br/>Python compiles? size ok?"}
    check -- no --> back["refused · reason returned to the agent"]
    check -- yes --> prop["proposal + diff<br/>~/.myclaw/medic/proposals/"]
    prop --> you{"You review<br/>Doctor → Change proposals"}
    you -- reject --> rj["rejected"]
    you -- approve --> stale{"code unchanged<br/>since the proposal?"}
    stale -- no --> outd["failed: ask for a new proposal"]
    stale -- yes --> stage["staging/ = copy of current release + change"]
    stage --> upd["update.sh update --from-dir staging --force<br/>(all steps of §9)"]
    upd -- ok --> ok["new release active · restart to use it ·<br/>zensynora update rollback undoes it"]
    upd -- failure --> rb["rolled back · running version unchanged"]
    agent -- record_lesson --> kb[("knowledge note<br/>medic-lessons")]
    kb -. "found by later searches" .-> agent
```

---

## 15. Telegram bot

`myclaw/channels/telegram.py`, started by `myclaw/web/telegram_api.py` (inside
`zensynora webui`) or by `myclaw/gateway.py` (`zensynora gateway`). User guide:
[telegram_guide.md](telegram_guide.md).

**Where the bot runs.** With `channels.telegram.enabled` and
`channels.telegram.run_in_webui` (default on) the Web UI process starts the
bot. `zensynora gateway` runs it without the Web UI. Telegram lets only one
copy of a bot receive messages: a second copy gets "conflict" and the Web UI
stops its own.

```mermaid
flowchart LR
    tgapp(["Telegram app<br/>you, on your phone"]) <--> tgsrv["Telegram servers"]
    subgraph webproc["zensynora webui · one process"]
        tapi["web/telegram_api.py<br/>status · test · restart"]
        tch1["TelegramChannel"]
        reg1["agent registry<br/>same agents as the Web UI"]
        tapi --> tch1 --> reg1
    end
    subgraph gwproc["zensynora gateway · alternative"]
        tch2["TelegramChannel"]
        reg2["agent registry"]
        tch2 --> reg2
    end
    tch1 -- "polling" --> tgsrv
    tch2 -. "polling, only if the Web UI copy is off" .-> tgsrv
    reg1 & reg2 --> kb[("main knowledge base<br/>shared with the Web UI")]
    reg1 & reg2 --> mem[("conversation memory<br/>one per Telegram user")]
    settings["Settings → Channels<br/>Test bot · Restart bot"] --> tapi
```

**One message, end to end.**

```mermaid
sequenceDiagram
    autonumber
    actor U as You in Telegram
    participant T as Telegram servers
    participant C as TelegramChannel
    participant Q as Queue
    participant W as Worker
    participant A as Agent
    U->>T: "@writer a haiku about rain"
    T->>C: update via polling
    C->>C: allowFrom check: numeric id or @username
    alt not allowed
        C-->>C: log the user id once and ignore
    else allowed
        C->>Q: put the message
        Q->>W: next message of this chat
        Note over Q,W: several workers, so several chats in parallel,<br/>one message at a time per chat
        W->>T: typing… while the agent works
        W->>W: @agent routing: remove @YourBot,<br/>match @writer ignoring case, else default agent
        W->>A: think(text) with this user's memory
        A-->>W: answer, or a short plain error
        W->>W: split into messages of at most 4096 characters
        W->>T: send each part, as plain text if Markdown fails
        T-->>U: answer
    end
```

* `/start`, `/help` and `/whoami` answer everyone; all other commands need an
  allowed user.
* Photos, voice messages and documents: the caption is used as the message;
  without a caption the bot says it can only read text for now.
* Agents created later (`create_agent`, Settings) are found without a restart.
* Answers from a named agent start with `@Name: `.
* Polling is the default; `zensynora gateway --webhook-url … --port 8443`
  switches the gateway copy to webhook mode (path `/webhook`). The Web UI copy
  always polls. `ZENSYNORA_TELEGRAM_API_URL` points either copy at a
  self-hosted Bot API server.

---

*Diagrams are Mermaid; GitHub renders them directly. Standalone dark-theme
pages are generated into [`diagrams/`](../diagrams/index.html) with
`python diagrams/generate.py`.*

## 16. Providers, keys and agents

Since the Unreleased changes a provider can hold several named API keys
(`providers.<name>.keys`; the plain `api_key` is the *Default key*). Each agent,
the default model and every rotation entry pick a provider, a key and a model.
With `providers.<name>.rotate_keys` on, a rate-limited or exhausted key makes
the agent try the same model with the provider's other keys before the next
model. Cooldowns are kept per provider, model and key.

```mermaid
flowchart LR
    agent["Agent<br/>provider · key · model"] --> pick{"key id"}
    pick -- "empty" --> defkey["Default key<br/>providers.X.api_key"]
    pick -- "work" --> named["Named key<br/>providers.X.keys[work]"]
    pick -- "unknown" --> warn["warning in the log<br/>uses the Default key"] --> defkey
    defkey & named --> client["provider client<br/>cached per provider + key"]
    client --> llm(["model server"])
    llm -- "429 · quota · 401" --> rot{"rotate_keys on?"}
    rot -- "yes" --> other["same model,<br/>next key of the provider"] --> client
    rot -- "no" --> next["next model in the rotation list"]
```

## 17. Knowledge page (Web UI)

The Knowledge page reads the Markdown notes directly (they are the source of
truth); the SQLite index is used for full-text search and is updated after
every edit. A relation whose target note does not exist becomes a "missing"
node.

```mermaid
flowchart LR
    ui["Knowledge page<br/>graph · notes · tags · panel"] -- "GET graph / note" --> api["knowledge_api.py"]
    ui -- "POST / PUT note" --> api
    ui -- "DELETE note" --> api
    api -- "parse every *.md" --> md[("~/.myclaw/knowledge/&lt;base&gt;/*.md")]
    api -- "nodes · edges · tags · categories" --> ui
    api -- "write file, then re-index" --> md
    api --> db[("knowledge_&lt;base&gt;.db<br/>FTS index")]
    api -- "move, not delete" --> trash[("&lt;base&gt;/.trash")]
    agents["agents<br/>write_to_knowledge …"] --> md
```

The model test on Settings → Agents uses `POST /api/models/test` (one short
real request) and colours the round-trip time: green < 1.5 s, amber < 5 s,
red above or failed.

## 18. Context length per agent

```mermaid
flowchart LR
    ui["Settings → Agents<br/>slider + number box"] -- "prompt · skills · provider · model<br/>(after each edit)" --> est["POST /api/agents/context-estimate"]
    est --> parts["estimate_context()<br/>prompt + skills + team + tool schemas<br/>→ min · recommended"]
    est --> mx["model_context_max()<br/>Ollama /api/show · LM Studio · llama.cpp /props ·<br/>/models lists · Gemini · Anthropic · model library"]
    parts & mx --> ui
    ui -- "save: context_length" --> cfg[("config.json<br/>agents.*.context_length")]
    cfg --> turn["agent turn"]
    turn --> fit["fit_messages()<br/>drop oldest messages to fit"]
    turn --> var["CONTEXT_LENGTH contextvar"] --> oll["Ollama: options.num_ctx"]
```

`myclaw/context_budget.py` holds all of it; token counts are estimated at
about 3.8 characters per token.

