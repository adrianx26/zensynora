# ZenSynora Comprehensive System Diagram

This document provides a highly detailed view of the ZenSynora (MyClaw) architecture, including all major modules, classes, and primary functions.

## 🗺️ Master Component Map

```mermaid
flowchart TB
    subgraph Gateways ["Channels & Gateways (myclaw/channels/)"]
        TG["TelegramChannel<br/>(telegram.py)"]
        WA["WhatsAppChannel<br/>(whatsapp.py)"]
        CLI["CLI Console<br/>(cli.py)"]
        WebUI["Web UI Dashboard<br/>(webui/)"]
    end

    subgraph Core ["Core Agent Engine (myclaw/)"]
        Agent["Agent Class<br/>(agent.py)"]
        Router["MessageRouter<br/>(agent/message_router.py)"]
        Context["ContextBuilder<br/>(agent/context_builder.py)"]
        Executor["ToolExecutor<br/>(agent/tool_executor.py)"]
        Response["ResponseHandler<br/>(agent/response_handler.py)"]

        Agent --- Router
        Agent --- Context
        Agent --- Executor
        Agent --- Response
    end

    subgraph LLM ["LLM Provider Layer (myclaw/provider.py)"]
        P_Base["Provider Interface"]
        P_Ollama["OllamaProvider"]
        P_OpenAI["OpenAIProvider"]
        P_Claude["AnthropicProvider"]
        Pool["HTTPClientPool"]
    end

    subgraph Tools ["Tool Ecosystem (myclaw/tools/)"]
        T_Reg["Tool Registry<br/>(core.py)"]
        T_Shell["shell(cmd)<br/>(shell.py)"]
        T_Files["read/write_file()<br/>(files.py)"]
        T_Web["browse/download()<br/>(web.py)"]
        T_KB["search/read_kb()<br/>(kb.py)"]
        T_Swarm["swarm_create/assign()<br/>(swarm.py)"]
        T_Sched["schedule/jobs()<br/>(scheduler.py)"]
        T_Toolbox["register_tool()<br/>(toolbox.py)"]
    end

    subgraph Knowledge ["Knowledge Base (myclaw/knowledge/)"]
        K_DB["KnowledgeDB<br/>(db.py)"]
        K_Sync["KnowledgeSync<br/>(sync.py)"]
        K_Graph["KnowledgeGraph<br/>(graph.py)"]
        K_Res["GapResearcher<br/>(researcher.py)"]
        K_Pars["NoteParser<br/>(parser.py)"]
    end

    subgraph Swarm ["Agent Swarms (myclaw/swarm/)"]
        S_Orch["SwarmOrchestrator<br/>(orchestrator.py)"]
        S_Strat["Strategies<br/>(strategies.py)"]
        S_Stor["SwarmStorage<br/>(storage.py)"]
        S_Agg["AggregationEngine"]
    end

    subgraph Specialized ["Specialized Agents (myclaw/agents/)"]
        A_Medic["MedicAgent<br/>(medic_agent.py)"]
        A_Evol["EvolverEngine<br/>(medic_evolver.py)"]
        A_Change["ChangeMgmt<br/>(medic_change_mgmt.py)"]
        A_Tech["NewTechAgent<br/>(newtech_agent.py)"]
        A_Adap["SkillAdapter<br/>(skill_adapter.py)"]
    end

    subgraph Persistence ["Persistence Layer (myclaw/)"]
        Memory["Memory (SQLite/FTS5)<br/>(memory.py)"]
        State["StateStore (Redis/IM)<br/>(state_store.py)"]
        AsyncSched["AsyncScheduler<br/>(async_scheduler.py)"]
        Config["Config Manager<br/>(config.py)"]
    end

    subgraph Backends ["Execution Backends (myclaw/backends/)"]
        B_Router["BackendRouter"]
        B_Local["LocalBackend"]
        B_Docker["DockerBackend"]
        B_SSH["SSHBackend"]
        B_WSL2["WSL2Backend"]
    end

    %% Connections
    Gateways --> Agent
    Agent <--> LLM
    Agent <--> Tools
    Agent <--> Specialized
    Agent <--> Swarm

    Tools --> Backends
    Tools --> Knowledge
    Tools --> Persistence

    Specialized --> Persistence
    Specialized --> Knowledge

    Swarm --> Specialized
    Swarm --> Persistence
```

## 🛠️ Functional Logic Flows

### 1. User Query Processing (`Agent.think`)

```mermaid
sequenceDiagram
    participant U as User
    participant G as Gateway
    participant A as Agent
    participant M as Memory
    participant K as Knowledge
    participant L as LLM
    participant T as Tools

    U->>G: Send Message
    G->>A: think(text, user_id)
    A->>M: get_history(user_id)
    M-->>A: Recent messages
    A->>K: search_knowledge(query)
    K-->>A: Relevant context
    A->>L: chat(context + history)

    alt Needs Tools
        L-->>A: tool_calls
        loop each tool
            A->>T: execute_tool(name, args)
            T-->>A: result
        end
        A->>L: chat(tool_results)
    end

    L-->>A: final_response
    A->>M: add_message(final_response)
    A-->>G: response
    G-->>U: message
```

### 2. Specialized Medic Self-Healing Loop

```mermaid
flowchart TD
    Start["Startup / Interval"] --> Scan["Medic: scan_system()"]
    Scan --> Check["Verify Integrity (Hashes)"]
    Check -- Issues Found --> Recover["Recover from GitHub/Backup"]

    Start --> Logs["LogAnalyzer: analyze_logs()"]
    Logs --> Evolver["EvolverEngine: Detect Patterns"]
    Evolver --> Scorer["Calculate Health Score"]
    Scorer -- Low Score --> Plan["Generate Evolution Plan"]
    Plan --> Change["ChangeMgmt: Propose Update"]
    Change -- Approved --> Apply["Apply Modification"]
    Apply --> Verify["Verify Syntax/Stability"]
```

### 3. Agent Swarm Execution

```mermaid
flowchart LR
    Task["Swarm Task"] --> Orchestrator

    subgraph Strategy ["Strategy Selection"]
        Orchestrator --> Parallel
        Orchestrator --> Sequential
        Orchestrator --> Hierarchical
    end

    subgraph Execution ["Worker Pool"]
        Parallel --> W1["Worker 1"]
        Parallel --> W2["Worker 2"]
        Hierarchical --> Coord["Coordinator"]
        Coord --> W3["Worker 3"]
    end

    W1 & W2 & W3 --> Aggregator["Aggregation Engine"]
    Aggregator --> Result["Final Synthesis"]
```

## 📂 Module & Function Registry

| Module | Primary Class | Key Functions |
|--------|---------------|---------------|
| `myclaw.agent` | `Agent` | `think()`, `chat()`, `_run_tool_calls()` |
| `myclaw.memory` | `Memory` | `get_history()`, `add_message()`, `search_history()` |
| `myclaw.provider` | `LLMProvider` | `chat()`, `chat_stream()`, `count_tokens()` |
| `myclaw.state_store` | `StateStore` | `get()`, `set()`, `delete()`, `increment()` |
| `myclaw.async_scheduler` | `AsyncScheduler` | `add_job()`, `remove_job()`, `run_forever()` |
| `myclaw.knowledge.db` | `KnowledgeDB` | `search_notes()`, `write_note()`, `get_relations()` |
| `myclaw.swarm.orchestrator`| `SwarmOrchestrator`| `create_swarm()`, `execute_task()`, `terminate()` |
| `myclaw.agents.medic_agent` | `MedicAgent` | `check_health()`, `verify_integrity()`, `recover_file()` |
| `myclaw.backends.discover` | `N/A` | `discover_backends()`, `get_default_backend()` |

---
*Last Updated: 2026-04-21*
*Generated by: ZenSynora Architecture Review*
