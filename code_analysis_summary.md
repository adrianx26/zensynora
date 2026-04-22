# ZenSynora (MyClaw) Code Analysis & Improvement Plan

This document summarizes the analysis of the ZenSynora codebase and outlines a roadmap for future improvements, additions, and optimizations.

## 🔍 Codebase Overview

ZenSynora is a sophisticated multi-agent AI platform designed for both local and cloud-based LLM orchestration. Key architectural strengths include:

- **Unified Provider Abstraction**: Supports Ollama, OpenAI, Anthropic, Gemini, Groq, and more.
- **Persistent Knowledge System**: Markdown-based notes with SQLite FTS5 indexing.
- **Agent Swarms**: Multi-agent coordination with various strategies (Parallel, Sequential, Hierarchical).
- **Security-First Tooling**: Sandboxed shell execution, path validation, and tamper-evident audit logs.
- **Hardware Awareness**: Deep telemetry-driven optimization for local model execution.

---

## 🚀 Proposed Improvements & Additions

### 1. Enhanced Intelligence & Reasoning
- **LLM-Based Knowledge Extraction**: Replace or supplement the current regex-based extraction in `myclaw/agent.py` with a dedicated, lightweight LLM (e.g., Llama 3.2 1B) to filter and structure facts more accurately.
- **Semantic Context Windows**: Implement a sliding-window summarization technique that prioritizes entities and facts stored in the Knowledge Base over simple recency.
- **Embedding-Based Intent Routing**: Upgrade the `IntelligentRouter` to use vector embeddings for classifying user intent, providing more precise model selection than keyword-based regex.

### 2. Architecture & Scalability
- **Full Redis StateStore Integration**: Complete the implementation of the Redis-backed `StateStore` in `myclaw/state_store.py` to support multi-worker environments with synchronized rate limits and agent registries.
- **Asynchronous Tool Streams**: Enable tools (like `web_browse`) to stream results back to the agent or user incrementally, improving perceived latency for long-running tasks.
- **Distributed Knowledge Sync**: Implement a pub/sub mechanism to notify all running instances when the Knowledge Base is updated externally.

### 3. User Experience (Web UI)
- **Real-Time Telemetry Dashboard**: Create a visual dashboard using the telemetry data from `backends/hardware.py` to show CPU/GPU/RAM usage and LLM performance metrics.
- **Visual Swarm Composer**: A drag-and-drop interface in the Web UI to build agent swarms, assign roles, and configure execution parameters.
- **Audit Log Explorer**: A dedicated, secure view for searching and verifying tamper-evident audit logs.

### 4. Security & Governance
- **Role-Based Access Control (RBAC)**: Implement granular permissions (Admin, User, Auditor) for tool access and system configuration.
- **Hardened Sandbox**: Add resource quotas (CPU, Memory, Network) to the `SecuritySandbox` for custom Python skills.
- **Secret Management Integration**: Support external secret stores (like HashiCorp Vault or AWS Secrets Manager) for API keys and credentials.

### 5. New Specialized Features
- **Multi-Modal Vision Support**: Expand `multimodal.py` to support image-to-text analysis using vision-capable local models.
- **Voice Gateway 2.0**: Enhance `voice_channel.py` with real-time speech-to-speech capabilities using low-latency STT/TTS providers.
- **Autonomous Self-Healer**: A specialized agent that monitors system logs and proactively attempts to resolve connectivity or configuration errors.

---

## 🛠️ Implementation Priority

| Priority | Feature | Target Module |
| :--- | :--- | :--- |
| **P0** | Redis StateStore Completion | `myclaw/state_store.py` |
| **P0** | LLM-Based KB Extraction | `myclaw/agent.py` |
| **P1** | Telemetry Dashboard | `webui/src/` |
| **P1** | RBAC Permissions | `myclaw/tools/core.py` |
| **P2** | Multi-Modal Vision | `myclaw/multimodal.py` |

---

## 📄 Documentation Gaps
- **Contributor Guide**: Expand `CONTRIBUTING.md` with a detailed guide on adding new providers and coordination strategies.
- **Skill SDK**: Create a formal SDK documentation for users to develop and share custom `TOOLBOX` skills.
- **Deployment Guide**: Add automated deployment scripts for Kubernetes and Cloud Run.
