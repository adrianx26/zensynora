# ZenSynora Architecture Diagram

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              ZENSYNORA (MyClaw)                              │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                              GATEWAY (gateway.py)                            │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │  Telegram Bot   │  │  WhatsApp Cloud │  │  CLI / Console Chat         │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────────┘
                                     │
                    ┌────────────────┴────────────────┐
                    │                                 │
                    ▼                                 ▼
┌───────────────────────────────────────┐   ┌───────────────────────────────────┐
│         AGENT (agent.py)             │   │         AGENT SWARMS              │
│  ┌─────────────────────────────────┐ │   │  ┌─────────────────────────────┐ │
│  │ System Prompt + User Profile   │ │   │  │ SwarmOrchestrator          │ │
│  ├─────────────────────────────────┤ │   │  ├─────────────────────────────┤ │
│  │ Lifecycle Hooks                  │ │   │  │ Strategies:               │ │
│  │  ├─ pre_llm_call                 │ │   │  │  ├─ parallel                │ │
│  │  ├─ post_llm_call                │ │   │  │  ├─ sequential             │ │
│  │  ├─ on_session_start            │ │   │  │  ├─ hierarchical           │ │
│  │  └─ on_session_end              │ │   │  │  └─ voting                 │ │
│  ├─────────────────────────────────┤ │   │  ├─────────────────────────────┤ │
│  │ Trajectory Compression           │ │   │  │ Aggregation:              │ │
│  │  ├─ History Summarization        │ │   │  │  ├─ consensus             │ │
│  │  ├─ Compression Ratio Logging   │ │   │  │  ├─ best_pick             │ │
│  │  └─ Key Decisions Focus          │ │   │  │  ├─ concatenation        │ │
│  └─────────────────────────────────┘ │   │  │  └─ synthesis             │ │
└───────────────────────────────────────┘   └───────────────────────────────────┘
                    │
                    ▼
┌───────────────────────────────────────────────────────────────────────────────┐
│                            TOOLS (tools.py)                                 │
│                                                                               │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────┐ │
│  │  Core Tools     │ │  Knowledge      │ │  Scheduling     │ │  Swarm      │ │
│  │  ├─ shell      │ │  ├─ search_kb   │ │  ├─ schedule    │ │  ├─ create  │ │
│  │  ├─ read_file  │ │  ├─ write_kb   │ │  ├─ nlp_sched   │ │  ├─ assign  │ │
│  │  └─ browse     │ │  └─ search_fts │ │  └─ list_jobs   │ │  └─ result  │ │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────┘ │
│                                                                               │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐ │
│  │  Skill System  │ │  Learning       │ │  ZenHub         │ │Skill Adapter   │ │Medic Agent     │ │ New Tech Agent │ │
│  │  ├─ register    │ │  ├─ daily_refl │ │  ├─ search      │ │├─analyze_ext   │ │├─check_health  │ │├─fetch_ai_news │ │
│  │  ├─ benchmark   │ │  ├─ insights   │ │  ├─ publish     │ │├─convert_skill │ │├─verify_integr │ │├─get_proposals │ │
│  │  ├─ evaluate   │ │  ├─ extract    │ │  ├─ install     │ │├─list_compat   │ │├─recover_file  │ │├─add_to_roadmap│ │
│  │  ├─ improve    │ │  └─ user_prof  │ │  └─ discover   │ │└─register_ext  │ │├─get_health_rep│ │├─enable_newtech│ │
│  │  └─ rollback   │ │                 │ │                 │ │                │ │├─validate_mod │ │├─run_newtech   │ │
│  │                 │ │                 │ │                 │ │                │ │├─record_task   │ │├─summarize_tech│ │
│  │                 │ │                 │ │                 │ │                │ │├─get_analytics │ │├─generate_prop │ │
│  │                 │ │                 │ │                 │ │                │ │├─enable_hash   │ │└─share_proposal│ │
│  │                 │ │                 │ │                 │ │                │ │├─scan_files    │ │                │ │
│  │                 │ │                 │ │                 │ │                │ │├─detect_errors │ │                │ │
│  │                 │ │                 │ │                 │ │                │ │└─prevent_loop  │ │                │ │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘ └─────────────────┘ │
└───────────────────────────────────────────────────────────────────────────────┘
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
┌──────────────┐ ┌──────────┐ ┌──────────────┐
│   MEMORY     │ │ KNOWLEDGE│ │   TOOLBOX    │
│  (memory.py) │ │ (knowledge/)│  (~/.myclaw/)│
│              │ │           │ │             │
│ ┌──────────┐ │ │ ┌───────┐ │ │ ┌─────────┐ │
│ │ SQLite   │ │ │ │ Graph │ │ │ │ Skills  │ │
│ │ + FTS5   │ │ │ │  DB   │ │ │ └─────────┘ │
│ └──────────┘ │ │ └───┬───┘ │ │ ┌─────────┐ │
│              │ │     │     │ │ │ Registry │ │
│ ┌──────────┐ │ │ ┌───┴───┐ │ │ └─────────┘ │
│ │ Messages │ │ │ │ FTS5 │ │ │ ┌─────────┐ │
│ │ + BM25    │ │ │ └───────┘ │ │ │  ZenHub │ │
│ └──────────┘ │ └───────────┘ │ └─────────┘ │
└──────────────┘ └─────────────┘ └──────────────┘
```

## New Tool Categories Added

```
Phase 1 (Quick Wins)          Phase 2 (Skill System)
─────────────────────         ─────────────────────
• register_hook()            • get_skill_info()
• list_hooks()               • enable_skill()
• clear_hooks()              • disable_skill()
• nlp_schedule()             • update_skill_metadata()
                               • benchmark_skill()
                               • evaluate_skill()
                               • improve_skill()
                               • rollback_skill()

Phase 3 (Memory & Learning)   Phase 4 (ZenHub Ecosystem)
────────────────────────     ─────────────────────────
• schedule_daily_reflection() • hub_search()
• generate_session_insights() • hub_list()
• extract_user_preferences()  • hub_publish()
• update_user_profile()       • hub_install()
• get_user_profile()          • hub_remove()
                               • discover_external_skills()
                               • hub_install_from_external()

Phase 5 (Skill Adapter)      Phase 6 (Medic Agent)
─────────────────────        ─────────────────────
• analyze_external_skill()   • check_system_health()
• convert_skill()             • verify_file_integrity()
• list_compatible_skills()    • recover_file(source="github"|"local")
• register_external_skill()   • get_health_report()
                                • validate_modification()
                                • record_task_execution()
                                • enable_hash_check()
                                • prevent_infinite_loop()
                                • create_backup()
                                • list_backups()
                                • check_file_virustotal()

Phase 7 (New Tech Agent)     Phase 8 (Backends)
───────────────────────      ────────────────
• fetch_ai_news()            • discover_backends()
• get_technology_proposals() • get_default_backend()
• add_to_roadmap()           • LocalBackend.execute()
• enable_newtech_agent()     • DockerBackend.execute()
• run_newtech_scan()         • SSHBackend.execute()
• summarize_tech()         • WSL2Backend.execute()
• generate_tech_proposal()
• share_proposal()
• get_roadmap()```

## New Agents (Phase 5 Implementation)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          AGENT SYSTEM                                      │
└─────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────┐
│  AGENTS PACKAGE (myclaw/agents/)                                           │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────────┐  │
│  │  SkillAdapter    │  │  MedicAgent      │  │  NewTechAgent            │  │
│  │                  │  │                  │  │                          │  │
│  │  ├─ parse_ext()  │  │  ├─ scan_system()│  │  ├─ fetch_ai_news()     │  │
│  │  ├─ convert()     │  │  ├─ hash_check() │  │  ├─ summarize_tech()    │  │
│  │  ├─ discover()    │  │  ├─ recover()     │  │  ├─ generate_proposal()│  │
│  │  └─ register()    │  │  ├─ detect_loop() │  │  └─ add_to_roadmap()   │  │
│  │                   │  │  └─ get_report()   │  │                         │  │
│  │  Tools:          │  │  Tools:           │  │  Tools:                 │  │
│  │  ├─ analyze_ext  │  │  ├─ check_health  │  │  ├─ fetch_ai_news       │  │
│  │  ├─ convert_skill│  │  ├─ verify_integr │  │  ├─ get_proposals       │  │
│  │  ├─ list_compat  │  │  ├─ recover_file  │  │  ├─ add_to_roadmap      │  │
│  │  └─ register_ext │  │  ├─ get_health_rep │  │  ├─ enable_newtech       │  │
│  │                   │  │  ├─ validate_mod │  │  ├─ run_newtech_scan     │  │
│  │                   │  │  ├─ record_task   │  │  ├─ summarize_tech       │  │
│  │                   │  │  ├─ get_analytics │  │  ├─ generate_proposal    │  │
│  │                   │  │  ├─ enable_hash   │  │  ├─ share_proposal       │  │
│  │                   │  │  ├─ scan_files    │  │  └─ get_roadmap          │  │
│  │                   │  │  ├─ detect_errors │  │                          │  │
│  │                   │  │  ├─ prevent_loop  │  │                          │  │
│  │                   │  │  ├─ create_backup │  │  Features:               │  │
│  │                   │  │  ├─ list_backups  │  │  ├─ Opt-in consent       │  │
│  │                   │  │  └─ virustotal   │  │  ├─ GitHub API sharing  │  │
│  │                   │  │                   │  │  ├─ Tech proposals      │  │
│  │                   │  │  Features:        │  │  └─ Roadmap tracking    │  │
│  │                   │  │  ├─ Hash integrity│  │                          │  │
│  │                   │  │  ├─ Local backup  │  │                          │  │
│  │                   │  │  ├─ Error recovery│  │                          │  │
│  │                   │  │  ├─ Loop prevent  │  │                          │  │
│  │                   │  │  ├─ GitHub fetch  │  │                          │  │
│  │                   │  │  └─ VirusTotal   │  │                          │  │
│  └──────────────────┘  └──────────────────┘  └──────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────────────────┐
│  BACKENDS PACKAGE (myclaw/backends/)                                        │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │                         AbstractBackend                              │  │
│  │  ├─ async execute()                                                  │  │
│  │  ├─ async upload()                                                   │  │
│  │  ├─ async download()                                                │  │
│  │  ├─ get_type()                                                       │  │
│  │  └─ is_available()                                                    │  │
│  └──────────────────────────────────────────────────────────────────────┘  │
│                              │                                             │
│         ┌────────────────────┼────────────────────┐                      │
│         ▼                    ▼                    ▼                      │
│  ┌─────────────┐      ┌─────────────┐      ┌─────────────┐               │
│  │  LocalBackend│      │ DockerBackend│      │ SSHBackend   │               │
│  │             │      │             │      │             │               │
│  │ Direct shell│      │ Container   │      │ Remote exec │               │
│  │ exec        │      │ exec        │      │ via SCP     │               │
│  └─────────────┘      └─────────────┘      └─────────────┘               │
│                                                              ┌────────────┐
│                                                              │ WSL2Backend│
│                                                              │            │
│                                                              │ WSL2 Linux │
│                                                              └────────────┘
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                         Backend Discovery                            │  │
│  │  discover_backends()  ──► Auto-detect available backends            │  │
│  │  get_default_backend()  ──► Select based on config or local         │  │
│  │  BackendRegistry        ──► Centralized backend management         │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```

## New Configuration Sections

```
config.json additions:
{
  "medic": {
    "enabled": false,
    "enable_hash_check": true,
    "repo_url": "https://github.com/zensynora/zensynora",
    "scan_on_startup": false,
    "max_loop_iterations": 100,
    "secondary_llm_provider": "",
    "secondary_llm_model": ""
  },
  "newtech": {
    "enabled": false,
    "interval_hours": 24,
    "share_consent": false,
    "github_repo_for_share": "",
    "max_news_items": 10
  },
  "backends": {
    "default_backend": "local",
    "docker": {"container": "zensynora", "image": "zensynora:latest"},
    "ssh": {"host": "", "user": "", "port": 22, "key_path": ""},
    "wsl2": {"distro": "Ubuntu"}
  }
}
```
Phase 1 (Quick Wins)          Phase 2 (Skill System)
─────────────────────         ─────────────────────
• register_hook()            • get_skill_info()
• list_hooks()               • enable_skill()
• clear_hooks()              • disable_skill()
• nlp_schedule()             • update_skill_metadata()
                              • benchmark_skill()
                              • evaluate_skill()
                              • improve_skill()
                              • rollback_skill()

Phase 3 (Memory & Learning)   Phase 4 (ZenHub Ecosystem)
─────────────────────────     ─────────────────────────
• schedule_daily_reflection() • hub_search()
• generate_session_insights() • hub_list()
• extract_user_preferences()  • hub_publish()
• update_user_profile()       • hub_install()
• get_user_profile()          • hub_remove()
                               • discover_external_skills()
                               • hub_install_from_external()
```

## Data Flow: Request Processing

```
User Message
      │
      ▼
┌────────────────┐
│ Gateway        │  Telegram/WhatsApp/CLI
└───────┬────────┘
        │
        ▼
┌────────────────────────────────────────────────────────┐
│ Agent.think()                                           │
│                                                         │
│  1. on_session_start hooks ──────────────────────────► │
│                                                         │
│  2. Context Summarization                              │
│     └─► (if history > threshold)                         │
│         └─► Compress + log ratio                        │
│                                                         │
│  3. Knowledge Base Search (FTS5 + BM25 + recency)      │
│     ├─► Results found ──► Add to context                │
│     └─► No results ─────► Log gap + suggest topics     │
│                                                         │
│  4. pre_llm_call hooks ────────────────────────────────► │
│                                                         │
│  5. LLM Provider (Ollama/OpenAI/etc)                   │
│                                                         │
│  6. post_llm_call hooks ───────────────────────────────► │
│                                                         │
│  7. Tool Execution (if tool_calls)                      │
│     └─► Each tool: audit log + rate limit check        │
│         └─► browse() errors: structured guidance       │
│                                                         │
│  8. on_session_end hooks ─────────────────────────────► │
└────────────────────────────────────────────────────────┘
        │
        ▼
   Response
```

## Tool Execution Pipeline

```
Tool Call Request
        │
        ▼
┌───────────────────┐
│ Rate Limiter      │  (10 calls/min per tool)
│ (Token Bucket)    │
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ Security Check    │  ALLOWED_COMMANDS / BLOCKED_COMMANDS
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ Tool Registry     │  TOOLS dict lookup
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ Async Execution   │  await asyncio.to_thread() or direct
│                   │  for coroutine functions
└───────┬───────────┘
        │
        ▼
┌───────────────────┐
│ Audit Logger      │  ToolAuditLogger
│                   │  (success/failure, duration)
└───────────────────┘
```

## Error Handling Architecture (v2.1)

### Browse Tool Error Handling

```
┌─────────────────────────────────────────────────────────────────┐
│                   BROWSE ERROR HANDLING                         │
└─────────────────────────────────────────────────────────────────┘

    requests.get(url, timeout=30)
              │
              ▼
    ┌─────────────────┐
    │  Error Type     │
    └────────┬────────┘
             │
    ┌────────┴────────┬──────────────┬──────────────┬──────────────┐
    │                 │              │              │              │
    ▼                 ▼              ▼              ▼              ▼
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ Timeout  │   │ Connection│   │  HTTP 404 │   │  HTTP 403 │   │  Other   │
│          │   │  Error    │   │          │   │          │   │          │
├──────────┤   ├──────────┤   ├──────────┤   ├──────────┤   ├──────────┤
│ • Wayback│   │ • Check  │   │ • Check  │   │ • Auth   │   │ • Retry  │
│   suggest│   │   internet│   │   typos  │   │   needed │   │ • Log    │
│ • Check  │   │ • Verify │   │ • Wayback│   │ • Try    │   │ • Report │
│   status │   │   URL    │   │   link   │   │   search │   │          │
│ • search_│   │ • search_│   │ • Web    │   │ • search_│   │          │
│   knowledge│  │   knowledge│  │   search │   │   knowledge│  │          │
└────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘   └────┬─────┘
     │              │              │              │              │
     └──────────────┴──────────────┴──────────────┴──────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────┐
                    │  Structured Response     │
                    │  (emoji + suggestions)   │
                    └──────────────────────────┘
```

### Knowledge Gap Handling

```
┌─────────────────────────────────────────────────────────────────┐
│                   KNOWLEDGE GAP HANDLING                        │
└─────────────────────────────────────────────────────────────────┘

    search_knowledge(query)
              │
              ▼
    ┌─────────────────┐
    │  Results?       │
    └────────┬────────┘
             │
      ┌──────┴──────┐
      │             │
      ▼             ▼
┌──────────┐   ┌──────────┐
│  Yes     │   │   No     │
│          │   │          │
│ Return   │   │ 1. Check │
│ formatted│   │    gap   │
│ results  │   │    cache │
│          │   │          │
│          │   │ 2. If new│
│          │   │    gap:  │
│          │   │    - Log │
│          │   │      to  │
│          │   │      gap │
│          │   │      log │
│          │   │    - Add │
│          │   │      to  │
│          │   │      cache│
│          │   │          │
│          │   │ 3. Return│
│          │   │    guidance│
│          │   │    +     │
│          │   │    suggested│
│          │   │    topics│
└──────────┘   └──────────┘

    Gap Cache (300s timeout):
    ┌─────────────────────────────────┐
    │  Key: "user_id:query"           │
    │  Value: timestamp               │
    │                                 │
    │  is_duplicate() checks:        │
    │  1. Clean expired entries      │
    │  2. Check if key exists        │
    │  3. Store if new               │
    └─────────────────────────────────┘
```

## Skill System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     SKILL LIFECYCLE                             │
└─────────────────────────────────────────────────────────────────┘

    ┌──────────────┐
    │ register_tool()
    └──────┬───────┘
           │
           ▼
    ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
    │ AST Validate │────►│ Code Compile │────►│ Load & Test │
    └──────────────┘     └──────────────┘     └──────┬───────┘
                                                     │
                    ┌──────────────┐                  │
                    │ TOOLBOX_REG  │◄─────────────────┘
                    │ (metadata)   │
                    └──────┬───────┘
                           │
           ┌───────────────┼───────────────┐
           │               │               │
           ▼               ▼               ▼
    ┌────────────┐  ┌────────────┐  ┌────────────┐
    │ evaluate() │  │benchmark()│  │ improve()  │
    └────────────┘  └────────────┘  └──────┬─────┘
                                          │
                    ┌────────────────────┼────────────────────┐
                    │                    │                    │
                    ▼                    ▼                    ▼
             ┌──────────┐         ┌──────────────┐      ┌──────────┐
             │ Safety   │         │  Auto-backup │      │ Version  │
             │ Checks   │         │  (.bak file)  │      │ Increment│
             └──────────┘         └──────────────┘      └──────────┘
                                          │
                                          ▼
                                   ┌──────────┐
                                   │rollback()│
                                   └──────────┘
```

## User Profile System

```
┌─────────────────────────────────────────────────────────────────┐
│                    USER DIALECTIC PROFILE                       │
└─────────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────┐
    │         user_dialectic.md (profile template)     │
    ├─────────────────────────────────────────────────┤
    │ ## Communication Style                           │
    │ ## Technical Profile                            │
    │ ## Interaction Patterns                          │
    │ ## Preferences                                   │
    │ ## Learning History                              │
    └─────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────────────┐
│                    Agent Initialization                      │
│                                                            │
│  agent.py __init__()                                       │
│       │                                                     │
│       ▼                                                     │
│  Load system prompt (default.md / custom)                  │
│       │                                                     │
│       ▼                                                     │
│  Check user_dialectic.md exists?                            │
│       │                                                     │
│       ▼                                                     │
│  Append dialectic profile to system_prompt                  │
└────────────────────────────────────────────────────────────┘

    ┌─────────────────────────────────────────────────┐
    │         Runtime Profile Updates                  │
    ├─────────────────────────────────────────────────┤
    │ extract_user_preferences() ──► writes to KB     │
    │ update_user_profile()       ──► writes .md file  │
    │ schedule_daily_reflection()  ──► daily summary   │
    └─────────────────────────────────────────────────┘
```

## ZenHub Registry

```
┌─────────────────────────────────────────────────────────────────┐
│                         ZENHUB REGISTRY                         │
└─────────────────────────────────────────────────────────────────┘

    ~/.myclaw/
    │
    ├── hub/
    │   ├── index.json          # Skill metadata index
    │   └── skills/             # Published skill files
    │       ├── skill1.py
    │       ├── skill2.py
    │       └── ...
    │
    └── skills/                 # External skills (auto-discover)
        ├── skill_a.py
        └── skill_b.py


┌────────────────────────────────────────────────────────────┐
│                    ZenHub Operations                        │
├────────────────────────────────────────────────────────────┤
│                                                            │
│  hub_search(query)  ────► Scan index.json by name/desc/tags
│                                                            │
│  hub_publish(name)  ────► Copy from TOOLBOX to hub/skills
│                                                            │
│  hub_install(name) ────► Copy from hub to TOOLBOX          │
│                                                            │
│  hub_remove(name)  ────► Delete from index + file         │
│                                                            │
│  discover_external() ──► Scan ~/.myclaw/skills/            │
│                                                            │
│  hub_install_external() ──► Import from external to TOOLBOX│
│                                                            │
└────────────────────────────────────────────────────────────┘
```

## File Structure Summary

```
myclaw/
├── __init__.py
├── agent.py           # Agent class with hooks + profile loading
├── tools.py            # All tools
├── memory.py           # Memory with enhanced FTS5 search
├── provider.py        # LLM providers
├── config.py          # Configuration (with medic/newtech/backends)
├── gateway.py         # Gateway startup
├── agents/             # Specialized Agents (NEW)
│   ├── __init__.py
│   ├── skill_adapter.py   # Skill compatibility agent
│   ├── medic_agent.py     # System health monitoring
│   └── newtech_agent.py   # AI news monitoring
├── backends/           # Terminal Backends (NEW)
│   ├── __init__.py
│   ├── base.py           # AbstractBackend base class
│   ├── local.py          # Local shell execution
│   ├── docker.py         # Docker container execution
│   ├── ssh.py            # SSH remote execution
│   ├── wsl2.py           # WSL2 execution
│   └── discover.py       # Backend discovery
├── swarm/             # Agent Swarms
│   ├── orchestrator.py
│   ├── models.py
│   ├── storage.py
│   └── strategies.py
├── knowledge/         # Knowledge Base
│   ├── db.py
│   ├── graph.py
│   ├── storage.py
│   └── sync.py
├── channels/          # Communication Channels
│   ├── telegram.py
│   └── whatsapp.py
├── hub/               # ZenHub Registry
│   └── __init__.py
├── mcp/               # Model Context Protocol
│   ├── __init__.py
│   ├── client.py        # MCP Client connections
│   └── server.py        # MCP Server exposure
├── profiles/         # Agent Profiles
│   ├── default.md
│   ├── user.md
│   └── user_dialectic.md
```

## Model Context Protocol (MCP)

```
┌─────────────────────────────────────────────────────────────────┐
│                    MCP CLIENT & SERVER                          │
└─────────────────────────────────────────────────────────────────┘

     External Clients                   External Servers
     (Cursor, Claude)                  (SQLite, WebSearch)
            │                                  │
            ▼                                  ▼
    ┌───────────────┐                  ┌───────────────┐
    │  MCP Server   │                  │  MCP Client   │
    │  (server.py)  │                  │  (client.py)  │
    └───────┬───────┘                  └───────┬───────┘
            │                                  │
            ▼                                  ▼
    ┌──────────────────────────────────────────────────┐
    │                   myclaw.tools                   │
    └──────────────────────────────────────────────────┘
```

## Legend

```
┌─────────┐  Component/Module
│  text   │  Process/Function
└─────────┘

──────►   Data/Control Flow
──▼──     Conditional/Branch
```

*Generated: 2026-03-29*
*Last Updated: 2026-04-10 (Added Knowledge Gap Handling & Enhanced Error Handling v2.1)*
*Part of: ZenSynora Full Implementation*