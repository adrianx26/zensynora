# ZenSynora (MyClaw) 🧬

**License:** AGPL-3.0 (open-source) | **Dual Licensing** available for enterprise.
Copyright © 2026 Adrian Petrescu. All rights reserved.

---

### 🛡️ Your Personal AI Agent, Everywhere.
A high-performance, privacy-first AI agent that runs locally or in the cloud. Seamlessly integrates with **Telegram**, **WhatsApp**, and the **Web**, featuring persistent memory, multi-agent swarms, and a dynamic tool-building ecosystem.

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![CI](https://github.com/adrianx26/zensynora/actions/workflows/ci.yml/badge.svg)](https://github.com/adrianx26/zensynora/actions/workflows/ci.yml)
[![Docker](https://img.shields.io/badge/docker-ready-2496ED.svg)](#option-2--docker-recommended-for-production)
[![Tests](https://img.shields.io/badge/tests-pytest-blue.svg)](CONTRIBUTING.md#testing)

> **"ZenSynora doesn't just execute tasks; it evolves with you, refining its internal models of your projects and its own capabilities."**

---

## ✨ Key Features

### 🧠 Core Intelligence
- **LLM Agnostic** — Native support for [Ollama](https://github.com/ollama/ollama), OpenAI, Anthropic, Gemini, Groq, and OpenRouter.
- **Persistent Memory** — SQLite-backed conversation history with per-user isolation and semantic retrieval.
- **Intelligent Routing** — Automatically upgrades to premium models for complex reasoning while using local models for simple tasks.

### 🛠️ Advanced Ecosystem
- **Natively Integrated Web UI** — Beautiful glassmorphism dashboard with real-time FastAPI WebSockets.
- **🐝 Agent Swarms** — Coordinate multiple agents using Parallel, Sequential, Hierarchical, or Voting strategies.
- **Full MCP Support** — Operates as both an MCP Client and Server, enabling compatibility with Cursor, Claude, and ClawHub.ai.
- **Dynamic Tool Building** — The agent can create, test, and register new Python tools at runtime in its secure **TOOLBOX**.

### 🏥 System Resilience
- **Medic Agent** — Self-healing system health monitoring, integrity verification, and change management with approval workflows.
- **Hardware Awareness** — Deep telemetry (CPU/GPU/NPU) with intelligence-driven optimization suggestions.

---

## 🏗️ Architecture

ZenSynora is built for modularity and performance, featuring a multi-layered optimization engine and a secure tool execution pipeline.

```mermaid
flowchart TB
    %% Styling
    classDef core fill:#2a507a,stroke:#4477aa,stroke-width:2px,color:#fff
    classDef channel fill:#1a4d2e,stroke:#2d7a4a,stroke-width:2px,color:#fff
    classDef data fill:#6a3a14,stroke:#9c5822,stroke-width:2px,color:#fff
    classDef llm fill:#4a1e50,stroke:#863990,stroke-width:2px,color:#fff
    classDef core fill:#2a507a,stroke:#4477aa,stroke-width:2px,color:#fff
    classDef intel fill:#7a5a2a,stroke:#aa7744,stroke-width:2px,color:#fff
    classDef infra fill:#3a3a3a,stroke:#666666,stroke-width:2px,color:#fff
    classDef cache fill:#8a6a12,stroke:#c9a227,stroke-width:2px,color:#fff

    %% Channels
    subgraph Interfaces [External Interfaces]
        direction LR
        CLI(["🖥️ CLI"])
        TG(["📱 Telegram"])
        WA(["💬 WhatsApp"])
        WebUI(["🌐 Web UI"])
        MCP(["🔌 MCP Hub"])
    end

    %% Core Application
    subgraph Platform [ZenSynora Platform]
        GW{"Gateway Router"}

        subgraph CoreAgent [🧠 Core Agent & Optimization]
            Agent("Agent Engine")
            Router("🛤️ Intelligent Router")
            ProfileCache("📋 Profile Cache")
        end

        subgraph Capabilities [Capabilities]
            Tools["🛠️ Dynamic Tools"]
            Sched("⏱️ Task Scheduler")
            Swarm("🐝 Swarm Orchestrator")
        end

        subgraph Intel [Intelligence Platform]
            GapRes("🔍 Gap Researcher")
            Bench("📊 Benchmark Runner")
            Hardware("💻 Hardware Probe")
        end
    end

    %% Caching Layer
    subgraph Caching [Caching & Performance]
        Semantic("🔮 Semantic Cache")
        LRU("📦 LRU Cache")
        ConfigC("🔧 Config Cache")
    end

    %% Data Layer
    subgraph Storage [Persistent Storage]
        direction LR
        Mem[("💾 Memory")]
        KB[("📚 Knowledge Base")]
        Toolbox[("🔧 ToolBox")]
        Jobs[("📋 Scheduled Jobs")]
    end

    %% LLM Providers
    subgraph Providers [AI Providers]
        direction LR
        Local("💻 Local Models")
        Cloud("☁️ Cloud APIs")
    end

    %% Connections
    Interfaces ==> GW
    GW ==> Agent
    Agent <--> Capabilities
    Agent <--> Intel
    Agent -.-> Caching

    Capabilities <--> Storage
    Intel <--> Storage

    Agent ==> Providers
    Intel ==> Providers

    %% Apply Classes
    class CLI,TG,WA,WebUI,MCP channel
    class GW,Agent,Router,ProfileCache,Tools,Sched,Swarm core
    class Mem,KB,Toolbox,Jobs data
    class Local,Cloud llm
    class GapRes,Bench,Hardware intel
    class Semantic,LRU,ConfigC cache
```

---

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- [Ollama](https://github.com/ollama/ollama) (for local models) or API keys for Cloud Providers.

### Option 1 — Easy Install (Recommended)

```bash
git clone https://github.com/adrianx26/zensynora.git
cd zensynora

# Install core with all providers and features
pip install -e ".[all]"

# Run onboarding wizard
zensynora onboard
```

### Option 2 — Docker (Production Ready)

```bash
cp .env.example .env
# Edit .env with your keys
docker compose up -d --build
```

---

## 🛠️ Usage & Commands

| Mode | Command | Description |
|------|---------|-------------|
| 🖥️ **Console** | `zensynora agent` | Interactive CLI chat |
| 📱 **Gateway** | `zensynora gateway` | Start Telegram/WhatsApp bots |
| 🌐 **Web UI** | `zensynora webui` | Launch the browser dashboard |
| 🛠️ **Onboard** | `zensynora onboard` | Initial configuration wizard |
| 📊 **Benchmark** | `zensynora benchmark` | Test model latency and accuracy |

### 🧩 Available Tools
- **Filesystem**: `read_file`, `write_file`, `download_file`
- **System**: `shell` (securely sandboxed), `hardware`
- **Knowledge**: `search_knowledge`, `write_to_knowledge`, `sync_knowledge_base`
- **Collaboration**: `delegate`, `swarm_create`, `swarm_assign`
- **Automation**: `schedule`, `list_schedules`, `cancel_schedule`

---

## 🧠 Advanced Capabilities

### 📚 Knowledge Base
ZenSynora utilizes a hybrid Markdown/SQLite storage system.
- **Search**: FTS5-powered full-text search across all notes.
- **Graph**: Automatic relation mapping between entities.
- **Research**: Background "Gap Researcher" fills information gaps using the Scrapling engine.

### 🐝 Agent Swarms
Enable complex collaboration between specialized agents.
- **Sequential**: Pipeline workflows (e.g., Draft -> Edit -> Publish).
- **Parallel**: Multi-perspective brainstorming.
- **Voting**: Consensus-driven decision making.

---

## 🔒 Security & Safety
Security is not an afterthought; it's the foundation of ZenSynora.
- **Command Sandboxing** — Strict allowlist/blocklist for shell execution.
- **Path Validation** — Prevents directory traversal attacks.
- **SSRF Protection** — Blocks access to private IP ranges in web tools.
- **Audit Logs** — Tamper-evident HMAC-SHA256 signed entries for every action.
- **MFA & Auth** — TOTP Multi-factor authentication for admin endpoints.

---

## 🗺️ Roadmap

- [x] **v0.4.1** — Security hardening, Async migration, and Performance overhaul.
- [ ] **Phase 8** — Plugin system, streaming tool execution, and webhook mode.
- [ ] **Phase 9** — Discord & Slack integration, Enterprise role-based access control.

---

## 🤝 Contributing
Contributions are what make the open-source community an amazing place to learn, inspire, and create.
1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📜 License
Distributed under the **AGPL-3.0 License**. See `LICENSE` for more information. Dual-licensing is available for commercial use.

---

**Developed by Adrian Petrescu.**
[GitHub](https://github.com/adrianx26) | [Website](https://zensynora.com) *(Coming Soon)*
