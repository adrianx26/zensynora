# ZenSynora Architecture Diagram

## System Overview

```mermaid
flowchart TB
    subgraph Gateway ["GATEWAY (gateway.py)"]
        direction LR
        TG["Telegram Bot"]
        WA["WhatsApp Cloud"]
        CLI["CLI / Console Chat"]
    end

    Gateway --> AgentRouter

    subgraph AgentSystem ["AGENT SYSTEM"]
        direction TB
        AgentRouter{Agent Router}
        Agent["Agent (agent.py)"]
        Swarm["Agent Swarms (swarm/)"]
    end

    AgentSystem --> Tools

    subgraph Tools ["TOOLS (myclaw/tools/)"]
        direction TB
        T_Core["core.py"]
        T_Shell["shell.py"]
        T_Files["files.py"]
        T_Web["web.py"]
        T_KB["kb.py"]
        T_Sched["scheduler.py"]
        T_Swarm["swarm.py"]
        T_Toolbox["toolbox.py"]
    end

    Tools --> Storage

    subgraph Storage ["PERSISTENCE & STORAGE"]
        direction LR
        Memory["MEMORY (memory.py)"]
        Knowledge["KNOWLEDGE (knowledge/)"]
        Toolbox["TOOLBOX (~/.myclaw/)"]
        State["STATE STORE (state_store.py)"]
        AsyncSched["ASYNC SCHEDULER (async_scheduler.py)"]
    end
```

## New Tool Categories (Evolution)

```mermaid
flowchart LR
    subgraph P1 ["Phase 1: Quick Wins"]
        direction TB
        T1["register_hook()<br/>list_hooks()<br/>nlp_schedule()"]
    end

    subgraph P2 ["Phase 2: Skill System"]
        direction TB
        T2["benchmark_skill()<br/>evaluate_skill()<br/>improve_skill()"]
    end

    subgraph P3 ["Phase 3: Memory & Learning"]
        direction TB
        T3["generate_session_insights()<br/>extract_user_preferences()<br/>update_user_profile()"]
    end

    subgraph P4 ["Phase 4: ZenHub Ecosystem"]
        direction TB
        T4["hub_search()<br/>hub_install()<br/>discover_external_skills()"]
    end

    P1 --> P2 --> P3 --> P4
```

## Agent Package (myclaw/agents/)

```mermaid
flowchart TB
    subgraph Agents ["Specialized Agents"]
        direction LR
        SA["SkillAdapter"]
        MA["MedicAgent"]
        NTA["NewTechAgent"]
    end

    SA --- SA_Tools["analyze_ext<br/>convert_skill<br/>register_ext"]
    MA --- MA_Tools["check_health<br/>verify_integr<br/>recover_file<br/>validate_mod"]
    NTA --- NTA_Tools["fetch_ai_news<br/>generate_prop<br/>add_to_roadmap"]

    Agents --> Registry["Registry & Discovery"]
```

## Backends Package (myclaw/backends/)

```mermaid
classDiagram
    class AbstractBackend {
        <<abstract>>
        +execute(command)
        +upload(local, remote)
        +download(remote, local)
        +is_available()
    }
    class LocalBackend
    class DockerBackend
    class SSHBackend
    class WSL2Backend

    AbstractBackend <|-- LocalBackend
    AbstractBackend <|-- DockerBackend
    AbstractBackend <|-- SSHBackend
    AbstractBackend <|-- WSL2Backend

    class BackendDiscovery {
        +discover_backends()
        +get_default_backend()
    }
```

## Data Flow: Request Processing

```mermaid
sequenceDiagram
    participant User
    participant Gateway
    participant Agent
    participant Hooks
    participant KB as Knowledge Base
    participant LLM as LLM Provider
    participant Tools as Tool Executor

    User->>Gateway: Sends Message
    Gateway->>Agent: think(message)
    Agent->>Hooks: on_session_start
    Agent->>KB: search_knowledge(query)
    KB-->>Agent: Results found
    Agent->>Hooks: pre_llm_call
    Agent->>LLM: generate_response(context)
    LLM-->>Agent: tool_calls (if needed)

    loop Tool Execution
        Agent->>Tools: execute(tool_name, args)
        Tools->>Agent: tool_output
    end

    Agent->>Hooks: post_llm_call
    Agent->>Hooks: on_session_end
    Agent-->>Gateway: final_response
    Gateway-->>User: Delivers Response
```

## Tool Execution Pipeline

```mermaid
flowchart TD
    Req["Tool Call Request"] --> RL["Rate Limiter (Token Bucket)"]
    RL --> SC["Security Check (Allowlist)"]
    SC --> TR["Tool Registry Lookup"]
    TR --> Exec["Async Execution"]
    Exec --> AL["Audit Logger (duration, status)"]
```

## Error Handling: Browse Tool

```mermaid
flowchart TD
    Req["requests.get(url)"] --> ErrType{Error Type?}
    ErrType -- Timeout --> Way1["Wayback Machine Suggestion"]
    ErrType -- 404 --> Way2["Wayback + Search Suggestions"]
    ErrType -- 403 --> KB_S["Suggest search_knowledge()"]
    ErrType -- Conn --> Net["Check Internet/URL Verification"]

    Way1 --> Res["Structured Response"]
    Way2 --> Res
    KB_S --> Res
    Net --> Res
```

## Knowledge Gap Handling

```mermaid
flowchart TD
    Search["search_knowledge(query)"] --> Found{Results Found?}
    Found -- Yes --> Return["Return formatted results"]
    Found -- No --> Cache{In Gap Cache?}

    Cache -- Yes --> Guidance["Return guidance + suggestions"]
    Cache -- No --> Log["Log to gap_log"]
    Log --> AddCache["Add to Gap Cache (300s)"]
    AddCache --> Guidance
```

## Skill Lifecycle

```mermaid
flowchart LR
    Reg["register_tool()"] --> AST["AST Validate"]
    AST --> Compile["Code Compile"]
    Compile --> Load["Load & Test"]
    Load --> Toolbox["TOOLBOX REGISTRY"]

    Toolbox --> Eval["evaluate()"]
    Toolbox --> Bench["benchmark()"]
    Toolbox --> Imp["improve()"]

    Imp --> Backup["Auto-backup (.bak)"]
    Backup --> Rollback["rollback()"]
```

## User Profile System

```mermaid
flowchart TB
    Init["Agent Initialization"] --> LoadMD["Load user_dialectic.md"]
    LoadMD --> Prompt["Append to System Prompt"]

    subgraph Updates ["Runtime Updates"]
        direction LR
        Extract["extract_preferences()"]
        Update["update_user_profile()"]
        Reflect["daily_reflection()"]
    end

    Updates --> KB["Knowledge Base"]
    Updates --> ProfileMD["user_dialectic.md"]
```

## ZenHub Registry

```mermaid
flowchart TD
    subgraph FS ["File System (~/.myclaw/)"]
        Hub["hub/"]
        Hub --- Index["index.json"]
        Hub --- H_Skills["skills/ (Published)"]
        Skills["skills/ (External)"]
    end

    subgraph Ops ["ZenHub Operations"]
        direction TB
        Search["hub_search()"]
        Publish["hub_publish()"]
        Install["hub_install()"]
        Discover["discover_external()"]
    end

    Search --> Index
    Publish --> H_Skills
    Install --> Toolbox["TOOLBOX"]
    Discover --> Skills
```

## File Structure Summary (myclaw/)

```mermaid
flowchart TD
    Root["myclaw/"]
    Root --- Core["agent.py<br/>tools/ (pkg)<br/>memory.py<br/>config.py<br/>state_store.py"]
    Root --- Agents["agents/ (pkg)<br/>medic, newtech, adapter"]
    Root --- Backends["backends/ (pkg)<br/>local, docker, ssh, wsl2"]
    Root --- Knowledge["knowledge/ (pkg)<br/>db, graph, sync, researcher"]
    Root --- Swarm["swarm/ (pkg)<br/>orchestrator, strategies"]
    Root --- Channels["channels/ (pkg)<br/>telegram, whatsapp"]
```

## Model Context Protocol (MCP)

```mermaid
flowchart LR
    subgraph ExtClients ["External Clients"]
        Cursor["Cursor"]
        Claude["Claude Desktop"]
    end

    subgraph ZenServer ["MCP Server"]
        MS["mcp/server.py"]
    end

    subgraph ZenClient ["MCP Client"]
        MC["mcp/client.py"]
    end

    subgraph ExtServers ["External Servers"]
        Sqlite["SQLite Server"]
        WebS["WebSearch Server"]
    end

    ExtClients <--> MS
    MS <--> Tools["myclaw.tools"]
    MC <--> Tools
    MC <--> ExtServers
```

*Generated: 2026-04-21*
*Last Updated: 2026-04-21 (Mermaid redesign, package structure update)*
*Part of: ZenSynora Full Implementation*
