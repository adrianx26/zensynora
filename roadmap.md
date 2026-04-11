# 🗺️ ZenSynora (MyClaw) – ROADMAP 2026-2027

**Version:** 0.4 (April 2026)  
**Author:** Adrian Petrescu (lead developer)  
**Status:** Active – in progress

This document describes the strategic direction of the **ZenSynora** (MyClaw) project for the next 6-12 months.  
The goal is to transform ZenSynora from the best **personal & privacy-first** AI agent into an **open-source killer app** that seriously competes with Deer-Flow, Agno, CrewAI, etc., without losing its simplicity and local-first focus.

---

## 🎯 General Objective
To become the **most complete, easy-to-use, and secure open-source personal AI agent** of 2026, featuring MCP support, a UI, a real sandbox, and a community ecosystem.

---

## 📋 Priority Roadmap

| Priority | Feature                              | Short Description                                                                | Why it matters (vs. competition)                            | Estimated Effort | Expected Impact     | Status          | Target       |
|----------|--------------------------------------|----------------------------------------------------------------------------------|-------------------------------------------------------------|------------------|---------------------|-----------------|--------------|
| **1**    | **Full MCP Support**                 | Model Context Protocol (server + client) + automatic tool discovery              | 2026 Standard (Deer-Flow has it natively)                   | Medium           | ★★★★★ (game-changer) | ✅ **Completed**| April 2026   |
| **2**    | **Web UI / Dashboard**               | Streamlit or FastAPI + React Lite (visual memory, swarms, jobs, knowledge base)  | Agno & Deer-Flow have UIs → users demand visibility         | Medium           | ★★★★                | ✅ **Completed**| June 2026    |
| **3**    | **Real Sandbox**                     | Docker / Firecracker / gVisor lite + per-user isolation                          | Just an allowlist is no longer enough (Deer-Flow container) | Medium-High      | ★★★★★ (security)    | **Planned**     | July 2026    |
| **4**    | **Hybrid Memory**                    | Current SQLite + Chroma/Qdrant (vector) + improved graph relations               | Semantic search + stronger long-term memory                 | Low-Medium       | ★★★★                | **Planned**     | August 2026  |
| **5**    | **Observability & Tracing**          | Langfuse / OpenTelemetry integration + detailed swarm logging                    | Everyone wants to see "why the agent decided that"          | Low              | ★★★★                | **Planned**     | Sept 2026    |
| **6**    | **Voice & Multi-modal**              | Whisper + local TTS + Vision (via Ollama) + hands-free                           | 2026 trend: voice and multi-modal agents                    | Medium           | ★★★                 | Planned         | Q4 2026      |
| **7**    | **Agent Hub + Marketplace**          | System for installing agents/community tools via CLI                             | Viral growth + community ecosystem                          | Low              | ★★★★ (viral)        | Planned         | Q4 2026      |
| **8**    | **Improved CLI + VS Code Ext.**      | Advanced commands + VS Code extension                                            | Developers want the agent right inside their editor         | Low              | ★★★                 | ✅ **Completed**| Q1 2027      |
| **9**    | **One-click Self-hosting**           | Complete Docker Compose + VPS script                                             | Users want a 24/7 solution that is easy to run              | Low              | ★★★★                | Planned         | Q1 2027      |
| **10**   | **Evaluation Framework**             | Automated task benchmarking + comparisons with other frameworks                  | Transparency and credibility                                | Medium           | ★★★                 | ✅ **Completed**| April 2026   |
| **11**   | **Secure Secret Manager**            | Encrypted local vault for API keys and sensitive tokens                          | Hardening our "Privacy-First" promise (no plain-text keys)  | Low-Medium       | ★★★★                | **Planned**     | May 2026     |
| **12**   | **Self-Directed Learning**           | Auto-filling Knowledge Gaps during idle time                                     | The agent proactively improves its own database             | Medium           | ★★★★                | ✅ **Completed**| April 2026   |
| **13**   | **Hardware Awareness**               | System hardware detection (CPU/GPU/RAM) for performance optimization             | Suggesting best models based on local hardware resources    | Low              | ★★★★                | **Planned**     | July 2026    |
| **14**   | **LLM Capability Library**           | Database of LLM benchmarks and technical limits (context window, tool support)   | Intelligent routing based on task requirements vs LLM power| Medium           | ★★★★                | ✅ **Completed**| April 2026   |
| **15**   | **SSH Remote Access**                | Secure execution of tools/commands on remote servers via SSH                     | Managing remote infra directly from the agent               | Medium           | ★★★★★               | **Planned**     | Aug 2026     |

---

## 🧩 Development Phases (Milestones)

### **Phase 1 – Core Upgrade (May – June 2026)**
- ✅ **Full MCP**
- ✅ **Simple Web UI (MVP)**
- **Release:** v0.4 "MCP Ready"

### **Phase 1.5 – Intelligence & Hardening (June 2026)**
- ✅ **Automatic Knowledge Gap Filling (Idle-time Research)**
- ✅ **LLM Capability & Benchmark Library (Intelligent Routing)**
- **Intelligent Routing Note:** This will only work if multiple models/providers are configured. If only one is available, it will default to that model regardless of task complexity.
- **Release:** v0.5 "Hardened"

### **Phase 2 – Security & Observability (July – Sept 2026)**
- Docker Sandbox
- Hardware Awareness (Auto-optimization for CPU/GPU)
- SSH Remote Connection Implementation
- Observability + Tracing (Langfuse Integration)
- AGPL-3.0 License + Dual Licensing preparation
- **Release:** v2v1.0.0 "Production Ready"

### **Phase 2.1 – Ecosystem & Experience (Oct 2026 – March 2027)**
- Voice / Multi-modal
- Agent Hub + Marketplace
- ✅ **CLI - Advanced commands**
- VS Code Extension
- Visual Swarm Debugger (Web UI Graph)
- Desktop App Wrapper (Tauri/Electron)
- One-click self-hosting
- Hybrid Memory
- Secure Secret Manager (Encrypted Key Vault)
- **Release:** v2.5.0 "Community Edition"

### **Phase 2.2 – Monetization & Enterprise (2027)**
- Paid commercial license
- Enterprise support (SLA, on-prem, etc.)
- Public evaluation suite

---

## 📌 Important Notes
- **License:** We will use **AGPL-3.0** (open-source) + **Dual Licensing** for commercial use.
- **User Priorities:** Privacy, installation simplicity, security, MCP.
- Any contribution is welcome! See `CONTRIBUTING.md`.
- The roadmap may change based on community feedback.

---

**Last updated:** April 11, 2026  
**Next review date:** May 1, 2026

---

If you want to add, modify, or remove anything from this roadmap, let me know and we will update the file immediately!
