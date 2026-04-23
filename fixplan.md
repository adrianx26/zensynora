# ZenSynora Comprehensive Fixplan

## Document Metadata
- **Project**: ZenSynora (MyClaw) v0.4.1
- **Scope**: Full codebase audit and correction roadmap
- **Files Analyzed**: 95 Python files, ~38,924 lines of code
- **Audit Date**: 2026-04-22
- **Branch**: main

---

## Executive Summary

### Project Overview
ZenSynora is an ambitious personal AI agent framework supporting 8+ LLM providers, multi-channel gateways, persistent SQLite memory, agent swarms, dynamic tool building, MCP protocol support, and a FastAPI WebUI.

### Current State Assessment
The project is in Beta (v0.4.1) with signs of rapid iterative development. While the architecture is sound, the codebase suffers from:
- **5 Critical bugs** causing crashes or security breaches
- **12 High-severity vulnerabilities** (OWASP Top 10 coverage)
- **8 Performance bottlenecks** blocking async event loops
- **15+ Code quality issues** from feature iteration debt
- **Test coverage gaps** with broken tests testing non-existent APIs

### Critical Findings (Priority Matrix)

| Priority | Count | Categories |
|----------|-------|------------|
| P0 (Fix Today) | 5 | Infinite recursion, command injection, broken connection pool, CORS/auth, AST bypass |
| P1 (Fix This Week) | 12 | Blocking sync calls, race conditions, SSRF, SSH MITM, rate limiter, error disclosure |
| P2 (Fix This Sprint) | 15 | Dependency bloat, cache issues, token counting, test fixes, type consistency |
| P3 (Backlog) | 20+ | Documentation drift, refactoring, optional feature hardening |

### High-Level Improvement Strategy
1. **Stabilization Phase** (Week 1): Fix P0 critical bugs
2. **Security Hardening** (Week 2): Fix P1 vulnerabilities
3. **Performance Optimization** (Week 3): Replace blocking sync calls, fix caches
4. **Quality and Testing** (Week 4): Fix broken tests, dependency cleanup
5. **Architecture Refinement** (Ongoing): Refactor globals, improve error handling

---

## Code Analysis Section

### Architecture Review

#### Identified Patterns
| Pattern | Implementation | Assessment |
|---------|---------------|------------|
| Provider Factory | _PROVIDER_MAP in provider.py | Excellent |
| Registry Pattern | _agent_registry, TOOLS dict | Good but global mutable state is risky |
| Plugin Hooks | _HOOKS dict in tools/core.py | Functional but no type safety |
| Singleton | get_state_store(), get_scheduler() | Thread-safety issues on some |
| Connection Pool | AsyncSQLitePool, HTTPClientPool | Intent good; implementation broken |
| Lazy Loading | Profile cache, embedding model | Well-implemented |
| Strategy Pattern | Swarm strategies | Clean implementation |

#### Identified Anti-Patterns
| Anti-Pattern | Location | Impact |
|--------------|----------|--------|
| Global Mutable State | tools/core.py (TOOLS, _HOOKS) | Race conditions, test isolation issues |
| Sync-in-Async | provider.py, backends/hardware.py | Blocks event loop; kills concurrency |
| Bare Exception Handling | 50+ locations | Hides real bugs |
| Circular Import Risk | tools/core.py <-> sandbox.py | Runtime import failures |
| God Class | Agent (1,665 lines) | Too many responsibilities |
| Duplicate Code | shell() and shell_async() | Security divergence |

### Dependency Mapping

#### Circular Dependencies Detected
`
tools/core.py -> worker_pool.py -> sandbox.py -> audit_log.py
tools/core.py -> state_store.py -> (lazy import back to tools/core)
provider.py -> semantic_cache.py -> metrics.py -> (potential back to provider)
`

**Mitigation**: Use lazy imports inside functions rather than module-level imports.

#### Dependency Bloat Analysis
| Dependency | Core? | Issue |
|------------|-------|-------|
| sentence-transformers | No | Pulls PyTorch (~2GB). Should be [semantic-cache] extra |
| vosk | No | Should be [voice] extra |
| speedtest-cli | No | Dead dependency |
| apscheduler | No | Replaced by async_scheduler.py. Unused |
| redis | No | Should be optional |
| prometheus-client | No | Should be [metrics] extra |
| cryptography | No | Should be [security] extra |
| keyring | No | Should be [security] extra |
| pyotp | No | Should be [mfa] extra |
| qrcode | No | Should be [mfa] extra |
| GPUtil | No | Unmaintained (2019). Use nvidia-ml-py |
| requests | No | httpx is primary HTTP client. Unused |

#### Version Constraint Issues
- python-telegram-bot[job-queue]==21.4 -- Exact pin misses security patches
- Most deps use >= only -- no upper bounds. Supply chain risk
- numpy >=1.24.0 -- No upper bound; v2.0 compatibility unknown

### Code Quality Metrics

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Files | 95 | -- | -- |
| Lines of Code | ~38,924 | -- | -- |
| Average file length | ~410 lines | <300 | Warning |
| Longest file | agent.py (1,665) | <500 | Critical |
| Type hint consistency | 60% | 90% | Critical |
| Docstring coverage | 40% | 80% | Critical |
| except Exception: count | 50+ | <10 | Critical |
| TODO/FIXME count | 12+ | 0 | Warning |

### Technical Debt Assessment
1. **Agent class decomposition** (High cost) -- Split into MessageRouter, ContextBuilder, ToolExecutor, ResponseHandler
2. **Global state refactoring** (High cost) -- Replace module-level globals with dependency injection
3. **Sync-to-async migration** (Medium cost) -- Replace sync OpenAI client, hardware probes
4. **Type hint standardization** (Low cost) -- Use modern dict, list, union syntax
5. **Exception hierarchy creation** (Low cost) -- Create ZenSynoraError base class

### Security Vulnerability Analysis

| # | Vulnerability | File | CVSS | Severity |
|---|---------------|------|------|----------|
| 1 | Infinite Recursion DoS | agent.py:583 | 5.3 | Medium |
| 2 | Command Injection (newline bypass) | tools/shell.py:65 | 8.1 | High |
| 3 | CORS Misconfiguration + Credentials | web/api.py:73 | 7.5 | High |
| 4 | Unauthenticated Admin Endpoints | web/api.py:108-201 | 7.5 | High |
| 5 | register_tool AST Bypass | tools/toolbox.py:99 | 8.8 | High |
| 6 | Sandbox Escape (wrapper ineffective) | sandbox.py:266 | 7.5 | High |
| 7 | SSRF (browse/download) | tools/web.py | 6.5 | Medium |
| 8 | SSH MITM (AutoAddPolicy) | backends/ssh.py:42 | 6.5 | Medium |
| 9 | Rate Limiter Race Condition | tools/core.py:75 | 5.3 | Medium |
| 10 | MFA Secret Exposure | web/api.py:154 | 6.5 | Medium |
| 11 | Error Message Disclosure | tools/shell.py:87 | 4.3 | Low |
| 12 | Audit Log Tamper Weakness | audit_log.py:87 | 4.0 | Low |
| 13 | Config Key Storage Weakness | config_encryption.py | 4.0 | Low |
| 14 | allowed_commands config drift | config.py:372 | 5.3 | Medium |
| 15 | _reveal_secrets non-functional | config.py:663 | 4.0 | Low |

### Performance Bottleneck Identification

| # | Bottleneck | File | Impact | Severity |
|---|------------|------|--------|----------|
| 1 | Sync OpenAI client blocks event loop | provider.py:651 | All concurrent requests freeze | Critical |
| 2 | Broken AsyncSQLitePool | memory.py:98 | DB lock errors under load | Critical |
| 3 | LRU cache key skips first arg | provider.py:121 | Cache collisions | High |
| 4 | Hardware probe blocks init | agent.py:286 | 100-500ms startup delay per agent | High |
| 5 | Unbounded _pending_preloads | agent.py:263 | Memory leak over time | High |
| 6 | Semantic cache O(n) scan | semantic_cache.py:270 | Latency spikes | Medium |
| 7 | FTS5 Cartesian product | knowledge/db.py | Query slowdown | Medium |
| 8 | Inaccurate token counting | context_window.py | Wrong truncation | Medium |
| 9 | HTTPClientPool event loop binding | provider.py:295 | Crashes on loop change | Medium |
| 10 | AsyncScheduler no concurrency limit | async_scheduler.py:290 | Thundering herd | Medium |

### Testing Coverage Gaps

| Component | Tests Exist? | Coverage Quality | Issue |
|-----------|-------------|------------------|-------|
| Agent.think() | Yes | Partial | Mocks provider directly; no E2E |
| Agent.stream_think() | **No** | -- | Zero tests |
| AsyncScheduler | **No** | -- | No startup/shutdown tests |
| AsyncSQLitePool | Yes | **Broken** | Tests fantasy API |
| WorkerPoolManager | Yes | Partial | No timeout tests |
| StateStore | **No** | -- | No Redis backend tests |
| HTTPClientPool | **No** | -- | No connection reuse tests |
| SecuritySandbox | Yes | Partial | No escape vector tests |
| RateLimiter | Yes | Partial | No concurrency tests |
| SemanticCache | Yes | Partial | No TTL expiration tests |

---

