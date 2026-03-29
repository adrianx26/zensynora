# ZenSynora Implementation Plan: New Agents & Features

## Overview

This plan addresses the following requirements:
1. **Skill Compatibility Agent** - Analyze and replicate skills from external standards (agentskills.io)
2. **Medic Agent** - System health monitoring, validation, error recovery, hash integrity checking
3. **New Tech Agent** - AI news monitoring and technology proposals
4. **Cross-platform Execution** - Terminal backends (local, Docker, SSH, WSL2)

---

## Phase 1: Skill Compatibility Agent ✅ COMPLETE

**Status:** Fully implemented in `myclaw/agents/skill_adapter.py`

**Implementation Summary:**
- `myclaw/agents/__init__.py` - Package initialized with SkillAdapter export
- `myclaw/agents/skill_adapter.py` - Full SkillAdapter class with all methods
- Tools registered in `tools.py` lines 2792-2795
- Documentation: `docs/skill_adapter_guide.md`
- Architecture diagram updated with Skill Adapter component

---

## Phase 2: Medic Agent ✅ COMPLETE

**Status:** Fully implemented in `myclaw/agents/medic_agent.py`

**Implementation Summary:**
- `myclaw/agents/medic_agent.py` - Full MedicAgent class with all methods
  - `calculate_hash()` - SHA-256 hash calculation
  - `check_integrity()` - Compare hashes with baseline
  - `scan_system()` - Scan and record baseline hashes
  - `detect_errors()` - AST syntax error detection
  - `validate_modification()` - Pre-execution validation with AST
  - `fetch_from_github()` - GitHub file fetching
  - `recover_from_github()` - File recovery
  - `record_task()` - Task execution logging
  - `get_task_analytics()` - Analytics retrieval
  - `detect_loop()` - Infinite loop detection
  - `check_execution()` - Execution guard
  - `handle_timeout()` - Timeout handling
  - `get_health_report()` - Formatted health report
- `myclaw/agents/__init__.py` - Updated with MedicAgent export
- Tools registered in `tools.py` lines 2797-2808
- Documentation: `docs/medic_agent_guide.md`

---

## Phase 3: New Tech Agent

| # | Task | File | Details |
|---|------|------|---------|
| 2.1.1 | Create medic agent module | `myclaw/agents/medic_agent.py` | New module with MedicAgent class |
| 2.1.2 | Implement hash checker |medic_agent.py:calculate_hash()| SHA-256 hash calculation |
| 2.1.3 | Implement integrity registry |medic_agent.py:check_integrity()| Compare current vs recorded hashes |
| 2.1.4 | Implement health scanner |medic_agent.py:scan_system()| Scan all system files |
| 2.1.5 | Implement error detector |medic_agent.py:detect_errors()| Syntax/runtime error detection |
| 2.1.6 | Implement validaton logic |medic_agent.py:validate_modification()| Pre-execution validation |

### 2.2 GitHub Integration

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 2.2.1 | Implement GitHub fetcher |medic_agent.py:fetch_from_github()| Get file from GitHub raw URL |
| 2.2.2 | Implement recovery logic |medic_agent.py:recover_from_github()| Replace corrupted/missing file |
| 2.2.3 | Add GitHub API config | config.py | Add GitHub token, repo config |

### 2.3 LLM Evaluation & Documentation

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 2.3.1 | Implement task tracker |medic_agent.py:record_task()| Record task execution times |
| 2.3.2 | Implement proposal generator |medic_agent.py:generate_proposal()| Propose system improvements |
| 2.3.3 | Implement analyzer agent |medic_agent.py:analyze_with_llm()| Use secondary LLM for analysis |
| 2.3.4 | Implement documentation log |medic_agent.py:log_documentation()| Write to knowledge base |

### 2.4 Infinite Loop Prevention

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 2.4.1 | Implement loop detector |medic_agent.py:detect_loop()| Detect repeated patterns |
| 2.4.2 | Implement execution guard |medic_agent.py:check_execution()| Max iterations check |
| 2.4.3 | Implement timeout handler |medic_agent.py:handle_timeout()| Execution timeout handling |

### 2.5 Hash Check Configuration

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 2.5.1 | Add hash config section | config.py | Add ENABLE_HASH_CHECK setting |
| 2.5.2 | Implement config toggle |medic_agent.py:is_hash_check_enabled()| Check config on startup |

**New Tools:**
```python
def check_system_health() -> str
def verify_file_integrity(file_path: str = None) -> str
def recover_file(file_path: str, source: str = "github") -> str
def get_health_report() -> str
def validate_modification(proposed_change: str) -> str
def record_task_execution(task_name: str, duration: float) -> str
def get_task_analytics() -> str
def enable_hash_check(enabled: bool = True) -> str
def prevent_infinite_loop() -> str  # Returns status
```

---

## Phase 3: New Tech Agent ✅ COMPLETE

**Status:** Fully implemented in `myclaw/agents/newtech_agent.py`

**Implementation Summary:**
- `myclaw/agents/newtech_agent.py` - Full NewTechAgent class
- Tools registered in `tools.py` lines 2833-2842
- Documentation: `docs/newtech_agent_guide.md`

---

## Phase 4: Cross-Platform Backends ✅ COMPLETE

**Status:** Fully implemented in `myclaw/backends/`

**Implementation Summary:**
- `myclaw/backends/base.py` - AbstractBackend + BackendRegistry
- `myclaw/backends/local.py` - LocalBackend
- `myclaw/backends/docker.py` - DockerBackend
- `myclaw/backends/ssh.py` - SSHBackend
- `myclaw/backends/wsl2.py` - WSL2Backend
- `myclaw/backends/discover.py` - Backend discovery
- Documentation: `docs/backends_guide.md`

---

## Summary

All 4 phases are now complete:
- ✅ Phase 1: Skill Adapter
- ✅ Phase 2: Medic Agent
- ✅ Phase 3: New Tech Agent
- ✅ Phase 4: Cross-Platform Backends

**Functionality:**
- AI news monitoring
- Technology proposals
- Roadmap integration

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 3.1.1 | Create newtech agent module | `myclaw/agents/newtech_agent.py` | New module with NewTechAgent class |
| 3.1.2 | Implement news fetcher |newtech_agent.py:fetch_ai_news()| Fetch from AI news sources |
| 3.1.3 | Implement summarizer |newtech_agent.py:summarize_technology()| Create 10-row summaries |
| 3.1.4 | Implement proposal generator |newtech_agent.py:generate_proposal()| Generate implementation proposal |
| 3.1.5 | Implement roadmap updater |newtech_agent.py:add_to_roadmap()| Add to roadmap if approved |
| 3.1.6 | Implement share function |newtech_agent.py:share_to_github()| Share on GitHub (opt-in) |

### 3.2 User Consent System

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 3.2.1 | Add consent config | config.py | Add NEWTECH_ENABLED, SHARE_CONSENT |
| 3.2.2 | Implement consent checker |newtech_agent.py:check_consent()| Verify user permission |
| 3.2.3 | Implement manual trigger |newtech_agent.py:run_ondemand()| Allow manual execution |

**Configuration:**
```python
# config.py additions
NEWTECH_ENABLED = False  # User must opt-in
NEWTECH_INTERVAL_HOURS = 24
SHARE_CONSENT = False  # User must explicitly allow
GITHUB_REPO_FOR_SHARE = ""  # User specifies
```

**New Tools:**
```python
def fetch_ai_news(limit: int = 10) -> str
def get_technology_proposals() -> str
def add_to_roadmap(technology: str) -> str
def enable_newtech_agent(enabled: bool = True) -> str
```

---

## Phase 4: Cross-Platform Execution

### 4.1 Terminal Backend Abstraction

**Location:** `myclaw/backends/` (new package)

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 4.1.1 | Create backend base class | `myclaw/backends/base.py` | AbstractBackend base class |
| 4.1.2 | Implement local backend | `myclaw/backends/local.py` | Direct shell execution |
| 4.1.3 | Implement docker backend | `myclaw/backends/docker.py` | Docker container execution |
| 4.1.4 | Implement SSH backend | `myclaw/backends/ssh.py` | SSH connection execution |
| 4.1.5 | Implement WSL2 backend | `myclaw/backends/wsl2.py` | WSL2 interop execution |
| 4.1.6 | Update gateway integration | gateway.py | Add backend selection |

**Backend Interface:**
```python
class AbstractBackend:
    async def execute(self, command: str) -> tuple[str, int]
    async def upload(self, local_path: str, remote_path: str) -> bool
    async def download(self, remote_path: str, local_path: str) -> bool
    def get_type(self) -> str
    def is_available(self) -> bool
```

### 4.2 Backend Configuration

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 4.2.1 | Add backend config | config.py | Add BACKEND_TYPE, BACKEND_CONFIG |
| 4.2.2 | Add backend discovery |backends/discover.py| Auto-detect available backends |

**Configuration:**
```python
# config.py additions
DEFAULT_BACKEND = "local"  # local, docker, ssh, wsl2
BACKEND_CONFIG = {
    "docker": {"container": "zensynora"},
    "ssh": {"host": "", "user": "", "key_path": ""},
    "wsl2": {"distro": "Ubuntu"}
}
```

---

## Phase 5: Integration & Testing

### 5.1 Agent Integration with Main System

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 5.1.1 | Update agent initialization | `myclaw/agent.py` | Load new agents on startup |
| 5.1.2 | Add lifecycle hooks for medic | agent.py | Integrate with think() pipeline |
| 5.1.3 | Update tools registry | tools.py | Register all new tools |

### 5.2 Testing

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 5.2.1 | Create skill adapter tests | `tests/test_skill_adapter.py` | Unit tests |
| 5.2.2 | Create medic tests | `tests/test_medic_agent.py` | Unit tests |
| 5.2.3 | Create newtech tests | `tests/test_newtech_agent.py` | Unit tests |
| 5.2.4 | Create backend tests | `tests/test_backends.py` | Unit tests |
| 5.2.5 | Integration smoke tests | `tests/test_integration.py` | E2E tests |

### 5.3 Documentation

**Implementation Tasks:**

| # | Task | File | Details |
|---|------|------|---------|
| 5.3.1 | Update architecture diagram | docs/architecture_diagram.md | Add new components |
| 5.3.2 | Create agent guides | docs/medic_agent_guide.md | User documentation |
| 5.3.3 | Create backend guide | docs/backends_guide.md | Configuration guide |

---

## Priority Order & Dependencies

```
Phase 1 (Skill Adapter)
    │
    ├── 1.1.1-1.1.3 (Core adapter)
    └── 1.1.4-1.1.6 (Integration) → Phase 5

Phase 2 (Medic Agent)
    ├── 2.1.1-2.1.6 (Core) ← AFTER Phase 1.1.1
    ├── 2.2.1-2.2.3 (GitHub)
    ├── 2.3.1-2.3.4 (LLM + docs) → Phase 5
    ├── 2.4.1-2.4.3 (Loop prevention)
    └── 2.5.1-2.5.2 (Config) → Phase 5

Phase 3 (New Tech Agent)
    └── 3.1.1-3.2.3 (All) ← REQUIRES user consent

Phase 4 (Backends)
    ├── 4.1.1-4.1.3 (Core backends)
    ├── 4.1.4-4.1.6 (Extended backends)
    └── 4.2.1-4.2.2 (Config)

Phase 5 (Integration)
    └── All integration tasks
```

---

## Estimated Files to Create/Modify

**New Files:**
- `myclaw/agents/__init__.py`
- `myclaw/agents/skill_adapter.py`
- `myclaw/agents/medic_agent.py`
- `myclaw/agents/newtech_agent.py`
- `myclaw/backends/__init__.py`
- `myclaw/backends/base.py`
- `myclaw/backends/local.py`
- `myclaw/backends/docker.py`
- `myclaw/backends/ssh.py`
- `myclaw/backends/wsl2.py`
- `tests/test_*_agent.py` (4 files)
- `docs/medic_agent_guide.md`
- `docs/backends_guide.md`

**Modified Files:**
- `myclaw/tools.py` - Register new tools
- `myclaw/config.py` - Add new config options
- `myclaw/agent.py` - Agent initialization
- `myclaw/gateway.py` - Backend integration
- `docs/architecture_diagram.md` - Update diagram
- `docs/implementation_plan_new_agents.md` - This plan

---

## Clarifications Addressed

1. **GitHub Integration**: User-specified repo OR local fallback folder (`~/.myclaw/medic/backup/`)
2. **Secondary LLM**: Uses main agent provider if not explicitly configured otherwise
3. **New Tech Sharing**: Implemented but remains disabled until user activates
4. **Backends**: User-selected or default to local

---

## Phase 1: Skill Compatibility Agent

**Objective:** Implement a skill adapter that can analyze skills from external standards (agentskills.io) and replicate them for ZenSynora use.