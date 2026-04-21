"""Medic Agent - System health monitoring, integrity checking, and error recovery.

v2.0 — Enhanced with deterministic evolver analysis engine.
"""

import ast
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta

from .medic_evolver import (
    EvolverEngine,
    EvolutionPlanner,
    LogEntry,
    AnalysisResult,
    Pattern,
    Recommendation,
)

logger = logging.getLogger(__name__)

MEDIC_DIR = Path.home() / ".myclaw" / "medic"
MEDIC_DIR.mkdir(parents=True, exist_ok=True)
INTEGRITY_FILE = MEDIC_DIR / "integrity_registry.json"
HEALTH_FILE = MEDIC_DIR / "health_state.json"
TASK_LOG_FILE = MEDIC_DIR / "task_log.json"
LOCAL_BACKUP_DIR = MEDIC_DIR / "backup"

DEFAULT_REPO_URL = "https://raw.githubusercontent.com/zensynora/zensynora/main"
CORE_FILES = [
    "myclaw/agent.py",
    "myclaw/tools.py",
    "myclaw/config.py",
    "myclaw/memory.py",
    "myclaw/gateway.py",
    "myclaw/knowledge/__init__.py",
    "myclaw/knowledge/storage.py",
    "myclaw/providers/ollama.py",
]

config = None


def set_config(cfg):
    """Set global config reference for Medic Agent."""
    global config
    config = cfg


class MedicAgent:
    """System health agent with hash integrity checking and error recovery."""

    def __init__(self, repo_url: str = DEFAULT_REPO_URL):
        if config and hasattr(config, 'medic'):
            self.repo_url = config.medic.repo_url if config.medic.repo_url else repo_url
            self.enabled = config.medic.enabled
            self.enable_hash_check = config.medic.enable_hash_check
            self.scan_on_startup = config.medic.scan_on_startup
            self.max_loop_iterations = config.medic.max_loop_iterations
        else:
            self.repo_url = repo_url
            self.enabled = True
            self.enable_hash_check = True
            self.scan_on_startup = False
            self.max_loop_iterations = 100
        
        self.medic_dir = MEDIC_DIR
        self.medic_dir.mkdir(parents=True, exist_ok=True)
        self.integrity_file = INTEGRITY_FILE
        self.health_file = HEALTH_FILE
        self.task_log_file = TASK_LOG_FILE
        self._loop_detector = LoopDetector()

        # Evolver engine (v2.0 — capability-evolver integration)
        self._evolver = EvolverEngine()
        self._planner = EvolutionPlanner()
        self._health_history: List[Dict[str, Any]] = []

    def calculate_hash(self, file_path: str) -> Optional[str]:
        """Calculate SHA-256 hash of a file.
        
        Args:
            file_path: Path to file
            
        Returns:
            SHA-256 hash string or None on error
        """
        try:
            path = Path(file_path)
            if not path.exists():
                return None
            
            sha256 = hashlib.sha256()
            with open(path, 'rb') as f:
                for chunk in iter(lambda: f.read(8192), b''):
                    sha256.update(chunk)
            return sha256.hexdigest()
        
        except Exception as e:
            logger.error(f"Error calculating hash for {file_path}: {e}")
            return None

    def check_integrity(self, file_path: Optional[str] = None) -> Dict[str, Any]:
        """Check file integrity against recorded hashes.
        
        Args:
            file_path: Specific file to check, or None for all tracked files
            
        Returns:
            Dict with integrity check results
        """
        if not self.integrity_file.exists():
            return {"status": "no_registry", "message": "Integrity registry not found. Run scan_system() first."}
        
        try:
            registry = json.loads(self.integrity_file.read_text())
            results = {"files": [], "valid": 0, "corrupted": 0, "missing": 0}
            
            files_to_check = [file_path] if file_path else list(registry.keys())
            
            for fp in files_to_check:
                if fp not in registry:
                    continue
                    
                record = registry[fp]
                current_hash = self.calculate_hash(fp)
                
                if current_hash is None:
                    results["missing"] += 1
                    results["files"].append({
                        "path": fp,
                        "status": "missing",
                        "recorded_hash": record.get("hash")
                    })
                elif current_hash != record.get("hash"):
                    results["corrupted"] += 1
                    results["files"].append({
                        "path": fp,
                        "status": "modified",
                        "recorded_hash": record.get("hash"),
                        "current_hash": current_hash
                    })
                else:
                    results["valid"] += 1
                    results["files"].append({
                        "path": fp,
                        "status": "valid"
                    })
            
            results["status"] = "ok" if results["corrupted"] == 0 and results["missing"] == 0 else "issues_found"
            return results
        
        except Exception as e:
            logger.error(f"Error checking integrity: {e}")
            return {"status": "error", "message": str(e)}

    def scan_system(self, files: Optional[List[str]] = None) -> Dict[str, Any]:
        """Scan system files and record their hashes.
        
        Args:
            files: List of file paths to scan, defaults to CORE_FILES
            
        Returns:
            Dict with scan results
        """
        files = files or CORE_FILES
        registry = {}
        scanned = []
        errors = []
        
        for fp in files:
            path = Path(fp)
            if path.exists():
                hash_val = self.calculate_hash(fp)
                if hash_val:
                    registry[fp] = {
                        "hash": hash_val,
                        "last_checked": datetime.now().isoformat(),
                        "status": "valid"
                    }
                    scanned.append(fp)
            else:
                errors.append(f"File not found: {fp}")
        
        self.integrity_file.write_text(json.dumps(registry, indent=2), encoding="utf-8")
        
        return {
            "scanned": len(scanned),
            "errors": errors,
            "registry_updated": True
        }

    def detect_errors(self, file_path: str) -> Dict[str, Any]:
        """Detect syntax and runtime errors in a Python file.
        
        Args:
            file_path: Path to Python file
            
        Returns:
            Dict with error details
        """
        try:
            path = Path(file_path)
            if not path.exists():
                return {"status": "missing", "message": f"File not found: {file_path}"}
            
            code = path.read_text(encoding="utf-8")
            
            try:
                ast.parse(code)
                syntax_ok = True
                syntax_errors = []
            except SyntaxError as e:
                syntax_ok = False
                syntax_errors = [{
                    "line": e.lineno,
                    "column": e.offset,
                    "message": str(e)
                }]
            
            return {
                "file": file_path,
                "syntax_valid": syntax_ok,
                "syntax_errors": syntax_errors
            }
        
        except Exception as e:
            logger.error(f"Error detecting errors in {file_path}: {e}")
            return {"status": "error", "message": str(e)}

    def validate_modification(self, proposed_change: str, target_file: str) -> Dict[str, Any]:
        """Validate a proposed code modification before applying.
        
        Args:
            proposed_change: New code content
            target_file: File to be modified
            
        Returns:
            Dict with validation results
        """
        issues = []
        
        try:
            ast.parse(proposed_change)
        except SyntaxError as e:
            issues.append(f"Syntax error: {e}")
        
        forbidden_imports = {"os", "sys", "subprocess", "shutil", "socket", "urllib", "http", "pty", "commands"}
        forbidden_calls = {"eval", "exec", "open", "__import__", "globals", "locals", "compile"}
        
        try:
            tree = ast.parse(proposed_change)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split('.')[0] in forbidden_imports:
                            issues.append(f"Forbidden import: {alias.name}")
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.module.split('.')[0] in forbidden_imports:
                        issues.append(f"Forbidden import from: {node.module}")
                elif isinstance(node, ast.Call):
                    if isinstance(node.func, ast.Name) and node.func.id in forbidden_calls:
                        issues.append(f"Forbidden call: {node.func.id}")
        except Exception as e:
            issues.append(f"AST parsing error: {e}")
        
        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "file": target_file
        }

    async def fetch_from_github(self, file_path: str, branch: str = "main") -> Optional[str]:
        """Fetch a file from GitHub repository.

        Uses httpx for async HTTP (cross-platform, no subprocess needed).

        Args:
            file_path: Path within repo (e.g., 'myclaw/agent.py')
            branch: Branch name (default: main)

        Returns:
            File content as string or None on error
        """
        try:
            import httpx
            url = f"{self.repo_url.rstrip('/')}/{branch}/{file_path}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(url)
                if response.status_code == 200:
                    return response.text
                logger.warning("GitHub fetch returned %s for %s", response.status_code, url)
                return None
        except ImportError:
            # Fallback to synchronous urllib if httpx is unavailable
            try:
                from urllib.request import urlopen
                url = f"{self.repo_url.rstrip('/')}/{branch}/{file_path}"
                with urlopen(url, timeout=10) as response:
                    return response.read().decode("utf-8")
            except Exception as e:
                logger.error(f"Error fetching from GitHub (fallback): {e}")
                return None
        except Exception as e:
            logger.error(f"Error fetching from GitHub: {e}")
            return None

    async def recover_from_github(self, file_path: str, branch: str = "main") -> Dict[str, Any]:
        """Recover a corrupted or missing file from GitHub.
        
        Args:
            file_path: Path to recover
            branch: GitHub branch
            
        Returns:
            Dict with recovery results
        """
        try:
            content = await self.fetch_from_github(file_path, branch)
            if content is None:
                return {"success": False, "message": "Failed to fetch from GitHub"}
            
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            
            new_hash = self.calculate_hash(file_path)
            return {
                "success": True,
                "file": file_path,
                "hash": new_hash,
                "source": f"github/{branch}"
            }
        
        except Exception as e:
            logger.error(f"Error recovering file: {e}")
            return {"success": False, "message": str(e)}

    def recover_from_local(self, file_path: str) -> Dict[str, Any]:
        """Recover a corrupted or missing file from local backup.
        
        Args:
            file_path: Path to recover
            
        Returns:
            Dict with recovery results
        """
        try:
            backup_path = LOCAL_BACKUP_DIR / file_path
            if not backup_path.exists():
                return {"success": False, "message": f"No local backup found for {file_path}"}
            
            content = backup_path.read_text(encoding="utf-8")
            
            target_path = Path(file_path)
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            
            new_hash = self.calculate_hash(file_path)
            return {
                "success": True,
                "file": file_path,
                "hash": new_hash,
                "source": "local_backup"
            }
        
        except Exception as e:
            logger.error(f"Error recovering from local: {e}")
            return {"success": False, "message": str(e)}

    def create_local_backup(self, file_path: str) -> Dict[str, Any]:
        """Create a local backup of a file.
        
        Args:
            file_path: Path to backup
            
        Returns:
            Dict with backup results
        """
        try:
            source = Path(file_path)
            if not source.exists():
                return {"success": False, "message": f"Source file not found: {file_path}"}
            
            LOCAL_BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            
            backup_path = LOCAL_BACKUP_DIR / file_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            
            content = source.read_text(encoding="utf-8")
            backup_path.write_text(content, encoding="utf-8")
            
            return {
                "success": True,
                "file": file_path,
                "backup_path": str(backup_path)
            }
        
        except Exception as e:
            logger.error(f"Error creating local backup: {e}")
            return {"success": False, "message": str(e)}

    def record_task(self, task_name: str, duration: float, success: bool = True) -> None:
        """Record task execution for analytics.
        
        Args:
            task_name: Name of the task
            duration: Execution time in seconds
            success: Whether task succeeded
        """
        log = []
        if self.task_log_file.exists():
            try:
                log = json.loads(self.task_log_file.read_text())
            except Exception:
                pass
        
        log.append({
            "task": task_name,
            "duration": duration,
            "success": success,
            "timestamp": datetime.now().isoformat()
        })
        
        if len(log) > 1000:
            log = log[-1000:]
        
        self.task_log_file.write_text(json.dumps(log, indent=2), encoding="utf-8")

    def get_task_analytics(self) -> Dict[str, Any]:
        """Get analytics from task execution history.
        
        Returns:
            Dict with task statistics
        """
        if not self.task_log_file.exists():
            return {"status": "no_data", "message": "No task history found"}
        
        try:
            log = json.loads(self.task_log_file.read_text())
            
            if not log:
                return {"status": "empty", "tasks": []}
            
            task_stats = {}
            for entry in log:
                name = entry.get("task", "unknown")
                if name not in task_stats:
                    task_stats[name] = {"count": 0, "total_duration": 0, "successes": 0}
                task_stats[name]["count"] += 1
                task_stats[name]["total_duration"] += entry.get("duration", 0)
                if entry.get("success"):
                    task_stats[name]["successes"] += 1
            
            for name, stats in task_stats.items():
                stats["avg_duration"] = stats["total_duration"] / stats["count"] if stats["count"] > 0 else 0
                stats["success_rate"] = stats["successes"] / stats["count"] if stats["count"] > 0 else 0
            
            return {"status": "ok", "tasks": task_stats, "total_entries": len(log)}
        
        except Exception as e:
            logger.error(f"Error getting task analytics: {e}")
            return {"status": "error", "message": str(e)}

    def detect_loop(self, pattern: str, max_iterations: int = 100) -> bool:
        """Check if a pattern suggests infinite loop.
        
        Args:
            pattern: Pattern to detect
            max_iterations: Maximum allowed iterations
            
        Returns:
            True if loop detected
        """
        return self._loop_detector.is_looping(pattern, max_iterations)

    def check_execution(self, execution_id: str, max_calls: int = 50) -> Dict[str, Any]:
        """Check execution state for infinite loop prevention.
        
        Args:
            execution_id: Unique identifier for this execution
            max_calls: Maximum allowed calls
            
        Returns:
            Dict with check results
        """
        is_looping, count = self._loop_detector.check(execution_id, max_calls)
        return {
            "allowed": not is_looping,
            "count": count,
            "max_allowed": max_calls,
            "should_stop": is_looping
        }

    def handle_timeout(self, execution_id: str, timeout_seconds: int = 30) -> Dict[str, Any]:
        """Handle execution timeout.
        
        Args:
            execution_id: Unique identifier for this execution
            timeout_seconds: Timeout threshold
            
        Returns:
            Dict with timeout handling results
        """
        self._loop_detector.clear(execution_id)
        return {
            "cleared": True,
            "execution_id": execution_id,
            "timeout_seconds": timeout_seconds
        }

    def get_health_report(self) -> str:
        """Generate a formatted health report.
        
        Returns:
            Formatted health status string
        """
        lines = ["🏥 ZenSynora Health Report", "", f"Timestamp: {datetime.now().isoformat()}", ""]
        
        integrity = self.check_integrity()
        lines.append("File Integrity:")
        lines.append(f"  Valid: {integrity.get('valid', 0)}")
        lines.append(f"  Modified: {integrity.get('corrupted', 0)}")
        lines.append(f"  Missing: {integrity.get('missing', 0)}")
        
        if integrity.get("files"):
            issues = [f for f in integrity["files"] if f["status"] != "valid"]
            if issues:
                lines.append("")
                lines.append("⚠️ Issues Detected:")
                for issue in issues:
                    lines.append(f"  - {issue['path']}: {issue['status']}")
        
        analytics = self.get_task_analytics()
        if analytics.get("tasks"):
            lines.append("")
            lines.append("Task Analytics:")
            for name, stats in list(analytics["tasks"].items())[:5]:
                rate = stats.get("success_rate", 0) * 100
                lines.append(f"  - {name}: {stats['count']} runs, {rate:.1f}% success, {stats.get('avg_duration', 0):.2f}s avg")
        
        lines.append("")
        lines.append("Status: " + ("✅ Healthy" if integrity.get("valid", 0) > 0 and not integrity.get("corrupted") else "⚠️ Needs Attention"))

        return "\n".join(lines)

    # ─── Evolver Engine Integration (v2.0) ─────────────────

    def analyze_logs_deterministic(self, logs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Deterministic log analysis inspired by capability-evolver.

        Processes structured log entries through a multi-pass analysis engine
        (pattern detection → health scoring → recommendation generation).

        Args:
            logs: List of dicts with keys: timestamp, level, message, context

        Returns:
            Structured analysis with patterns, health_score, recommendations, summary
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

        result = self._evolver.analyze(entries)

        # Record health history
        self._health_history.append({
            "timestamp": datetime.now().isoformat(),
            "score": result.health_score,
            "pattern_count": len(result.patterns),
        })
        # Keep last 100 entries
        if len(self._health_history) > 100:
            self._health_history = self._health_history[-100:]

        return result.to_dict()

    def generate_evolution_plan(
        self,
        logs: List[Dict[str, Any]],
        strategy: str = "auto",
        target_file: str = "",
    ) -> Dict[str, Any]:
        """
        Generate a structured evolution proposal from log analysis.

        Args:
            logs: Log entries to analyze
            strategy: Evolution strategy (auto, balanced, innovate, harden, repair-only)
            target_file: Optional file to focus analysis on

        Returns:
            Evolution proposal with prioritized recommendations
        """
        analysis_dict = self.analyze_logs_deterministic(logs)

        # Reconstruct AnalysisResult for planner
        analysis = AnalysisResult(
            patterns=[
                Pattern(
                    type=p.get("type", "error"),
                    severity=p.get("severity", "low"),
                    description=p.get("description", ""),
                    occurrences=p.get("occurrences", 0),
                    first_seen=p.get("first_seen", ""),
                    last_seen=p.get("last_seen", ""),
                    affected_files=p.get("affected_files", []),
                )
                for p in analysis_dict.get("patterns", [])
            ],
            health_score=analysis_dict.get("health_score", 100),
            recommendations=[
                Recommendation(
                    priority=r.get("priority", "medium"),
                    category=r.get("category", "stability"),
                    description=r.get("description", ""),
                    affected_files=r.get("affected_files", []),
                    suggested_approach=r.get("suggested_approach", ""),
                )
                for r in analysis_dict.get("recommendations", [])
            ],
            summary=analysis_dict.get("summary", {}),
        )

        return self._planner.generate_proposal(
            analysis, strategy, target_file or None
        )

    def get_unified_health_score(self) -> Dict[str, Any]:
        """
        Calculate unified health score combining all subsystems.

        Components:
          - File integrity   (0-25 points)
          - Task performance (0-25 points)
          - Log health       (0-25 points)
          - Syntax health    (0-25 points)

        Returns:
            Dict with overall_score, grade, components, trend, history
        """
        # File integrity component (0-25 points)
        integrity = self.check_integrity()
        total_files = (
            integrity.get("valid", 0)
            + integrity.get("corrupted", 0)
            + integrity.get("missing", 0)
        )
        integrity_score = (
            (integrity.get("valid", 0) / max(total_files, 1)) * 25
            if total_files > 0 else 25
        )

        # Task performance component (0-25 points)
        tasks = self.get_task_analytics()
        task_score = 0
        if tasks.get("status") == "ok" and tasks.get("tasks"):
            avg_success = sum(
                s.get("success_rate", 0) for s in tasks["tasks"].values()
            ) / max(len(tasks["tasks"]), 1)
            task_score = avg_success * 25
        else:
            task_score = 25  # no data = assume good

        # Log health component (0-25 points) — from last analysis
        log_score = 25
        if self._health_history:
            last_health = self._health_history[-1]["score"]
            log_score = (last_health / 100) * 25

        # Syntax/component health (0-25 points) — scan core files
        syntax_score = 25
        errors_found = 0
        files_checked = 0
        for fp in CORE_FILES:
            result = self.detect_errors(fp)
            files_checked += 1
            if not result.get("syntax_valid", True):
                errors_found += len(result.get("syntax_errors", []))
        if files_checked > 0:
            syntax_score = max(0, 25 - (errors_found * 5))

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

    @staticmethod
    def _score_to_grade(score: int) -> str:
        """Convert numeric score to letter grade."""
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"


class LoopDetector:
    """Detects and prevents infinite loops in execution."""

    def __init__(self, max_iterations: int = 100):
        self._executions: Dict[str, List[float]] = {}
        self._max_iterations = max_iterations

    def is_looping(self, pattern: str, max_iterations: Optional[int] = None) -> bool:
        """Check if pattern has exceeded max iterations."""
        max_iter = max_iterations or self._max_iterations

        if pattern not in self._executions:
            self._executions[pattern] = []

        self._executions[pattern].append(datetime.now().timestamp())

        cutoff = datetime.now().timestamp() - 60
        recent = [t for t in self._executions[pattern] if t > cutoff]
        self._executions[pattern] = recent

        return len(recent) > max_iter

    def check(self, execution_id: str, max_calls: int = 50) -> Tuple[bool, int]:
        """Check if execution has exceeded call limit."""
        if execution_id not in self._executions:
            self._executions[execution_id] = []

        # FIXED: Increment the counter
        self._executions[execution_id].append(datetime.now().timestamp())

        count = len(self._executions[execution_id])
        return count >= max_calls, count

    def record_iteration(self, pattern: str) -> bool:
        """Record an iteration and return whether still allowed."""
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


def check_system_health() -> str:
    """Check overall system health status.
    
    Returns:
        Formatted health report
    """
    medic = MedicAgent()
    return medic.get_health_report()


def verify_file_integrity(file_path: Optional[str] = None) -> str:
    """Verify file integrity against recorded hashes.
    
    Args:
        file_path: Optional specific file to check
        
    Returns:
        Integrity check results
    """
    medic = MedicAgent()
    result = medic.check_integrity(file_path)
    
    if result.get("status") == "no_registry":
        return "⚠️ No integrity registry found. Run scan_system() first to create baseline."
    
    lines = [f"📋 Integrity Check {'for ' + file_path if file_path else 'for all files'}", ""]
    lines.append(f"Valid: {result.get('valid', 0)}")
    lines.append(f"Modified: {result.get('corrupted', 0)}")
    lines.append(f"Missing: {result.get('missing', 0)}")
    
    if result.get("files"):
        for f in result["files"]:
            if f["status"] != "valid":
                status_icon = "❌" if f["status"] == "missing" else "⚠️"
                lines.append(f"{status_icon} {f['path']}: {f['status']}")
    
    return "\n".join(lines)


async def recover_file(file_path: str, source: str = "github") -> str:
    """Recover a corrupted or missing file.
    
    Args:
        file_path: Path to recover
        source: Recovery source ('github' or 'local')
    
    Returns:
        Recovery result
    """
    medic = MedicAgent()
    
    if source == "github":
        result = await medic.recover_from_github(file_path)
        if result.get("success"):
            return f"✅ File recovered from GitHub: {file_path}\nNew hash: {result.get('hash', 'unknown')}"
        return f"❌ Recovery failed: {result.get('message', 'Unknown error')}"
    
    if source == "local":
        result = medic.recover_from_local(file_path)
        if result.get("success"):
            return f"✅ File recovered from local backup: {file_path}\nNew hash: {result.get('hash', 'unknown')}"
        return f"❌ Recovery failed: {result.get('message', 'Unknown error')}"
    
    return f"⚠️ Unknown source '{source}'. Use 'github' or 'local' for recovery."


def create_backup(file_path: str) -> str:
    """Create a local backup of a file.
    
    Args:
        file_path: Path to backup
    
    Returns:
        Backup result
    """
    medic = MedicAgent()
    result = medic.create_local_backup(file_path)
    
    if result.get("success"):
        return f"✅ Backup created: {file_path}\nBackup location: {result.get('backup_path')}"
    return f"❌ Backup failed: {result.get('message', 'Unknown error')}"


def list_backups() -> str:
    """List all local backups.
    
    Returns:
        Formatted list of backups
    """
    medic = MedicAgent()
    
    if not LOCAL_BACKUP_DIR.exists():
        return "📁 No backups found. No backup directory exists."
    
    backups = list(LOCAL_BACKUP_DIR.rglob("*"))
    files = [b for b in backups if b.is_file()]
    
    if not files:
        return "📁 No backups found. Backup directory is empty."
    
    lines = ["📁 Local Backups:", ""]
    for f in files:
        rel_path = f.relative_to(LOCAL_BACKUP_DIR)
        lines.append(f"  • {rel_path}")
    
    return "\n".join(lines)


def get_health_report() -> str:
    """Get formatted health report.
    
    Returns:
        Health report string
    """
    medic = MedicAgent()
    return medic.get_health_report()


def validate_modification(proposed_change: str, target_file: str) -> str:
    """Validate a proposed code modification.
    
    Args:
        proposed_change: New code content
        target_file: File to be modified
    
    Returns:
        Validation results
    """
    medic = MedicAgent()
    result = medic.validate_modification(proposed_change, target_file)
    
    if result.get("valid"):
        return f"✅ Modification validated for {target_file}"
    
    lines = [f"❌ Validation failed for {target_file}", "", "Issues found:"]
    for issue in result.get("issues", []):
        lines.append(f"  - {issue}")
    
    return "\n".join(lines)


def record_task_execution(task_name: str, duration: float, success: bool = True) -> str:
    """Record a task execution for analytics.
    
    Args:
        task_name: Name of the task
        duration: Execution time in seconds
        success: Whether task succeeded
    
    Returns:
        Recording confirmation
    """
    medic = MedicAgent()
    medic.record_task(task_name, duration, success)
    status = "✅" if success else "❌"
    return f"{status} Task '{task_name}' recorded: {duration:.2f}s"


def get_task_analytics() -> str:
    """Get task execution analytics.
    
    Returns:
        Formatted analytics report
    """
    medic = MedicAgent()
    result = medic.get_task_analytics()
    
    if result.get("status") in ("no_data", "empty"):
        return "📊 No task data available yet."
    
    lines = ["📊 Task Analytics", ""]
    for name, stats in result.get("tasks", {}).items():
        rate = stats.get("success_rate", 0) * 100
        avg = stats.get("avg_duration", 0)
        lines.append(f"  • {name}")
        lines.append(f"    Runs: {stats['count']} | Success: {rate:.1f}% | Avg: {avg:.2f}s")
    
    lines.append("")
    lines.append(f"Total entries: {result.get('total_entries', 0)}")
    
    return "\n".join(lines)


def enable_hash_check(enabled: bool = True) -> str:
    """Enable or disable hash checking.
    
    Args:
        enabled: True to enable, False to disable
    
    Returns:
        Status message
    """
    global config
    if config and hasattr(config, 'medic'):
        config.medic.enable_hash_check = enabled
        return f"✅ Hash checking {'enabled' if enabled else 'disabled'}"
    return f"ℹ️ Hash checking {'enabled' if enabled else 'disabled'} (config update pending restart)"


def scan_files(files: str = "") -> str:
    """Scan files and record their hashes for integrity checking.
    
    Args:
        files: Comma-separated list of files to scan, or empty for defaults
    
    Returns:
        Scan results
    """
    medic = MedicAgent()
    file_list = [f.strip() for f in files.split(",")] if files else None
    result = medic.scan_system(file_list)
    
    return f"📊 Scan complete:\n  Scanned: {result.get('scanned', 0)} files\n  Errors: {len(result.get('errors', []))}\n  Registry updated: {result.get('registry_updated', False)}"


def detect_errors_in_file(file_path: str) -> str:
    """Detect syntax errors in a Python file.
    
    Args:
        file_path: Path to Python file
    
    Returns:
        Error detection results
    """
    medic = MedicAgent()
    result = medic.detect_errors(file_path)
    
    if result.get("status") == "missing":
        return f"❌ {result.get('message')}"
    
    if result.get("syntax_valid"):
        return f"✅ {file_path}: No syntax errors found"
    
    lines = [f"❌ Syntax errors in {file_path}:"]
    for err in result.get("syntax_errors", []):
        lines.append(f"  Line {err['line']}: {err['message']}")
    
    return "\n".join(lines)


def prevent_infinite_loop() -> str:
    """Get status of infinite loop prevention.
    
    Returns:
        Loop prevention status
    """
    medic = MedicAgent()
    state = medic.check_execution("current", max_calls=50)
    
    if state["should_stop"]:
        return "⚠️ Execution limit reached. Stopping to prevent infinite loop."
    return f"✅ Loop prevention active. Calls: {state['count']}/{state['max_allowed']}"


VIRUSTOTAL_API_URL = "https://www.virustotal.com/api/v3"


def check_file_virustotal(file_path: str, api_key: str = "") -> str:
    """Check a file against VirusTotal for malware detection.
    
    Args:
        file_path: Path to the file to check
        api_key: VirusTotal API key (optional, can be set in config)
    
    Returns:
        Detection report
    """
    if not api_key:
        if config and hasattr(config, 'medic'):
            api_key = getattr(config.medic, 'virustotal_api_key', "")
    
    if not api_key:
        return "⚠️ VirusTotal API key not configured. Set virustotal_api_key in config or pass as parameter."
    
    try:
        import requests
        
        path = Path(file_path)
        if not path.exists():
            return f"❌ File not found: {file_path}"
        
        # Calculate file hash
        hash_value = hashlib.sha256()
        with open(path, 'rb') as f:
            for chunk in iter(lambda: f.read(8192), b''):
                hash_value.update(chunk)
        file_hash = hash_value.hexdigest()
        
        # Query VirusTotal
        headers = {"x-apikey": api_key}
        response = requests.get(
            f"{VIRUSTOTAL_API_URL}/files/{file_hash}",
            headers=headers,
            timeout=30
        )
        
        if response.status_code == 200:
            data = response.json()
            stats = data.get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
            
            malicious = stats.get("malicious", 0)
            suspicious = stats.get("suspicious", 0)
            harmless = stats.get("harmless", 0)
            undetected = stats.get("undetected", 0)
            total = malicious + suspicious + harmless + undetected
            
            if total == 0:
                return f"🔍 No scan results for {file_path}. File may be new to VirusTotal."
            
            detection_ratio = (malicious + suspicious) / total * 100
            
            lines = [
                f"🔍 VirusTotal Report for {path.name}",
                f"SHA256: {file_hash[:16]}...",
                "",
                f"Malicious: {malicious}",
                f"Suspicious: {suspicious}",
                f"Harmless: {harmless}",
                f"Undetected: {undetected}",
                "",
            ]
            
            if detection_ratio > 50:
                lines.append(f"⚠️ HIGH RISK: {detection_ratio:.1f}% of engines detected threats!")
                return "\n".join(lines)
            elif detection_ratio > 0:
                lines.append(f"⚡ LOW RISK: {detection_ratio:.1f}% of engines detected threats")
                return "\n".join(lines)
            else:
                lines.append(f"✅ CLEAN: No threats detected")
                return "\n".join(lines)
        
        elif response.status_code == 404:
            return f"🔍 File not found in VirusTotal database. It may be a new file or not yet scanned."
        else:
            return f"❌ VirusTotal API error: {response.status_code}"
    
    except Exception as e:
        logger.error(f"Error checking VirusTotal: {e}")
        return f"❌ Error checking VirusTotal: {e}"


# ─── Evolver Engine Wrapper Functions (v2.0) ───────────────

def analyze_logs_deterministic(logs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Analyze logs using the deterministic evolver engine.

    Args:
        logs: List of dicts with keys: timestamp, level, message, context

    Returns:
        Structured analysis with patterns, health_score, recommendations
    """
    medic = MedicAgent()
    return medic.analyze_logs_deterministic(logs)


def generate_evolution_plan(
    logs: List[Dict[str, Any]],
    strategy: str = "auto",
    target_file: str = "",
) -> Dict[str, Any]:
    """
    Generate a structured evolution proposal from log analysis.

    Args:
        logs: Log entries to analyze
        strategy: Evolution strategy (auto, balanced, innovate, harden, repair-only)
        target_file: Optional file to focus analysis on

    Returns:
        Evolution proposal with prioritized recommendations
    """
    medic = MedicAgent()
    return medic.generate_evolution_plan(logs, strategy, target_file)


def get_unified_health_score() -> Dict[str, Any]:
    """
    Get unified health score combining all subsystems.

    Returns:
        Dict with overall_score, grade, components, trend, history
    """
    medic = MedicAgent()
    return medic.get_unified_health_score()


def get_detailed_health_report() -> str:
    """
    Get a detailed health report including unified score and components.

    Returns:
        Formatted health report string
    """
    medic = MedicAgent()
    health = medic.get_unified_health_score()

    lines = [
        "🏥 ZenSynora Detailed Health Report",
        "",
        f"Timestamp: {datetime.now().isoformat()}",
        f"Overall Score: {health['overall_score']}/100 (Grade: {health['grade']})",
        f"Trend: {health['trend'].capitalize()}",
        "",
        "Component Breakdown:",
        f"  • File Integrity:   {health['components']['integrity']}/25",
        f"  • Task Performance: {health['components']['tasks']}/25",
        f"  • Log Health:       {health['components']['logs']}/25",
        f"  • Syntax Health:    {health['components']['syntax']}/25",
        "",
    ]

    # Append legacy report
    lines.append(medic.get_health_report())
    return "\n".join(lines)


__all__ = [
    # Classes
    "MedicAgent",
    "LoopDetector",
    # Legacy functions
    "check_system_health",
    "verify_file_integrity",
    "recover_file",
    "create_backup",
    "list_backups",
    "get_health_report",
    "validate_modification",
    "record_task_execution",
    "get_task_analytics",
    "enable_hash_check",
    "scan_files",
    "detect_errors_in_file",
    "prevent_infinite_loop",
    "check_file_virustotal",
    # Evolver v2.0 functions
    "analyze_logs_deterministic",
    "generate_evolution_plan",
    "get_unified_health_score",
    "get_detailed_health_report",
]