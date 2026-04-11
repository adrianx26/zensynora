# 🗺️ ZenSynora (MyClaw) – ROADMAP 2026-2027

**Version:** 1.1 (April 2026)  
**Author:** Adrian (lead developer)  
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
| **10**   | **Evaluation Framework**             | Automated task benchmarking + comparisons with other frameworks                  | Transparency and credibility                                | Medium           | ★★★                 | Planned         | Q2 2027      |

---

## 🧩 Development Phases (Milestones)

### **Phase 1 – Core Upgrade (May – June 2026)**
- Full MCP
- Simple Web UI (MVP)
- **Release:** v1.5.0 "MCP Ready"

### **Phase 2 – Security & Observability (July – Sept 2026)**
- Docker Sandbox
- Observability + Tracing
- AGPL-3.0 License + Dual Licensing preparation
- **Release:** v2.0.0 "Production Ready"

### **Phase 3 – Ecosystem & Experience (Oct 2026 – March 2027)**
- Voice / Multi-modal
- Agent Hub + Marketplace
- CLI - Advanced commands 
- VS Code Extension
- One-click self-hosting
- Hybrid Memory
- **Release:** v2.5.0 "Community Edition"

### **Phase 4 – Monetization & Enterprise (2027)**
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
