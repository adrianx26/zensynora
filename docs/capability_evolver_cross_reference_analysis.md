# Comprehensive Analysis: capability-evolver x medic_agent Cross-Reference

**Date:** 2026-04-21  
**Analyst:** Technical Documentation & Architecture Review  
**Scope:** Architectural pattern extraction, gap analysis, and integration roadmap  

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [capability-evolver Architectural Deep Dive](#2-capability-evolver-architectural-deep-dive)
3. [medic_agent Current State Analysis](#3-medic_agent-current-state-analysis)
4. [Gap Analysis & Integration Points](#4-gap-analysis--integration-points)
5. [Architectural Recommendations](#5-architectural-recommendations)
6. [Implementation Strategies](#6-implementation-strategies)
7. [Concrete Code Examples](#7-concrete-code-examples)
8. [Prioritized Roadmap](#8-prioritized-roadmap)

---

## 1. Executive Summary

The **capability-evolver** project (github.com/kennyzir/capability-evolver) is a deterministic, pure-logic meta-skill for AI agent self-improvement. It analyzes structured runtime logs through a multi-pass analysis engine, computes health scores, detects patterns (errors, regressions, inefficiencies), and generates prioritized improvement proposals via configurable evolution strategies.

The **medic_agent** in ZenSynora is a system health monitoring and recovery agent with file integrity checking, error detection, change management, and basic log analysis capabilities.

### Key Finding
> **The medic_agent has solid foundational infrastructure but lacks the deterministic analysis engine, health scoring system, and structured evolution framework that capability-evolver demonstrates. Integrating these patterns would elevate medic_agent from a passive monitoring tool to an active self-improvement system.**

### Integration Value Matrix

| Capability | medic_agent | capability-evolver | Integration Priority |
|------------|-------------|-------------------|---------------------|
| File Integrity | ✅ SHA-256 hashes | ❌ | N/A |
| Syntax Validation | ✅ AST parsing | ❌ | N/A |
| Health Scoring | ❌ | ✅ 0-100 algorithm | **Critical** |
| Pattern Detection | ⚠️ Regex only | ✅ Multi-pass engine | **Critical** |
| Evolution Strategies | ❌ | ✅ 5 strategies | **High** |
| Error Cascades | ❌ | ✅ Time-window analysis | **High** |
| Regression Detection | ❌ | ✅ Statistical analysis | **Medium** |
| Inefficiency Detection | ❌ | ✅ Slow-op detection | **Medium** |
| Change Management | ✅ Full workflow | ❌ | N/A (extend) |
| Task Analytics | ✅ Basic stats | ❌ (consumes data) | **Medium** |

---

## 2. capability-evolver Architectural Deep Dive

### 2.1 Core Design Philosophy

```
┌─────────────────────────────────────────────────────────────────────┐
│              capability-evolver: Pure Logic Engine                   │
├─────────────────────────────────────────────────────────────────────┤
│  Design Principles:                                                  │
│  1. Deterministic — same logs always produce same results           │
│  2. Reproducible — no hallucination, audit-friendly                 │
│  3. Fast — sub-100ms processing, no external API calls              │
│  4. Structured — typed I/O, JSON-native                             │
│  5. Composable — action-based API (analyze → evolve → status)       │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Type System Architecture

capability-evolver uses a strongly-typed interface design that could be mapped to Python TypedDicts or dataclasses:

```typescript
// Core type hierarchy from handler.ts
LogEntry          → timestamp, level, message, context?, stack?
PatternEntry      → type, severity, description, occurrences, first_seen, last_seen, affected_files[]
AnalysisResult    → patterns[], health_score, recommendations[], summary{}
EvolutionProposal → evolution_id, strategy, recommendations[], risk_assessment{}, estimated_improvement
```

**Key Insight:** The type system enforces a clear data flow: `LogEntry[] → AnalysisResult → EvolutionProposal`. Each stage enriches the data without mutation side effects.

### 2.3 Multi-Pass Analysis Engine

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Analysis Pipeline (3 Passes)                       │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  Pass 1: Pattern Detection                                           │
│  ├── Error Aggregation (Map<message_prefix, {count, files, times}>) │
│  ├── Severity Classification (count ≥10=critical, ≥5=high, ≥2=med)  │
│  ├── Type Determination (count ≥3 → regression, else error)         │
│  └── Inefficiency Detection (info + regex /slow|timeout|\d{4,}ms/)  │
│                                                                       │
│  Pass 2: Health Scoring                                              │
│  ├── Error Rate = errors / total_logs × 100                         │
│  ├── Warn Penalty = warns / total_logs × 30                         │
│  └── Score = max(0, round(100 - error_penalty - warn_penalty))      │
│                                                                       │
│  Pass 3: Recommendation Generation                                   │
│  ├── Context-aware suggestions (critical → immediate fix)           │
│  ├── Strategy-specific recommendations                               │
│  └── Hot file identification (top 3 by occurrence count)            │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.4 Evolution Strategy Framework

```
┌─────────────────────────────────────────────────────────────────────┐
│              Strategy Selection Matrix                               │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│   Input Health Score    │  Auto-Selected Strategy                    │
│   ──────────────────────┼────────────────────────────────────────    │
│   < 40                  │  repair-only (critical fixes only)         │
│   40-70                 │  harden (reliability focus)                │
│   > 70                  │  balanced (reliability + features)         │
│                                                                      │
│   Manual Override:                                                   │
│   • balanced    — equal weight to reliability and features          │
│   • innovate    — prioritize new capabilities (health > 70)         │
│   • harden      — prioritize reliability and error reduction        │
│   • repair-only — fix critical issues only (crisis mode)            │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.5 Pattern Classification Taxonomy

| Type | Trigger | Severity Logic | Example |
|------|---------|---------------|---------|
| `error` | Single occurrence | count-based | One-off exception |
| `regression` | ≥3 occurrences | count-based | Repeated timeout |
| `inefficiency` | info + slow regex | frequency-based | Multiple slow DB queries |
| `drift` | (reserved) | N/A | Behavioral drift (future) |

### 2.6 Key Algorithms

#### Health Score Calculation
```typescript
// From handler.ts lines ~280-285
const healthScore = Math.max(0, Math.round(
  100 
  - (errorCount / Math.max(totalLogs, 1)) * 100 
  - (warnCount / Math.max(totalLogs, 1)) * 30
));
```

**Properties:**
- Bounded: [0, 100]
- Errors have 3.3× weight of warnings (100 vs 30 multiplier)
- Uses `Math.max(totalLogs, 1)` to prevent division by zero
- Rounded to integer for reproducibility

#### Pattern Severity Classification
```typescript
// From handler.ts lines ~265-270
const severity = data.count >= 10 ? 'critical' 
               : data.count >= 5 ? 'high' 
               : data.count >= 2 ? 'medium' 
               : 'low';
```

**Properties:**
- Logarithmic escalation (thresholds at 2, 5, 10)
- Deterministic — no ML or statistical variance
- Directly tied to business impact

---

## 3. medic_agent Current State Analysis

### 3.1 Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    medic_agent Architecture                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  medic_agent.py                                                      │
│  ├── MedicAgent (main class)                                        │
│  │   ├── File Integrity: calculate_hash(), check_integrity()        │
│  │   ├── Error Detection: detect_errors() (AST-based)               │
│  │   ├── Validation: validate_modification() (AST + forbidden list) │
│  │   ├── Recovery: fetch_from_github(), recover_from_*()            │
│  │   ├── Analytics: record_task(), get_task_analytics()             │
│  │   └── Loop Prevention: detect_loop(), check_execution()          │
│  ├── LoopDetector (class)                                           │
│  └── Tool Functions (convenience wrappers)                          │
│                                                                      │
│  medic_change_mgmt.py                                                │
│  ├── ChangeManagementSystem                                         │
│  │   ├── Change Plan CRUD                                           │
│  │   ├── Approval Workflow                                          │
│  │   ├── Execution with Rollback                                   │
│  │   └── Audit Logging                                              │
│  ├── LogAnalyzer                                                    │
│  │   ├── Regex-based anomaly detection                             │
│  │   ├── Trend detection (simple count aggregation)                 │
│  │   └── Trigger evaluation                                         │
│  └── ScheduledReviewSystem                                          │
│      └── Continuous monitoring loop                                 │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 3.2 Strengths

1. **Comprehensive file integrity system** — SHA-256 hashing with registry persistence
2. **Multi-source recovery** — GitHub (curl) and local backup recovery paths
3. **Full change management lifecycle** — plan → approve → execute → rollback → audit
4. **Security validation** — AST-based forbidden import/call detection
5. **Integration points** — Gateway startup hook, config injection
6. **VirusTotal integration** — External malware scanning

### 3.3 Current Limitations

1. **No health scoring** — Cannot quantify "how healthy" the system is
2. **Naive log analysis** — Simple regex matching without pattern aggregation or severity classification
3. **No pattern detection** — Cannot identify repeated errors, cascades, or regressions
4. **Static analytics** — Task analytics are basic counts; no trend analysis or failure pattern detection
5. **No evolution framework** — Change management creates plans but doesn't generate recommendations from analysis
6. **No severity system** — All issues treated equally
7. **No time-window analysis** — Cannot distinguish clustered vs. distributed errors
8. **No strategy system** — Change management has priorities but no strategic evolution direction

### 3.4 Code Quality Observations

**Positive:**
- Clean separation between `MedicAgent` and `ChangeManagementSystem`
- Proper use of `async/await` for I/O operations
- Type hints throughout
- Consistent error handling with logger integration

**Areas for Improvement:**
- Global `config` variable (line 34) creates tight coupling
- `LoopDetector.check()` (line 545-551) has a bug — increments are never recorded, always returns `count=0`
- `subprocess.run(['curl', ...])` is platform-dependent (Windows doesn't have curl by default)
- File I/O is synchronous and blocking
- No timeout handling for file operations
- Missing input validation on public methods

---

## 4. Gap Analysis & Integration Points

### 4.1 Missing: Deterministic Health Scoring

**Current State:** medic_agent can check integrity and detect errors but produces qualitative output ("valid: 8, modified: 0").

**Gap:** No unified quantitative health metric that combines file integrity, error rates, task success, and log anomalies into a single score.

**Integration Point:** Add a `calculate_health_score()` method that aggregates:
- File integrity ratio (valid / total)
- Syntax error count
- Task success rate
- Log anomaly rate
- Recent failure trend

### 4.2 Missing: Pattern Detection Engine

**Current State:** `LogAnalyzer` uses 13 regex patterns to flag anomalies line-by-line.

**Gap:** No aggregation of similar errors, no classification into types (regression vs. inefficiency), no severity scoring based on frequency.

**Integration Point:** Replace regex-only approach with capability-evolver's Map-based aggregation:
```python
error_map: Dict[str, {count, first_seen, last_seen, files}] = {}
# Aggregate by message prefix, then classify
```

### 4.3 Missing: Evolution Strategy Framework

**Current State:** `ChangeManagementSystem` has `ChangePriority` (critical/high/medium/low) and `ChangeType` (config/code/security/patch/hotfix).

**Gap:** No strategic direction for improvements. A system with health score 95 should receive different recommendations than one with score 35.

**Integration Point:** Add `EvolutionStrategy` enum and auto-selection logic:
```python
if health_score < 40: strategy = "repair-only"
elif health_score < 70: strategy = "harden"
else: strategy = "balanced"
```

### 4.4 Missing: Error Cascade Detection

**Current State:** No detection of dependency chain failures.

**Gap:** If `auth-service.py` fails and then `payment-api.py` fails within a time window, medic_agent doesn't connect these events.

**Integration Point:** Add time-window correlation analysis:
```python
# If module A errors followed by module B errors within 60s → cascade
cascades = detect_cascades(logs, time_window_seconds=60)
```

### 4.5 Missing: Structured Recommendation Generation

**Current State:** `LogAnalyzer.should_trigger_change()` returns a boolean + reason string.

**Gap:** No structured, actionable recommendations with priority, category, affected files, and suggested approach.

**Integration Point:** Adopt capability-evolver's recommendation structure:
```python
@dataclass
class Recommendation:
    priority: Literal["immediate", "high", "medium", "low"]
    category: Literal["error-handling", "performance", "stability", "architecture", "monitoring"]
    description: str
    affected_files: List[str]
    suggested_approach: str
```

### 4.6 Missing: Inefficiency Detection

**Current State:** No detection of performance issues from logs.

**Gap:** Slow operations, timeouts, and retries are invisible unless they produce errors.

**Integration Point:** Add regex-based slow-op detection to `LogAnalyzer`:
```python
slow_ops = logs.filter(
    level="info" and regex_match(r"(\d{4,})ms|slow|timeout", message)
)
```

---

## 5. Architectural Recommendations

### 5.1 Refactor: Introduce `EvolverEngine` Class

Create a new core class that encapsulates capability-evolver's analysis patterns:

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Proposed medic_agent v2.0                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐          │
│  │ EvolverEngine │───→│ HealthScorer │───→│ EvolutionPlanner│       │
│  └──────────────┘    └──────────────┘    └──────────────┘          │
│         │                   │                   │                    │
│         ▼                   ▼                   ▼                    │
│  ┌──────────────────────────────────────────────────────────┐      │
│  │              medic_agent (orchestrator)                   │      │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────┐  │      │
│  │  │ FileIntegrity│  │ LogAnalyzer │  │ ChangeManagement│  │      │
│  │  └─────────────┘  └─────────────┘  └─────────────────┘  │      │
│  └──────────────────────────────────────────────────────────┘      │
│                                                                      │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.2 Refactor: Decouple Config from Global State

**Current:**
```python
config = None  # Global

def set_config(cfg):
    global config
    config = cfg
```

**Recommended:**
```python
@dataclass
class MedicConfig:
    enabled: bool = True
    enable_hash_check: bool = True
    repo_url: str = DEFAULT_REPO_URL
    scan_on_startup: bool = False
    max_loop_iterations: int = 100
    evolution_strategy: str = "auto"
    health_threshold_critical: int = 40
    health_threshold_warning: int = 70
    maintenance_window_start: Optional[int] = None
    maintenance_window_end: Optional[int] = None
```

### 5.3 Refactor: Async File I/O

**Current:**
```python
# Blocking synchronous I/O
path.write_text(content, encoding="utf-8")
content = path.read_text(encoding="utf-8")
```

**Recommended:**
```python
import aiofiles

async def read_file_async(path: Path) -> str:
    async with aiofiles.open(path, 'r', encoding='utf-8') as f:
        return await f.read()

async def write_file_async(path: Path, content: str) -> None:
    async with aiofiles.open(path, 'w', encoding='utf-8') as f:
        await f.write(content)
```

### 5.4 Enhancement: Typed Data Models

Replace loose dict returns with structured dataclasses:

```python
from dataclasses import dataclass
from typing import Literal
from datetime import datetime

@dataclass(frozen=True)
class LogEntry:
    timestamp: datetime
    level: Literal["error", "warn", "info", "debug"]
    message: str
    context: str = ""
    stack: str = ""

@dataclass(frozen=True)
class Pattern:
    type: Literal["error", "regression", "inefficiency", "drift", "cascade"]
    severity: Literal["low", "medium", "high", "critical"]
    description: str
    occurrences: int
    first_seen: datetime
    last_seen: datetime
    affected_files: list[str]
    related_patterns: list[str] = None

@dataclass(frozen=True)
class HealthReport:
    score: int  # 0-100
    grade: Literal["A", "B", "C", "D", "F"]
    patterns: list[Pattern]
    recommendations: list[Recommendation]
    summary: dict
    trend: Literal["improving", "stable", "degrading"]
```

### 5.5 Enhancement: Plugin Architecture for Analyzers

Allow custom analyzers to be registered:

```python
class AnalyzerRegistry:
    def __init__(self):
        self._analyzers: list[Callable[[list[LogEntry]], list[Pattern]]] = []
    
    def register(self, analyzer: Callable) -> None:
        self._analyzers.append(analyzer)
    
    def analyze(self, logs: list[LogEntry]) -> list[Pattern]:
        patterns = []
        for analyzer in self._analyzers:
            patterns.extend(analyzer(logs))
        return patterns
```

---

## 6. Implementation Strategies

### 6.1 Phase 1: Foundation (Week 1-2) — Health Scoring & Pattern Detection

**Goal:** Add deterministic health scoring and pattern detection without breaking existing APIs.

**Approach:**
1. Create `myclaw/agents/medic_evolver.py` with `EvolverEngine` class
2. Keep existing `MedicAgent` unchanged but add composition:
   ```python
   class MedicAgent:
       def __init__(self, ...):
           # ... existing init ...
           self._evolver = EvolverEngine()
       
       def analyze_logs(self, logs: list[dict]) -> dict:
           """NEW: Capability-evolver style analysis"""
           return self._evolver.analyze(logs)
       
       def get_health_score(self) -> dict:
           """NEW: Unified health score"""
           integrity = self.check_integrity()
           tasks = self.get_task_analytics()
           return self._evolver.calculate_health_score(integrity, tasks)
   ```
3. Add backward-compatible wrapper functions

### 6.2 Phase 2: Integration (Week 3-4) — Connect to Change Management

**Goal:** Feed evolver analysis into change management system.

**Approach:**
1. Extend `ChangeManagementSystem` to accept `EvolutionProposal` as input
2. Auto-generate change plans from detected patterns
3. Map evolution strategies to change priorities:
   ```python
   STRATEGY_PRIORITY_MAP = {
       "repair-only": ChangePriority.CRITICAL,
       "harden": ChangePriority.HIGH,
       "balanced": ChangePriority.MEDIUM,
       "innovate": ChangePriority.LOW,
   }
   ```

### 6.3 Phase 3: Enhancement (Week 5-6) — Advanced Analytics

**Goal:** Add cascade detection, trend analysis, and predictive health.

**Approach:**
1. Implement time-window correlation for cascade detection
2. Add health score history tracking
3. Implement trend prediction (simple linear regression on health history)
4. Add inefficiency pattern detection

### 6.4 Phase 4: Optimization (Week 7-8) — Performance & Polish

**Goal:** Async I/O, caching, and production hardening.

**Approach:**
1. Migrate file I/O to async (aiofiles)
2. Add LRU cache for hash calculations
3. Implement chunked log processing for large files
4. Add configurable processing limits

---

## 7. Concrete Code Examples

### 7.1 EvolverEngine Implementation

```python
# myclaw/agents/medic_evolver.py
"""Deterministic analysis engine inspired by capability-evolver."""

import re
import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Any
from datetime import datetime
from collections import defaultdict


@dataclass(frozen=True)
class LogEntry:
    timestamp: str
    level: Literal["error", "warn", "info", "debug"]
    message: str
    context: str = ""
    stack: str = ""


@dataclass(frozen=True)
class Pattern:
    type: Literal["error", "regression", "inefficiency", "drift", "cascade"]
    severity: Literal["low", "medium", "high", "critical"]
    description: str
    occurrences: int
    first_seen: str
    last_seen: str
    affected_files: List[str]


@dataclass(frozen=True)
class Recommendation:
    priority: Literal["immediate", "high", "medium", "low"]
    category: Literal["error-handling", "performance", "stability", "architecture", "monitoring"]
    description: str
    affected_files: List[str]
    suggested_approach: str


@dataclass
class AnalysisResult:
    patterns: List[Pattern] = field(default_factory=list)
    health_score: int = 100
    recommendations: List[Recommendation] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)


class EvolverEngine:
    """
    Deterministic log analysis engine.
    Pure logic — no external AI dependency. Sub-100ms processing.
    """
    
    # Severity thresholds (from capability-evolver)
    SEVERITY_THRESHOLDS = {
        "critical": 10,
        "high": 5,
        "medium": 2,
        "low": 1,
    }
    
    # Inefficiency detection patterns
    INEFFICIENCY_PATTERNS = [
        re.compile(r"(\d{4,})ms"),      # Operations > 999ms
        re.compile(r"slow", re.I),
        re.compile(r"timeout", re.I),
        re.compile(r"retry", re.I),
    ]
    
    def analyze(self, logs: List[LogEntry]) -> AnalysisResult:
        """Run multi-pass analysis on log entries."""
        result = AnalysisResult()
        
        # Pass 1: Pattern Detection
        result.patterns = self._detect_patterns(logs)
        
        # Pass 2: Health Scoring
        result.health_score = self._calculate_health_score(logs, result.patterns)
        
        # Pass 3: Recommendation Generation
        result.recommendations = self._generate_recommendations(result)
        
        # Summary
        result.summary = {
            "total_logs": len(logs),
            "error_count": sum(1 for l in logs if l.level == "error"),
            "warn_count": sum(1 for l in logs if l.level == "warn"),
            "unique_patterns": len(result.patterns),
            "critical_count": sum(1 for p in result.patterns if p.severity == "critical"),
        }
        
        return result
    
    def _detect_patterns(self, logs: List[LogEntry]) -> List[Pattern]:
        """Detect error, regression, and inefficiency patterns."""
        patterns = []
        error_map: Dict[str, Dict] = defaultdict(
            lambda: {"count": 0, "first": "", "last": "", "files": set()}
        )
        
        # Aggregate errors and warnings
        for log in logs:
            if log.level in ("error", "warn"):
                key = log.message[:100]  # Group by message prefix
                error_map[key]["count"] += 1
                error_map[key]["last"] = log.timestamp
                if not error_map[key]["first"]:
                    error_map[key]["first"] = log.timestamp
                if log.context:
                    error_map[key]["files"].add(log.context)
        
        # Classify aggregated errors
        for msg, data in error_map.items():
            severity = self._classify_severity(data["count"])
            pattern_type = "regression" if data["count"] >= 3 else "error"
            
            patterns.append(Pattern(
                type=pattern_type,
                severity=severity,
                description=msg,
                occurrences=data["count"],
                first_seen=data["first"],
                last_seen=data["last"],
                affected_files=list(data["files"]),
            ))
        
        # Detect inefficiencies
        slow_ops = [
            log for log in logs
            if log.level == "info" and any(p.search(log.message) for p in self.INEFFICIENCY_PATTERNS)
        ]
        if len(slow_ops) >= 2:
            severity = "high" if len(slow_ops) >= 5 else "medium"
            patterns.append(Pattern(
                type="inefficiency",
                severity=severity,
                description=f"{len(slow_ops)} slow operations detected",
                occurrences=len(slow_ops),
                first_seen=slow_ops[0].timestamp,
                last_seen=slow_ops[-1].timestamp,
                affected_files=list(set(l.context for l in slow_ops if l.context)),
            ))
        
        # Sort by occurrences descending
        patterns.sort(key=lambda p: p.occurrences, reverse=True)
        return patterns[:50]  # Cap at 50 patterns
    
    def _classify_severity(self, count: int) -> Literal["low", "medium", "high", "critical"]:
        """Classify severity based on occurrence count."""
        if count >= self.SEVERITY_THRESHOLDS["critical"]:
            return "critical"
        elif count >= self.SEVERITY_THRESHOLDS["high"]:
            return "high"
        elif count >= self.SEVERITY_THRESHOLDS["medium"]:
            return "medium"
        return "low"
    
    def _calculate_health_score(
        self, 
        logs: List[LogEntry], 
        patterns: List[Pattern]
    ) -> int:
        """
        Calculate health score (0-100).
        
        Algorithm: 100 - error_penalty - warn_penalty
        - Error penalty: (error_count / total) * 100
        - Warn penalty: (warn_count / total) * 30
        """
        total = max(len(logs), 1)
        error_count = sum(1 for l in logs if l.level == "error")
        warn_count = sum(1 for l in logs if l.level == "warn")
        
        error_penalty = (error_count / total) * 100
        warn_penalty = (warn_count / total) * 30
        
        score = max(0, round(100 - error_penalty - warn_penalty))
        return score
    
    def _generate_recommendations(self, result: AnalysisResult) -> List[Recommendation]:
        """Generate actionable recommendations based on analysis."""
        recommendations = []
        
        critical_count = sum(1 for p in result.patterns if p.severity == "critical")
        regression_count = sum(1 for p in result.patterns if p.type == "regression")
        
        # Critical recommendations
        if critical_count > 0:
            recommendations.append(Recommendation(
                priority="immediate",
                category="error-handling",
                description="Critical patterns detected — prioritize immediate fixes",
                affected_files=[],
                suggested_approach="Address all critical severity patterns before new development",
            ))
        
        # Regression recommendations
        if regression_count >= 2:
            recommendations.append(Recommendation(
                priority="high",
                category="stability",
                description=f"Multiple regressions found ({regression_count})",
                affected_files=[],
                suggested_approach="Add regression tests and consider 'harden' strategy",
            ))
        
        # Health-based recommendations
        if result.health_score > 80 and len(result.patterns) < 3:
            recommendations.append(Recommendation(
                priority="low",
                category="architecture",
                description="System is healthy — safe to pursue innovation",
                affected_files=[],
                suggested_approach="Consider 'innovate' strategy for capability expansion",
            ))
        
        if result.health_score < 50:
            recommendations.append(Recommendation(
                priority="immediate",
                category="stability",
                description="Low health score — focus on stability",
                affected_files=[],
                suggested_approach="Enable review_mode and focus on stability before features",
            ))
        
        # Performance recommendations
        inefficiency_patterns = [p for p in result.patterns if p.type == "inefficiency"]
        for pattern in inefficiency_patterns:
            recommendations.append(Recommendation(
                priority="medium",
                category="performance",
                description=f"Optimize: {pattern.description}",
                affected_files=pattern.affected_files,
                suggested_approach="Profile slow path, add caching, or batch operations",
            ))
        
        # Hot file recommendations
        hot_files = defaultdict(int)
        for p in result.patterns:
            for f in p.affected_files:
                hot_files[f] += 1
        
        top_hot = sorted(hot_files.items(), key=lambda x: x[1], reverse=True)[:3]
        if top_hot:
            recommendations.append(Recommendation(
                priority="medium",
                category="monitoring",
                description=f"Hot files: {', '.join(f'{f} ({c})' for f, c in top_hot)}",
                affected_files=[f for f, _ in top_hot],
                suggested_approach="Review and add targeted tests for these files",
            ))
        
        return recommendations[:20]  # Cap at 20


class EvolutionPlanner:
    """Generate evolution proposals based on analysis results."""
    
    STRATEGIES = ["auto", "balanced", "innovate", "harden", "repair-only"]
    
    def generate_proposal(
        self, 
        analysis: AnalysisResult, 
        strategy: str = "auto",
        target_file: Optional[str] = None
    ) -> dict:
        """Generate structured evolution proposal."""
        
        # Auto-select strategy
        effective_strategy = self._select_strategy(strategy, analysis.health_score)
        
        # Filter recommendations by strategy
        recommendations = self._filter_by_strategy(
            analysis.recommendations, 
            effective_strategy,
            target_file
        )
        
        # Risk assessment
        critical_count = sum(1 for p in analysis.patterns if p.severity == "critical")
        risk_level = (
            "high" if critical_count >= 3 
            else "medium" if critical_count >= 1 
            else "low"
        )
        
        # Estimate improvement
        estimated_score = min(
            100, 
            analysis.health_score + (len(recommendations) * 5)
        )
        
        return {
            "evolution_id": f"evo_{int(datetime.now().timestamp())}",
            "strategy": effective_strategy,
            "recommendations": [
                {
                    "priority": r.priority,
                    "category": r.category,
                    "description": r.description,
                    "affected_files": r.affected_files,
                    "suggested_approach": r.suggested_approach,
                }
                for r in recommendations
            ],
            "risk_assessment": {
                "level": risk_level,
                "factors": [
                    p.description for p in analysis.patterns 
                    if p.severity == "critical"
                ][:5],
            },
            "estimated_improvement": (
                f"Health score: {analysis.health_score} → ~{estimated_score}"
            ),
        }
    
    def _select_strategy(self, strategy: str, health_score: int) -> str:
        """Auto-select strategy based on health score."""
        if strategy != "auto":
            return strategy
        
        if health_score < 40:
            return "repair-only"
        elif health_score < 70:
            return "harden"
        return "balanced"
    
    def _filter_by_strategy(
        self, 
        recommendations: List[Recommendation],
        strategy: str,
        target_file: Optional[str]
    ) -> List[Recommendation]:
        """Filter recommendations based on strategy."""
        filtered = recommendations
        
        if target_file:
            filtered = [
                r for r in filtered 
                if not r.affected_files or target_file in r.affected_files
            ]
        
        if strategy == "repair-only":
            # Only immediate/high priority error handling
            filtered = [
                r for r in filtered 
                if r.priority in ("immediate", "high") 
                and r.category == "error-handling"
            ]
        elif strategy == "harden":
            # Focus on stability and monitoring
            filtered = [
                r for r in filtered 
                if r.category in ("stability", "monitoring", "error-handling")
            ]
        elif strategy == "innovate":
            # Include architecture recommendations
            pass  # Keep all, including low-priority architecture
        
        return filtered
```

### 7.2 Integration with Existing MedicAgent

```python
# myclaw/agents/medic_agent.py (enhanced)

from .medic_evolver import EvolverEngine, EvolutionPlanner, LogEntry

class MedicAgent:
    def __init__(self, repo_url: str = DEFAULT_REPO_URL):
        # ... existing init ...
        self._evolver = EvolverEngine()
        self._planner = EvolutionPlanner()
        self._health_history: list[dict] = []
    
    # ... existing methods ...
    
    def analyze_logs_deterministic(self, logs: list[dict]) -> dict:
        """
        NEW: Capability-evolver style deterministic analysis.
        
        Args:
            logs: List of dicts with keys: timestamp, level, message, context
            
        Returns:
            Structured analysis result with patterns, health_score, recommendations
        """
        entries = [
            LogEntry(
                timestamp=l.get("timestamp", ""),
                level=l.get("level", "info"),
                message=l.get("message", ""),
                context=l.get("context", ""),
            )
            for l in logs
        ]
        
        result = self._evolver.analyze(entries)
        
        # Record health history
        self._health_history.append({
            "timestamp": datetime.now().isoformat(),
            "score": result.health_score,
            "pattern_count": len(result.patterns),
        })
        
        # Convert to dict for backward compatibility
        return {
            "patterns": [
                {
                    "type": p.type,
                    "severity": p.severity,
                    "description": p.description,
                    "occurrences": p.occurrences,
                    "affected_contexts": p.affected_files,
                }
                for p in result.patterns
            ],
            "health_score": result.health_score,
            "recommendations": [r.description for r in result.recommendations],
            "summary": result.summary,
        }
    
    def generate_evolution_plan(
        self, 
        logs: list[dict], 
        strategy: str = "auto",
        target_file: str = ""
    ) -> dict:
        """
        NEW: Generate structured evolution proposal.
        
        Args:
            logs: Log entries to analyze
            strategy: Evolution strategy (auto, balanced, innovate, harden, repair-only)
            target_file: Optional file to focus analysis on
            
        Returns:
            Evolution proposal with prioritized recommendations
        """
        analysis = self.analyze_logs_deterministic(logs)
        
        # Reconstruct AnalysisResult for planner
        from .medic_evolver import AnalysisResult, Pattern, Recommendation
        
        result = AnalysisResult(
            patterns=[
                Pattern(
                    type=p["type"],
                    severity=p["severity"],
                    description=p["description"],
                    occurrences=p["occurrences"],
                    first_seen="",
                    last_seen="",
                    affected_files=p.get("affected_contexts", []),
                )
                for p in analysis["patterns"]
            ],
            health_score=analysis["health_score"],
            recommendations=[
                Recommendation(
                    priority="medium",  # Default if not preserved
                    category="stability",
                    description=r,
                    affected_files=[],
                    suggested_approach="",
                )
                for r in analysis["recommendations"]
            ],
        )
        
        return self._planner.generate_proposal(
            result, strategy, target_file or None
        )
    
    def get_unified_health_score(self) -> dict:
        """
        NEW: Calculate unified health score combining all subsystems.
        
        Returns:
            Dict with overall score, component scores, and trend
        """
        # File integrity component (0-25 points)
        integrity = self.check_integrity()
        total_files = integrity.get("valid", 0) + integrity.get("corrupted", 0) + integrity.get("missing", 0)
        integrity_score = (
            (integrity.get("valid", 0) / max(total_files, 1)) * 25
            if total_files > 0 else 25
        )
        
        # Task performance component (0-25 points)
        tasks = self.get_task_analytics()
        task_score = 0
        if tasks.get("status") == "ok":
            avg_success = sum(
                s["success_rate"] for s in tasks["tasks"].values()
            ) / max(len(tasks["tasks"]), 1)
            task_score = avg_success * 25
        else:
            task_score = 25  # No data = assume good
        
        # Log health component (0-25 points) — from last analysis
        log_score = 25
        if self._health_history:
            last_health = self._health_history[-1]["score"]
            log_score = (last_health / 100) * 25
        
        # Syntax/component health (0-25 points)
        syntax_score = 25  # Placeholder — could scan core files
        
        overall = round(integrity_score + task_score + log_score + syntax_score)
        
        # Trend
        trend = "stable"
        if len(self._health_history) >= 2:
            recent = [h["score"] for h in self._health_history[-5:]]
            if len(recent) >= 2:
                slope = (recent[-1] - recent[0]) / len(recent)
                trend = "improving" if slope > 2 else "degrading" if slope < -2 else "stable"
        
        return {
            "overall_score": overall,
            "grade": self._score_to_grade(overall),
            "components": {
                "integrity": round(integrity_score),
                "tasks": round(task_score),
                "logs": round(log_score),
                "syntax": round(syntax_score),
            },
            "trend": trend,
            "history": self._health_history[-10:],
        }
    
    def _score_to_grade(self, score: int) -> str:
        """Convert numeric score to letter grade."""
        if score >= 90: return "A"
        if score >= 80: return "B"
        if score >= 70: return "C"
        if score >= 60: return "D"
        return "F"
```

### 7.3 Enhanced LogAnalyzer with Pattern Detection

```python
# myclaw/agents/medic_change_mgmt.py (enhanced LogAnalyzer)

class LogAnalyzer:
    """Enhanced log analyzer with capability-evolver pattern detection."""
    
    def __init__(self):
        self.medic_dir = MEDIC_DIR
        self.log_sources: List[Path] = []
        self.evolver = EvolverEngine()  # NEW: Add evolver engine
        
        # Legacy regex patterns (keep for compatibility)
        self.anomaly_patterns = [
            r"ERROR", r"CRITICAL", r"FATAL", r"Exception", r"Traceback",
            r"timeout", r"failed", r"connection.*refused",
            r"permission denied", r"disk full", r"memory error", r"segmentation fault",
        ]
        self._configure_default_sources()
    
    def analyze_logs_enhanced(self, since_minutes: int = 60) -> Dict[str, Any]:
        """
        NEW: Enhanced analysis using capability-evolver patterns.
        
        Returns structured result with patterns, health score, and recommendations.
        """
        # Collect raw log entries
        raw_logs = self._collect_raw_logs(since_minutes)
        
        # Convert to LogEntry format
        entries = []
        for log in raw_logs:
            level = self._detect_level(log["content"])
            entries.append(LogEntry(
                timestamp=log.get("timestamp", datetime.now().isoformat()),
                level=level,
                message=log["content"][:200],
                context=str(log.get("file", "")),
            ))
        
        # Run deterministic analysis
        result = self.evolver.analyze(entries)
        
        return {
            "timestamp": datetime.now().isoformat(),
            "sources_analyzed": len(self.log_sources),
            "total_lines": len(raw_logs),
            "health_score": result.health_score,
            "patterns": [
                {
                    "type": p.type,
                    "severity": p.severity,
                    "description": p.description,
                    "occurrences": p.occurrences,
                    "affected_files": p.affected_files,
                }
                for p in result.patterns
            ],
            "recommendations": [
                {
                    "priority": r.priority,
                    "category": r.category,
                    "description": r.description,
                    "affected_files": r.affected_files,
                }
                for r in result.recommendations
            ],
            "summary": result.summary,
        }
    
    def _detect_level(self, content: str) -> Literal["error", "warn", "info", "debug"]:
        """Detect log level from content."""
        upper = content.upper()
        if "ERROR" in upper or "CRITICAL" in upper or "FATAL" in upper or "EXCEPTION" in upper:
            return "error"
        elif "WARN" in upper:
            return "warn"
        elif "DEBUG" in upper:
            return "debug"
        return "info"
    
    def _collect_raw_logs(self, since_minutes: int) -> List[Dict]:
        """Collect raw log entries from all sources."""
        raw_logs = []
        cutoff = datetime.now() - timedelta(minutes=since_minutes)
        
        for source in self.log_sources:
            if not source.exists():
                continue
            
            try:
                if source.is_dir():
                    for log_file in source.glob("*.log"):
                        raw_logs.extend(self._parse_log_file(log_file, cutoff))
                else:
                    raw_logs.extend(self._parse_log_file(source, cutoff))
            except Exception as e:
                logger.error(f"Error reading log source {source}: {e}")
        
        return raw_logs
    
    def _parse_log_file(self, log_file: Path, cutoff: datetime) -> List[Dict]:
        """Parse a single log file and filter by time."""
        results = []
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            for line_num, line in enumerate(content.split("\n"), 1):
                if not line.strip():
                    continue
                
                # Try to extract timestamp
                ts = self._extract_timestamp(line)
                if ts:
                    try:
                        log_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if log_time < cutoff:
                            continue
                    except:
                        pass
                
                results.append({
                    "file": str(log_file),
                    "line": line_num,
                    "content": line,
                    "timestamp": ts or datetime.now().isoformat(),
                })
        except Exception as e:
            logger.error(f"Error parsing log file {log_file}: {e}")
        
        return results
```

### 7.4 Bug Fix: LoopDetector

```python
# Fix for LoopDetector.check() — current implementation doesn't increment

class LoopDetector:
    """Fixed loop detector with proper increment tracking."""
    
    def __init__(self, max_iterations: int = 100):
        self._executions: Dict[str, List[float]] = {}
        self._max_iterations = max_iterations
    
    def is_looping(self, pattern: str, max_iterations: int = None) -> bool:
        """Check if pattern has exceeded max iterations."""
        max_iter = max_iterations or self._max_iterations
        
        if pattern not in self._executions:
            self._executions[pattern] = []
        
        now = datetime.now().timestamp()
        self._executions[pattern].append(now)
        
        # Keep only last 60 seconds
        cutoff = now - 60
        recent = [t for t in self._executions[pattern] if t > cutoff]
        self._executions[pattern] = recent
        
        return len(recent) > max_iter
    
    def check(self, execution_id: str, max_calls: int = 50) -> Tuple[bool, int]:
        """
        FIXED: Check if execution has exceeded call limit.
        
        Previous bug: count was read but never incremented.
        """
        if execution_id not in self._executions:
            self._executions[execution_id] = []
        
        # FIXED: Increment the counter
        self._executions[execution_id].append(datetime.now().timestamp())
        
        count = len(self._executions[execution_id])
        is_looping = count >= max_calls
        
        return is_looping, count
    
    def record_iteration(self, pattern: str) -> bool:
        """
        NEW: Record an iteration and return whether allowed.
        Used by test_medic_agent.py (already expects this interface).
        """
        if pattern not in self._executions:
            self._executions[pattern] = []
        
        self._executions[pattern].append(datetime.now().timestamp())
        
        # Trim old entries (keep last 60 seconds)
        cutoff = datetime.now().timestamp() - 60
        self._executions[pattern] = [
            t for t in self._executions[pattern] if t > cutoff
        ]
        
        return len(self._executions[pattern]) <= self._max_iterations
    
    def clear(self, execution_id: str) -> None:
        """Clear execution tracking."""
        self._executions.pop(execution_id, None)
    
    def get_count(self, execution_id: str) -> int:
        """Get current count for execution."""
        return len(self._executions.get(execution_id, []))
```

### 7.5 Gateway Integration Enhancement

```python
# myclaw/gateway.py (enhanced startup health check)

def _run_startup_health_check(config):
    """Enhanced startup health check with evolver analysis."""
    try:
        medic_cfg = getattr(config, "medic", None)
        if not medic_cfg:
            return
        if not getattr(medic_cfg, "enabled", False):
            return
        
        logger.info("Starting health check...")
        medic = MedicAgent()
        
        # Phase 1: File integrity scan
        scan_result = medic.scan_system()
        logger.info("File integrity scan: %s files scanned", scan_result.get("scanned", 0))
        
        # Phase 2: Unified health score (NEW)
        if getattr(medic_cfg, "enable_evolver_analysis", False):
            health = medic.get_unified_health_score()
            logger.info(
                "System health score: %s/100 (Grade: %s, Trend: %s)",
                health["overall_score"],
                health["grade"],
                health["trend"]
            )
            
            if health["overall_score"] < 60:
                logger.warning("⚠️ Health score below threshold — consider running repairs")
        
        # Phase 3: Log analysis if scan_on_startup (NEW)
        if getattr(medic_cfg, "scan_on_startup", False):
            from .agents.medic_change_mgmt import LogAnalyzer
            analyzer = LogAnalyzer()
            
            # Try enhanced analysis first
            try:
                analysis = analyzer.analyze_logs_enhanced(since_minutes=60)
                if analysis["health_score"] < 50:
                    logger.warning(
                        "Recent log analysis shows health score: %s — %d patterns detected",
                        analysis["health_score"],
                        len(analysis["patterns"])
                    )
            except Exception:
                # Fall back to legacy analysis
                pass
        
    except Exception as exc:
        logger.error("Startup health check failed (continuing startup): %s", exc)
```

---

## 8. Prioritized Roadmap

### Critical Priority (Implement First)

| # | Feature | Files | Effort | Impact |
|---|---------|-------|--------|--------|
| 1 | **Fix LoopDetector.check() bug** | `medic_agent.py` | 1h | High — currently broken |
| 2 | **Create EvolverEngine** | `medic_evolver.py` (new) | 1d | Critical — core analysis |
| 3 | **Add health scoring to MedicAgent** | `medic_agent.py` | 4h | Critical — unified metric |
| 4 | **Replace curl with aiohttp** | `medic_agent.py` | 2h | High — Windows compatibility |

### High Priority (Week 2)

| # | Feature | Files | Effort | Impact |
|---|---------|-------|--------|--------|
| 5 | **Integrate evolver with LogAnalyzer** | `medic_change_mgmt.py` | 1d | High — better diagnostics |
| 6 | **Add evolution strategies to change mgmt** | `medic_change_mgmt.py` | 6h | High — strategic direction |
| 7 | **Async file I/O migration** | `medic_agent.py`, `medic_change_mgmt.py` | 1d | Medium — performance |
| 8 | **Health history persistence** | `medic_evolver.py` | 4h | Medium — trend analysis |

### Medium Priority (Week 3-4)

| # | Feature | Files | Effort | Impact |
|---|---------|-------|--------|--------|
| 9 | **Error cascade detection** | `medic_evolver.py` | 1d | Medium — dependency awareness |
| 10 | **Plugin architecture for analyzers** | `medic_evolver.py` | 1d | Medium — extensibility |
| 11 | **Structured dataclass models** | All medic files | 1d | Medium — type safety |
| 12 | **Config dataclass refactor** | `medic_agent.py` | 4h | Medium — decoupling |

### Low Priority (Future)

| # | Feature | Files | Effort | Impact |
|---|---------|-------|--------|--------|
| 13 | **Health score prediction** | `medic_evolver.py` | 2d | Low — ML-like feature |
| 14 | **Fleet-wide analysis** | New module | 3d | Low — multi-agent |
| 15 | **External CMDB integration** | `medic_change_mgmt.py` | 3d | Low — enterprise |

---

## Appendix A: Test Coverage Recommendations

Add these test cases to `tests/test_medic_agent.py`:

```python
# Test 1: EvolverEngine deterministic analysis
def test_evolver_engine_analysis():
    engine = EvolverEngine()
    logs = [
        LogEntry(timestamp="2025-01-01T10:00:00Z", level="error", message="Timeout", context="api.py"),
        LogEntry(timestamp="2025-01-01T10:01:00Z", level="error", message="Timeout", context="api.py"),
        LogEntry(timestamp="2025-01-01T10:02:00Z", level="error", message="Timeout", context="api.py"),
    ]
    result = engine.analyze(logs)
    assert result.health_score < 100
    assert len(result.patterns) > 0
    assert result.patterns[0].type == "regression"  # 3+ occurrences

# Test 2: Health score calculation
def test_health_score_bounds():
    engine = EvolverEngine()
    # All errors → score should be 0
    logs = [LogEntry(timestamp="now", level="error", message="fail") for _ in range(100)]
    result = engine.analyze(logs)
    assert result.health_score == 0
    
    # All clean → score should be 100
    logs = [LogEntry(timestamp="now", level="info", message="ok") for _ in range(100)]
    result = engine.analyze(logs)
    assert result.health_score == 100

# Test 3: Evolution strategy auto-selection
def test_strategy_auto_selection():
    planner = EvolutionPlanner()
    assert planner._select_strategy("auto", 30) == "repair-only"
    assert planner._select_strategy("auto", 50) == "harden"
    assert planner._select_strategy("auto", 80) == "balanced"
    assert planner._select_strategy("harden", 50) == "harden"  # Manual override

# Test 4: LoopDetector increment fix
def test_loop_detector_increments():
    detector = LoopDetector(max_iterations=3)
    detector.check("exec1", max_calls=3)
    is_looping, count = detector.check("exec1", max_calls=3)
    assert count == 2  # Should increment
    assert not is_looping
    
    detector.check("exec1", max_calls=3)
    is_looping, count = detector.check("exec1", max_calls=3)
    assert count == 3
    assert is_looping  # Should now be looping
```

---

## Appendix B: Migration Guide

### For Existing Users

**No breaking changes.** All new functionality is additive:

1. Existing `MedicAgent` methods remain unchanged
2. New methods: `analyze_logs_deterministic()`, `generate_evolution_plan()`, `get_unified_health_score()`
3. New config options (all optional with sensible defaults):
   ```python
   medic = {
       "enabled": True,
       "enable_hash_check": True,
       "enable_evolver_analysis": True,  # NEW
       "evolution_strategy": "auto",     # NEW
       "health_threshold_critical": 40,  # NEW
       "health_threshold_warning": 70,   # NEW
   }
   ```

### For Tool Integrations

Register new tools alongside existing ones:

```python
from myclaw.tools import register_tool
from myclaw.agents.medic_agent import MedicAgent

medic = MedicAgent()

register_tool("analyze_logs", medic.analyze_logs_deterministic)
register_tool("health_score", medic.get_unified_health_score)
register_tool("evolve_plan", medic.generate_evolution_plan)
```

---

*End of Analysis*
