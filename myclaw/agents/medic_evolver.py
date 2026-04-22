"""Medic Evolver — Deterministic analysis engine inspired by capability-evolver.

This module provides a pure-logic, deterministic analysis engine that processes
structured log data and produces actionable diagnostics. No LLM is involved —
the analysis is rule-based, which means results are reproducible and fast.

Integration with MedicAgent:
    from myclaw.agents.medic_agent import MedicAgent
    medic = MedicAgent()
    analysis = medic.analyze_logs_deterministic(logs)
    proposal = medic.generate_evolution_plan(logs, strategy="harden")
    health = medic.get_unified_health_score()

Design Principles (from capability-evolver):
  1. Deterministic — same logs always produce same results.
  2. Reproducible — no hallucination, audit-friendly.
  3. Fast — sub-100ms processing, no external API calls.
  4. Structured — typed I/O, dict-native.
  5. Composable — action-based API (analyze → evolve → status).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional
from collections import defaultdict


# ─── Enums ───────────────────────────────────────────────────

class PatternType(str, Enum):
    """Types of detected patterns."""
    ERROR = "error"
    REGRESSION = "regression"
    INEFFICIENCY = "inefficiency"
    DRIFT = "drift"
    CASCADE = "cascade"


class Severity(str, Enum):
    """Severity levels for patterns."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Priority(str, Enum):
    """Priority levels for recommendations."""
    IMMEDIATE = "immediate"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Category(str, Enum):
    """Categories for recommendations."""
    ERROR_HANDLING = "error-handling"
    PERFORMANCE = "performance"
    STABILITY = "stability"
    ARCHITECTURE = "architecture"
    MONITORING = "monitoring"


class EvolutionStrategy(str, Enum):
    """Evolution strategies for improvement planning."""
    AUTO = "auto"
    BALANCED = "balanced"
    INNOVATE = "innovate"
    HARDEN = "harden"
    REPAIR_ONLY = "repair-only"


# ─── Data Classes ────────────────────────────────────────────

@dataclass(frozen=True)
class LogEntry:
    """A single structured log entry."""
    timestamp: str
    level: Literal["error", "warn", "info", "debug"]
    message: str
    context: str = ""
    stack: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "message": self.message,
            "context": self.context,
            "stack": self.stack,
        }


@dataclass(frozen=True)
class Pattern:
    """A detected pattern in log data."""
    type: str
    severity: str
    description: str
    occurrences: int
    first_seen: str
    last_seen: str
    affected_files: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "severity": self.severity,
            "description": self.description,
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "affected_files": self.affected_files,
        }


@dataclass(frozen=True)
class Recommendation:
    """An actionable improvement recommendation."""
    priority: str
    category: str
    description: str
    affected_files: List[str]
    suggested_approach: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "priority": self.priority,
            "category": self.category,
            "description": self.description,
            "affected_files": self.affected_files,
            "suggested_approach": self.suggested_approach,
        }


@dataclass
class AnalysisResult:
    """Result of a log analysis pass."""
    patterns: List[Pattern] = field(default_factory=list)
    health_score: int = 100
    recommendations: List[Recommendation] = field(default_factory=list)
    summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patterns": [p.to_dict() for p in self.patterns],
            "health_score": self.health_score,
            "recommendations": [r.to_dict() for r in self.recommendations],
            "summary": self.summary,
        }


# ─── EvolverEngine ───────────────────────────────────────────

class EvolverEngine:
    """
    Deterministic log analysis engine (pure logic — no external AI dependency).

    Processes structured log data through a multi-pass analysis pipeline:
      Pass 1: Pattern Detection     — aggregate errors, classify severity, detect inefficiencies
      Pass 2: Health Scoring        — compute 0-100 score from error/warn rates
      Pass 3: Recommendation Gen    — context-aware actionable suggestions
    """

    # Severity thresholds (logarithmic escalation)
    SEVERITY_THRESHOLDS = {"critical": 10, "high": 5, "medium": 2, "low": 1}

    # Inefficiency detection patterns
    INEFFICIENCY_PATTERNS = [
        re.compile(r"(\d{4,})ms"),      # operations > 999 ms
        re.compile(r"slow", re.I),
        re.compile(r"timeout", re.I),
        re.compile(r"retry", re.I),
        re.compile(r"retrying", re.I),
    ]

    # Cascade detection window (seconds)
    CASCADE_WINDOW_SECONDS = 60

    def analyze(self, logs: List[LogEntry]) -> AnalysisResult:
        """Run multi-pass analysis on log entries."""
        result = AnalysisResult()
        result.patterns = self._detect_patterns(logs)
        result.health_score = self._calculate_health_score(logs, result.patterns)
        result.recommendations = self._generate_recommendations(result)
        result.summary = {
            "total_logs": len(logs),
            "error_count": sum(1 for l in logs if l.level == "error"),
            "warn_count": sum(1 for l in logs if l.level == "warn"),
            "unique_patterns": len(result.patterns),
            "critical_count": sum(1 for p in result.patterns if p.severity == Severity.CRITICAL.value),
        }
        return result

    # ─── Pass 1: Pattern Detection ─────────────────────────

    def _detect_patterns(self, logs: List[LogEntry]) -> List[Pattern]:
        """Detect error, regression, inefficiency, and cascade patterns."""
        patterns: List[Pattern] = []

        # Aggregate errors and warnings
        error_map: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "first": "", "last": "", "files": set()}
        )

        for log in logs:
            if log.level in ("error", "warn"):
                key = log.message[:100]  # group by message prefix
                entry = error_map[key]
                entry["count"] += 1
                entry["last"] = log.timestamp
                if not entry["first"]:
                    entry["first"] = log.timestamp
                if log.context:
                    entry["files"].add(log.context)

        # Classify aggregated errors into Pattern objects
        for msg, data in error_map.items():
            severity = self._classify_severity(data["count"])
            pattern_type = PatternType.REGRESSION.value if data["count"] >= 3 else PatternType.ERROR.value
            patterns.append(Pattern(
                type=pattern_type,
                severity=severity,
                description=msg,
                occurrences=data["count"],
                first_seen=data["first"],
                last_seen=data["last"],
                affected_files=sorted(data["files"]),
            ))

        # Detect inefficiencies
        slow_ops = [
            log for log in logs
            if log.level == "info" and any(p.search(log.message) for p in self.INEFFICIENCY_PATTERNS)
        ]
        if len(slow_ops) >= 2:
            severity = Severity.HIGH.value if len(slow_ops) >= 5 else Severity.MEDIUM.value
            patterns.append(Pattern(
                type=PatternType.INEFFICIENCY.value,
                severity=severity,
                description=f"{len(slow_ops)} slow operations detected",
                occurrences=len(slow_ops),
                first_seen=slow_ops[0].timestamp,
                last_seen=slow_ops[-1].timestamp,
                affected_files=sorted({l.context for l in slow_ops if l.context}),
            ))

        # Detect cascades (errors in one module followed by errors in another)
        cascade_patterns = self._detect_cascades(logs)
        patterns.extend(cascade_patterns)

        # Sort by occurrences descending, cap at 50
        patterns.sort(key=lambda p: p.occurrences, reverse=True)
        return patterns[:50]

    def _classify_severity(self, count: int) -> str:
        """Classify severity based on occurrence count."""
        if count >= self.SEVERITY_THRESHOLDS["critical"]:
            return Severity.CRITICAL.value
        elif count >= self.SEVERITY_THRESHOLDS["high"]:
            return Severity.HIGH.value
        elif count >= self.SEVERITY_THRESHOLDS["medium"]:
            return Severity.MEDIUM.value
        return Severity.LOW.value

    def _detect_cascades(self, logs: List[LogEntry]) -> List[Pattern]:
        """
        Detect error cascades: errors in module A followed by errors in module B
        within CASCADE_WINDOW_SECONDS.
        """
        patterns: List[Pattern] = []
        error_logs = [l for l in logs if l.level == "error"]
        if len(error_logs) < 2:
            return patterns

        # Group errors by context and track first occurrence time
        context_errors: Dict[str, List[str]] = defaultdict(list)
        for log in error_logs:
            if log.context:
                context_errors[log.context].append(log.timestamp)

        contexts = sorted(context_errors.keys())
        for i, ctx_a in enumerate(contexts):
            for ctx_b in contexts[i + 1:]:
                # Check if ctx_a errors precede ctx_b errors within window
                if context_errors[ctx_a] and context_errors[ctx_b]:
                    first_a = context_errors[ctx_a][0]
                    first_b = context_errors[ctx_b][0]
                    # Simple string comparison works for ISO timestamps
                    if first_a < first_b:
                        patterns.append(Pattern(
                            type=PatternType.CASCADE.value,
                            severity=Severity.HIGH.value,
                            description=f"Error cascade: {ctx_a} → {ctx_b}",
                            occurrences=min(len(context_errors[ctx_a]), len(context_errors[ctx_b])),
                            first_seen=first_a,
                            last_seen=context_errors[ctx_b][-1],
                            affected_files=[ctx_a, ctx_b],
                        ))
        return patterns

    # ─── Pass 2: Health Scoring ────────────────────────────

    def _calculate_health_score(
        self,
        logs: List[LogEntry],
        patterns: List[Pattern],
    ) -> int:
        """
        Calculate health score (0-100).

        Algorithm: 100 - error_penalty - warn_penalty
          - Error penalty: (error_count / total) * 100
          - Warn penalty:  (warn_count  / total) * 30
        """
        total = max(len(logs), 1)
        error_count = sum(1 for l in logs if l.level == "error")
        warn_count = sum(1 for l in logs if l.level == "warn")

        error_penalty = (error_count / total) * 100
        warn_penalty = (warn_count / total) * 30

        score = max(0, round(100 - error_penalty - warn_penalty))
        return score

    # ─── Pass 3: Recommendation Generation ─────────────────

    def _generate_recommendations(self, result: AnalysisResult) -> List[Recommendation]:
        """Generate actionable recommendations based on analysis."""
        recommendations: List[Recommendation] = []

        critical_count = sum(1 for p in result.patterns if p.severity == Severity.CRITICAL.value)
        regression_count = sum(1 for p in result.patterns if p.type == PatternType.REGRESSION.value)

        # Critical patterns
        if critical_count > 0:
            recommendations.append(Recommendation(
                priority=Priority.IMMEDIATE.value,
                category=Category.ERROR_HANDLING.value,
                description="Critical patterns detected — prioritize immediate fixes before any new development",
                affected_files=[],
                suggested_approach="Address all critical severity patterns before new development",
            ))

        # Multiple regressions
        if regression_count >= 2:
            recommendations.append(Recommendation(
                priority=Priority.HIGH.value,
                category=Category.STABILITY.value,
                description=f"Multiple regressions found ({regression_count})",
                affected_files=[],
                suggested_approach="Add regression tests and consider 'harden' strategy",
            ))

        # Health-based recommendations
        if result.health_score > 80 and len(result.patterns) < 3:
            recommendations.append(Recommendation(
                priority=Priority.LOW.value,
                category=Category.ARCHITECTURE.value,
                description="System is healthy — safe to pursue 'innovate' strategy for capability expansion",
                affected_files=[],
                suggested_approach="Consider 'innovate' strategy for capability expansion",
            ))

        if result.health_score < 50:
            recommendations.append(Recommendation(
                priority=Priority.IMMEDIATE.value,
                category=Category.STABILITY.value,
                description="Low health score — focus on stability before adding features",
                affected_files=[],
                suggested_approach="Enable review_mode and focus on stability before features",
            ))

        # Inefficiency recommendations
        inefficiency_patterns = [p for p in result.patterns if p.type == PatternType.INEFFICIENCY.value]
        for pattern in inefficiency_patterns:
            recommendations.append(Recommendation(
                priority=Priority.MEDIUM.value,
                category=Category.PERFORMANCE.value,
                description=f"Optimize: {pattern.description}",
                affected_files=pattern.affected_files,
                suggested_approach="Profile slow path, add caching, or batch operations where possible",
            ))

        # Hot file recommendations
        hot_files: Dict[str, int] = defaultdict(int)
        for p in result.patterns:
            for f in p.affected_files:
                hot_files[f] += 1

        top_hot = sorted(hot_files.items(), key=lambda x: x[1], reverse=True)[:3]
        if top_hot:
            recommendations.append(Recommendation(
                priority=Priority.MEDIUM.value,
                category=Category.MONITORING.value,
                description=f"Hot files (most issues): {', '.join(f'{f} ({c})' for f, c in top_hot)}",
                affected_files=[f for f, _ in top_hot],
                suggested_approach="Review and add targeted tests for these files",
            ))

        return recommendations[:20]  # cap at 20


# ─── EvolutionPlanner ────────────────────────────────────────

class EvolutionPlanner:
    """
    Generate evolution proposals based on analysis results.

    Maps detected patterns and health scores into prioritized improvement
    recommendations with risk assessment and estimated impact.
    """

    VALID_STRATEGIES = [s.value for s in EvolutionStrategy]

    def generate_proposal(
        self,
        analysis: AnalysisResult,
        strategy: str = "auto",
        target_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate structured evolution proposal."""
        # Auto-select strategy
        effective_strategy = self._select_strategy(strategy, analysis.health_score)

        # Filter recommendations
        recommendations = self._filter_by_strategy(
            analysis.recommendations, effective_strategy, target_file
        )

        # Risk assessment
        critical_count = sum(1 for p in analysis.patterns if p.severity == Severity.CRITICAL.value)
        risk_level = (
            "high" if critical_count >= 3
            else "medium" if critical_count >= 1
            else "low"
        )

        # Estimate improvement
        estimated_score = min(100, analysis.health_score + (len(recommendations) * 5))

        return {
            "evolution_id": f"evo_{int(datetime.now().timestamp())}",
            "strategy": effective_strategy,
            "recommendations": [r.to_dict() for r in recommendations],
            "risk_assessment": {
                "level": risk_level,
                "factors": [
                    p.description for p in analysis.patterns
                    if p.severity == Severity.CRITICAL.value
                ][:5],
            },
            "estimated_improvement": f"Health score: {analysis.health_score} → ~{estimated_score}",
        }

    def _select_strategy(self, strategy: str, health_score: int) -> str:
        """Auto-select strategy based on health score."""
        if strategy != EvolutionStrategy.AUTO.value:
            if strategy in self.VALID_STRATEGIES:
                return strategy
            return EvolutionStrategy.BALANCED.value

        if health_score < 40:
            return EvolutionStrategy.REPAIR_ONLY.value
        elif health_score < 70:
            return EvolutionStrategy.HARDEN.value
        return EvolutionStrategy.BALANCED.value

    def _filter_by_strategy(
        self,
        recommendations: List[Recommendation],
        strategy: str,
        target_file: Optional[str],
    ) -> List[Recommendation]:
        """Filter recommendations based on strategy and target file."""
        filtered = list(recommendations)

        if target_file:
            filtered = [
                r for r in filtered
                if not r.affected_files or target_file in r.affected_files
            ]

        if strategy == EvolutionStrategy.REPAIR_ONLY.value:
            filtered = [
                r for r in filtered
                if r.priority in (Priority.IMMEDIATE.value, Priority.HIGH.value)
                and r.category == Category.ERROR_HANDLING.value
            ]
        elif strategy == EvolutionStrategy.HARDEN.value:
            filtered = [
                r for r in filtered
                if r.category in (
                    Category.STABILITY.value,
                    Category.MONITORING.value,
                    Category.ERROR_HANDLING.value,
                )
            ]
        elif strategy == EvolutionStrategy.INNOVATE.value:
            # Include all; architecture recs are already low-priority
            pass
        elif strategy == EvolutionStrategy.BALANCED.value:
            # Drop low-priority architecture suggestions if there are other issues
            has_issues = any(
                r.priority in (Priority.IMMEDIATE.value, Priority.HIGH.value)
                for r in filtered
            )
            if has_issues:
                filtered = [r for r in filtered if r.priority != Priority.LOW.value]

        return filtered


# ─── Convenience functions ───────────────────────────────────

def analyze_logs(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convenience function: analyze raw log dicts and return structured result.

    Args:
        logs: List of dicts with keys: timestamp, level, message, context (optional)

    Returns:
        Dict with patterns, health_score, recommendations, summary
    """
    entries = [
        LogEntry(
            timestamp=str(l.get("timestamp", "")),
            level=l.get("level", "info"),
            message=str(l.get("message", "")),
            context=str(l.get("context", "")),
        )
        for l in logs
    ]
    engine = EvolverEngine()
    result = engine.analyze(entries)
    return result.to_dict()


def generate_evolution_proposal(
    analysis_result: Dict[str, Any],
    strategy: str = "auto",
    target_file: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function: generate evolution proposal from analysis result.

    Args:
        analysis_result: Output from analyze_logs()
        strategy: Evolution strategy (auto, balanced, innovate, harden, repair-only)
        target_file: Optional file to focus on

    Returns:
        Dict with evolution_id, strategy, recommendations, risk_assessment
    """
    analysis = AnalysisResult(
        patterns=[
            Pattern(
                type=p["type"],
                severity=p["severity"],
                description=p["description"],
                occurrences=p["occurrences"],
                first_seen=p.get("first_seen", ""),
                last_seen=p.get("last_seen", ""),
                affected_files=p.get("affected_files", []),
            )
            for p in analysis_result.get("patterns", [])
        ],
        health_score=analysis_result.get("health_score", 100),
        recommendations=[
            Recommendation(
                priority=r["priority"],
                category=r["category"],
                description=r["description"],
                affected_files=r.get("affected_files", []),
                suggested_approach=r.get("suggested_approach", ""),
            )
            for r in analysis_result.get("recommendations", [])
        ],
        summary=analysis_result.get("summary", {}),
    )
    planner = EvolutionPlanner()
    return planner.generate_proposal(analysis, strategy, target_file)


__all__ = [
    # Enums
    "PatternType", "Severity", "Priority", "Category", "EvolutionStrategy",
    # Dataclasses
    "LogEntry", "Pattern", "Recommendation", "AnalysisResult",
    # Engines
    "EvolverEngine", "EvolutionPlanner",
    # Functions
    "analyze_logs", "generate_evolution_proposal",
]
