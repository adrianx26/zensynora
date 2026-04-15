# ZenSynora – Implementation Gap Report
*Cross-referenced against `future_implementation_plan.md` and the live codebase.*

---

## ✅ Truly Implemented (confirmed in code)

| Feature | Where |
|---------|--------|
| `MedicConfig` + `NewTechConfig` in `AppConfig` | `config.py` L356 / L369 / L452-453 |
| `scan_on_startup` field exists in `MedicConfig` | `config.py` L361, `medic_agent.py` L51 |
| `recover_from_local()` + `create_local_backup()` | `medic_agent.py` L317, L617 |
| `check_file_virustotal()` | `medic_agent.py` L816, registered in `tools.py` L3396 |
| `_create_gist()` / `_create_issue()` (real GitHub API calls) | `newtech_agent.py` L263 / L309 |
| Loop prevention in `agent.think()` via `_depth > 10` | `agent.py` L936 |
| `prevent_infinite_loop()` call in `think()` | `agent.py` L944 |
| Proactive **Skill Preloader** (`skill_preloader.py`) | Fully implemented & wired into `think()` L969-974 |
| **Semantic Cache** (`semantic_cache.py`) | Exists as standalone module |
| Parallel tool execution | `agent.py` L1118-1158 |
| **Sandbox** (`sandbox.py`) with resource limits, code validation, audit log | Full implementation |
| **Audit logging** for tools (`ToolAuditLogger` in `tools.py` L171) | Implemented |
| Background Knowledge Researcher (idle-only scheduler) | `gateway.py` L107-113 |
| Connection pooling (HTTP + SQLite) | `provider.py` L252, `memory.py` L78 |
| Context summarization / trajectory compression | `agent.py` L976-1001 |

---

## ⚠️ Partially Implemented / Incomplete

### 1. `scan_on_startup` — Config exists, **never triggered in gateway**
- **Plan says:** "Already available – add to gateway startup."
- **Reality:** `scan_on_startup` is stored in config and read by `MedicAgent.__init__()`, but `gateway.start()` **never instantiates `MedicAgent`** and **never calls any scan** on startup.
- **Status:** Config field only. The actual health check on startup is **not wired up**.

### 2. GitHub Issue creation — **hardcoded `OWNER/REPO` placeholder**
- **Plan says:** Real GitHub integration implemented.
- **Reality:** `_create_gist()` is fully real, but `_create_issue()` (`newtech_agent.py` L326) posts to:
  ```python
  f"{GITHUB_API_URL}/repos/OWNER/REPO/issues"
  ```
  `OWNER` and `REPO` are literal strings — there is no config field to supply them.
- **Status:** Gist ✅ — Issue ❌ (will always fail with 404).

### 3. Proactive Latency Optimization — **Skill preloader wired, but NOT integrated as a startup warm-up**
- **Plan says:** Pre-load frequently used skills, cache LLM responses, pre-warm connection pools.
- **Reality:**
  - Skill preloader runs *reactively* inside `think()` (per-request background task). There is no startup phase that pre-loads a fixed set of common skills.
  - LLM response caching (`semantic_cache.py`) exists as a class but is **not injected or used** anywhere in `agent.py` or `provider.py`.
  - Connection pool pre-warming is not done — pools are lazy-initialized on first use.
- **Status:** ~30% done. The skeleton exists but is not fully wired.

### 4. Sandbox — **Not integrated into the skill execution path**
- **Plan says:** Run skills in isolated containers.
- **Reality:** `sandbox.py` is a complete, well-written module with `SecuritySandbox`, `SandboxedFunction`, resource limits, and audit log. However:
  - It is **never imported or used** in `tools.py`, `skill_generator.py`, or `agent.py`.
  - The `shell` / `shell_async` tools in `tools.py` run commands directly via `subprocess`, bypassing the sandbox entirely.
- **Status:** Module exists but is **dead code** — zero integration with the agent pipeline.

### 5. Audit Logging — **In-memory only, no tamper-evidence, no log rotation**
- **Plan says:** Tamper-evident logging + log rotation.
- **Reality:**
  - `ToolAuditLogger` stores entries in a Python list (in-memory, lost on restart).
  - `sandbox.py`'s `_audit_log` is also in-memory (capped at 1000 → drops to 500 on overflow).
  - `medic_change_mgmt.py` writes to `audit_log.json` — this is the only persistent audit, and it has no tamper-evidence (no hashing/signing) and no rotation.
- **Status:** Basic logging ✅ — tamper-evident ❌ — rotation ❌.

---

## ❌ Not Implemented At All

### Phase 3.2 — Additional News Sources
- **Plan says:** Add OpenAI Blog, DeepAI; RSS feed parsing; newsletter integration.
- **Reality:** `newtech_agent.py` has only 3 hardcoded sources (Hugging Face, AI News, TechCrunch). No RSS parser, no newsletter integration.

### Phase 5.1 — Redis-like LLM Response Caching
- **Plan says:** Add Redis-like caching for LLM responses.
- **Reality:** `semantic_cache.py` exists but is **never called** from `provider.py` or `agent.py`. LLM responses are not cached at all.

### Phase 5.2 — Worker Pool Management
- **Plan says:** Worker pool management.
- **Reality:** `ThreadPoolExecutor` is created in `gateway.py` but only for the event loop executor. There is no dedicated worker pool for tool execution or skill processing.

### Phase 4 — Unit & Integration Tests (incomplete)
- **Plan says:** Create `test_skill_adapter.py`, `test_medic_agent.py`, `test_newtech_agent.py`, `test_backends.py`, `test_agent_integration.py`, `test_swarm_integration.py`.
- **Reality:** All 6 files **exist** — but running them surfaces issues:
  - `test_medic_agent.py` and `test_newtech_agent.py` are minimal stubs (< 4 KB each) with few real assertions.
  - `test_agent_integration.py` does **not exist** (plan mentioned it; it's absent from `tests/`).
  - `test_swarm_integration.py` exists but is a stub with no actual swarm workflow tested.

---

## Summary Table

| Phase | Feature | Status |
|-------|---------|--------|
| 1.2 | Auto Health Check on Startup (`scan_on_startup`) | ⚠️ Config only – not triggered |
| 2.3 | Proactive Latency Optimization | ⚠️ Partially wired |
| 3.1 | GitHub Gist | ✅ Working |
| 3.1 | GitHub Issue | ❌ Hardcoded OWNER/REPO placeholder |
| 3.2 | Additional News Sources (RSS, newsletters) | ❌ Not implemented |
| 4.1 | Unit Tests (medic, newtech stubs) | ⚠️ Minimal / stubs only |
| 4.2 | Integration Tests (`test_agent_integration.py`) | ❌ File missing |
| 5.1 | LLM Response Caching (semantic_cache) | ❌ Dead code – not wired |
| 5.2 | Worker Pool Management | ❌ Not implemented |
| 6.1 | Skill Sandboxing (sandbox.py) | ⚠️ Module exists – not integrated |
| 6.2 | Tamper-evident Audit Logging | ❌ Not implemented |
| 6.2 | Log Rotation | ❌ Not implemented |
