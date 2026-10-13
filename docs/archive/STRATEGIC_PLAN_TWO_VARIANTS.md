# Strategic Plan: ZenSynora Transformation

> **Archived on 2026-10-13 (v0.7.0).** Strategic plan (personal vs. enterprise variants). Kept for history; it describes the project at the time it was written and may not match the current code. Current documentation: [docs/README.md](../README.md).

## Variant A: Federated Personal Intelligence

Focus on local-first, privacy-preserving personal AI.

```mermaid
flowchart TB
    User["User (Master)"]

    subgraph LocalNodes ["Local Infrastructure"]
        Ollama["Ollama / Local LLM"]
        SQLite["SQLite / Vector Memory"]
    end

    subgraph Gateways ["Private Access"]
        TG["Telegram (Bot API)"]
        CLI["Local Console"]
    end

    User <--> Gateways <--> MyClaw["ZenSynora Core"]
    MyClaw <--> LocalNodes
```

## Variant B: Enterprise Agent Swarm

Focus on high-scale, multi-user, multi-agent collaboration.

```mermaid
flowchart TD
    subgraph Cloud ["ZenSynora Cloud"]
        API["FastAPI / WebUI"]
        Bus["Redis Message Bus"]
        Workers["Scaled Worker Pool"]
    end

    subgraph Persistence ["Distributed Data"]
        PG["PostgreSQL (Memory)"]
        Redis["Redis (State)"]
        S3["Object Storage (Files)"]
    end

    User1["User A"] & User2["User B"] --> API
    API --> Bus --> Workers
    Workers <--> Persistence
```
