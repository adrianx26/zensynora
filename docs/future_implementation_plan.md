# ZenSynora Future Implementation Plan

## Overview

Based on code review, this document outlines future improvements and missing features from the original requirements.

---

## Future Phase 1: Agent Integration & Automation

### 1.1 Integrate Agents into Agent.think() Pipeline

**Current State:** Agents are standalone tools but not automatically invoked during agent execution.

**Implementation:**
- Add automatic health check on agent startup
- Integrate loop prevention into agent execution
- Auto-run newtech scan on scheduled intervals

**Files to Modify:**
- `myclaw/agent.py` - Add agent lifecycle hooks for medic

### 1.2 Config Integration for Agents

**Current State:** Agents have defaults but no explicit config sections.

**Implementation:**
Add to `config.py`:
```python
class MedicConfig(BaseModel):
    enabled: bool = True
    enable_hash_check: bool = True
    repo_url: str = ""
    scan_on_startup: bool = False
    max_loop_iterations: int = 100
    backup_dir: str = ""

class NewTechConfig(BaseModel):
    enabled: bool = False
    interval_hours: int = 24
    share_consent: bool = False
    github_repo: str = ""
    max_news_items: int = 10

class AppConfig(BaseModel):
    # ... existing fields
    medic: MedicConfig = MedicConfig()
    newtech: NewTechConfig = NewTechConfig()
```

---

## Future Phase 2: Medic Agent Enhancements

### 2.1 Local Backup Recovery

**Current State:** Only GitHub recovery implemented.

**Implementation:**
- Implement local backup folder (`~/.myclaw/medic/backup/`)
- Add `recover_file(source="local")` option

**Files to Modify:**
- `myclaw/agents/medic_agent.py`

### 2.2 VirusTotal Integration

**Original Requirement:** Check files on Virustotal or with anti-malware engine.

**Implementation:**
```python
def check_file_virustotal(file_path: str, api_key: str) -> dict:
    """Check file hash against VirusTotal API."""
    # Calculate hash
    # Query VT API
    # Return detection ratio
```

### 2.3 Proactive Latency Optimization

**Original Requirement:** Perform proactive operations to reduce latency.

**Implementation:**
- Pre-load frequently used skills
- Cache LLM responses for similar queries
- Pre-warm connection pools

---

## Future Phase 3: New Tech Agent Enhancements

### 3.1 Real GitHub Integration

**Current State:** Placeholder implementation.

**Implementation:**
```python
async def _create_gist(self, title: str, content: str) -> Dict:
    """Create actual GitHub Gist."""
    # Use GitHub API with token
```

### 3.2 Additional News Sources

**Implementation:**
- Add more AI news sources (OpenAI Blog, DeepAI, etc.)
- Add RSS feed parsing
- Add newsletter integration

---

## Future Phase 4: Testing & Documentation

### 4.1 Unit Tests

**Files to Create:**
- `tests/test_skill_adapter.py`
- `tests/test_medic_agent.py`
- `tests/test_newtech_agent.py`
- `tests/test_backends.py`

### 4.2 Integration Tests

**Files to Create:**
- `tests/test_agent_integration.py`
- `tests/test_swarm_integration.py`

---

## Future Phase 5: Performance Optimization

### 5.1 Caching Layer

**Implementation:**
- Add Redis-like caching for LLM responses
- Implement skill result caching
- Add memory-mapped file optimization

### 5.2 Parallel Processing

**Implementation:**
- Parallel tool execution where possible
- Async optimization for I/O-bound tasks
- Worker pool management

---

## Future Phase 6: Security Enhancements

### 6.1 Skill Sandboxing

**Implementation:**
- Run skills in isolated containers
- Add resource limits (CPU, memory)
- Implement skill permissions system

### 6.2 Audit Logging

**Implementation:**
- Log all skill executions
- Add tamper-evident logging
- Implement log rotation

---

## Priority Order

| Priority | Feature | Impact |
|----------|---------|--------|
| HIGH | Config integration | Required for production use |
| HIGH | Agent pipeline integration | Core functionality |
| MEDIUM | Local backup recovery | Reliability |
| MEDIUM | Real GitHub integration | User value |
| LOW | VirusTotal integration | Security |
| LOW | Testing | Quality assurance |

---

## Notes

- All LSP errors in existing files should be fixed
- Consider adding type hints throughout
- Performance testing should be done before Phase 5

---

*Generated: 2026-03-29*
*Part of: ZenSynora Future Planning*
