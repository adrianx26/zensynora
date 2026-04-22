"""
Medic Agent Change Management Extension

This module extends the Medic Agent with comprehensive change management capabilities:
- Change plan creation with approval workflow
- Log ingestion and anomaly detection
- Safe change execution with rollback
- Audit logging and history
- Scheduled reviews and continuous monitoring
"""

import json
import hashlib
import asyncio
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, TypedDict, cast
from enum import Enum
from datetime import datetime, timedelta
import time

from .medic_evolver import (
    EvolverEngine,
    LogEntry,
    EvolutionStrategy,
    Priority as EvolverPriority,
    Category as EvolverCategory,
)

logger = logging.getLogger(__name__)

MEDIC_DIR = Path.home() / ".myclaw" / "medic"
MEDIC_DIR.mkdir(parents=True, exist_ok=True)
LOCAL_BACKUP_DIR = MEDIC_DIR / "backup"


class ChangeStatus(Enum):
    """Status of a change request."""
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    ROLLED_BACK = "rolled_back"


class ChangePriority(Enum):
    """Priority levels for changes."""
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ChangeType(Enum):
    """Types of changes."""
    CONFIG = "configuration"
    CODE = "code"
    SECURITY = "security"
    PATCH = "patch"
    HOTFIX = "hotfix"


class ChangePlan(TypedDict, total=False):
    """Structure for a change plan."""
    change_id: str
    title: str
    description: str
    rationale: str
    change_type: str
    priority: str
    affected_components: List[str]
    proposed_changes: Dict[str, Any]
    potential_risks: List[str]
    rollback_steps: List[str]
    success_criteria: List[str]
    approval_required: bool
    approved_by: Optional[str]
    approved_at: Optional[str]
    status: str
    created_at: str
    executed_at: Optional[str]
    completed_at: Optional[str]
    execution_result: Optional[Dict[str, Any]]
    audit_log: List[Dict[str, Any]]


class ChangeManagementSystem:
    """
    Comprehensive change management system for the Medic Agent.
    """
    
    def __init__(self):
        self.medic_dir = MEDIC_DIR
        self.changes_dir = self.medic_dir / "changes"
        self.changes_dir.mkdir(parents=True, exist_ok=True)
        self.audit_log_file = self.medic_dir / "audit_log.json"
        
        # Configuration
        self.auto_approve_low_risk = True
        self.auto_approve_config = True
        self.maintenance_window_start = None
        self.maintenance_window_end = None
        
        # Active changes tracking
        self._active_changes: Dict[str, ChangePlan] = {}
        self._change_lock = asyncio.Lock()
        
        self._load_pending_changes()
    
    def _load_pending_changes(self) -> None:
        """Load pending changes from disk."""
        if not self.changes_dir.exists():
            return
        
        for change_file in self.changes_dir.glob("change_*.json"):
            try:
                change_data = json.loads(change_file.read_text())
                if change_data.get("status") in ["pending", "approved", "in_progress"]:
                    self._active_changes[change_data["change_id"]] = change_data
            except Exception as e:
                logger.error(f"Error loading change file {change_file}: {e}")
    
    def _generate_change_id(self) -> str:
        """Generate unique change ID."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        random_suffix = hashlib.md5(str(time.time()).encode()).hexdigest()[:6]
        return f"CHG_{timestamp}_{random_suffix}"
    
    def create_change_plan(
        self,
        title: str,
        description: str,
        rationale: str,
        change_type: ChangeType,
        priority: ChangePriority,
        affected_components: List[str],
        proposed_changes: Dict[str, Any],
        risks: Optional[List[str]] = None,
        rollback_steps: Optional[List[str]] = None,
        success_criteria: Optional[List[str]] = None
    ) -> ChangePlan:
        """Create a comprehensive change plan."""
        change_id = self._generate_change_id()
        
        if not rollback_steps:
            rollback_steps = self._generate_rollback_steps(proposed_changes)
        
        if not success_criteria:
            success_criteria = self._generate_success_criteria(affected_components)
        
        approval_required = self._requires_approval(change_type, priority)
        
        plan: ChangePlan = {
            "change_id": change_id,
            "title": title,
            "description": description,
            "rationale": rationale,
            "change_type": change_type.value,
            "priority": priority.value,
            "affected_components": affected_components,
            "proposed_changes": proposed_changes,
            "potential_risks": risks or ["Unknown - assessment needed"],
            "rollback_steps": rollback_steps,
            "success_criteria": success_criteria,
            "approval_required": approval_required,
            "approved_by": None,
            "approved_at": None,
            "status": ChangeStatus.PENDING.value,
            "created_at": datetime.now().isoformat(),
            "executed_at": None,
            "completed_at": None,
            "execution_result": None,
            "audit_log": [{
                "timestamp": datetime.now().isoformat(),
                "action": "created",
                "details": "Change plan created"
            }]
        }
        
        self._save_change(plan)
        
        if not approval_required:
            plan = self.approve_change(change_id, "auto_approval_system")
        
        logger.info(f"Change plan created: {change_id} - {title}")
        return plan
    
    def _generate_rollback_steps(self, proposed_changes: Dict[str, Any]) -> List[str]:
        """Generate rollback steps based on proposed changes."""
        steps = []
        for file_path in proposed_changes.keys():
            steps.append(f"Restore {file_path} from backup or GitHub")
            steps.append(f"Verify {file_path} integrity after restore")
        steps.append("Restart affected services if needed")
        steps.append("Verify system health")
        return steps
    
    def _generate_success_criteria(self, affected_components: List[str]) -> List[str]:
        """Generate success criteria based on affected components."""
        criteria = []
        for component in affected_components:
            criteria.append(f"{component} is operational")
            criteria.append(f"{component} passes health checks")
        criteria.append("No errors in logs")
        criteria.append("System performance within normal parameters")
        return criteria
    
    def _requires_approval(self, change_type: ChangeType, priority: ChangePriority) -> bool:
        """Determine if change requires manual approval."""
        if priority in [ChangePriority.CRITICAL, ChangePriority.HIGH]:
            return True
        if change_type == ChangeType.SECURITY:
            return True
        if change_type == ChangeType.CODE:
            return True
        if change_type == ChangeType.CONFIG and priority == ChangePriority.LOW:
            return False if self.auto_approve_config else True
        if change_type in [ChangeType.PATCH, ChangeType.HOTFIX]:
            return not self.auto_approve_low_risk
        return True
    
    def _save_change(self, plan: ChangePlan) -> None:
        """Save change plan to disk."""
        change_file = self.changes_dir / f"change_{plan['change_id']}.json"
        change_file.write_text(json.dumps(plan, indent=2, default=str), encoding="utf-8")
    
    def approve_change(self, change_id: str, approved_by: str) -> Optional[ChangePlan]:
        """Approve a change plan."""
        change_file = self.changes_dir / f"change_{change_id}.json"
        if not change_file.exists():
            return None
        
        try:
            plan = json.loads(change_file.read_text())
            plan["status"] = ChangeStatus.APPROVED.value
            plan["approved_by"] = approved_by
            plan["approved_at"] = datetime.now().isoformat()
            plan["audit_log"].append({
                "timestamp": datetime.now().isoformat(),
                "action": "approved",
                "approved_by": approved_by
            })
            
            self._save_change(plan)
            logger.info(f"Change {change_id} approved by {approved_by}")
            return plan
        except Exception as e:
            logger.error(f"Error approving change {change_id}: {e}")
            return None
    
    def reject_change(self, change_id: str, rejected_by: str, reason: str) -> Optional[ChangePlan]:
        """Reject a change plan."""
        change_file = self.changes_dir / f"change_{change_id}.json"
        if not change_file.exists():
            return None
        
        try:
            plan = json.loads(change_file.read_text())
            plan["status"] = ChangeStatus.REJECTED.value
            plan["audit_log"].append({
                "timestamp": datetime.now().isoformat(),
                "action": "rejected",
                "rejected_by": rejected_by,
                "reason": reason
            })
            
            self._save_change(plan)
            logger.info(f"Change {change_id} rejected by {rejected_by}: {reason}")
            return plan
        except Exception as e:
            logger.error(f"Error rejecting change {change_id}: {e}")
            return None
    
    async def execute_change(self, change_id: str, dry_run: bool = False) -> Dict[str, Any]:
        """Execute an approved change plan."""
        async with self._change_lock:
            change_file = self.changes_dir / f"change_{change_id}.json"
            if not change_file.exists():
                return {"success": False, "error": "Change not found"}
            
            try:
                plan = json.loads(change_file.read_text())
                
                if plan["status"] != ChangeStatus.APPROVED.value:
                    return {"success": False, "error": f"Change not approved. Status: {plan['status']}"}
                
                if not self._in_maintenance_window():
                    return {"success": False, "error": "Outside maintenance window"}
                
                plan["status"] = ChangeStatus.IN_PROGRESS.value
                plan["executed_at"] = datetime.now().isoformat()
                plan["audit_log"].append({
                    "timestamp": datetime.now().isoformat(),
                    "action": "execution_started",
                    "dry_run": dry_run
                })
                self._save_change(plan)
                
                backups = {}
                if not dry_run:
                    backups = await self._create_backups(plan["proposed_changes"])
                
                results = []
                for file_path, new_content in plan["proposed_changes"].items():
                    if dry_run:
                        results.append({"file": file_path, "action": "dry_run", "success": True})
                    else:
                        result = await self._apply_change(file_path, new_content)
                        results.append(result)
                
                success = all(r.get("success") for r in results)
                
                if success:
                    plan["status"] = ChangeStatus.COMPLETED.value
                    plan["completed_at"] = datetime.now().isoformat()
                    plan["execution_result"] = {"success": True, "results": results}
                else:
                    if not dry_run:
                        await self._rollback_change(plan, backups)
                    plan["status"] = ChangeStatus.FAILED.value
                    plan["execution_result"] = {"success": False, "results": results}
                
                plan["audit_log"].append({
                    "timestamp": datetime.now().isoformat(),
                    "action": "execution_completed",
                    "success": success,
                    "dry_run": dry_run
                })
                
                self._save_change(plan)
                self._write_audit_log(plan)
                
                return plan["execution_result"]
                
            except Exception as e:
                logger.error(f"Error executing change {change_id}: {e}")
                return {"success": False, "error": str(e)}
    
    async def _create_backups(self, proposed_changes: Dict[str, Any]) -> Dict[str, str]:
        """Create backups of files before modification."""
        backups = {}
        for file_path in proposed_changes.keys():
            path = Path(file_path)
            if path.exists():
                backup_path = LOCAL_BACKUP_DIR / f"{file_path}.{int(time.time())}.bak"
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                backup_path.write_text(path.read_text(), encoding="utf-8")
                backups[file_path] = str(backup_path)
        return backups
    
    async def _apply_change(self, file_path: str, new_content: str) -> Dict[str, Any]:
        """Apply a single file change."""
        try:
            path = Path(file_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_content, encoding="utf-8")
            return {"file": file_path, "action": "modified", "success": True}
        except Exception as e:
            logger.error(f"Error applying change to {file_path}: {e}")
            return {"file": file_path, "action": "failed", "success": False, "error": str(e)}
    
    async def _rollback_change(self, plan: ChangePlan, backups: Dict[str, str]) -> bool:
        """Rollback changes using backups."""
        try:
            logger.warning(f"Rolling back change {plan['change_id']}")
            
            for file_path, backup_path in backups.items():
                if Path(backup_path).exists():
                    content = Path(backup_path).read_text(encoding="utf-8")
                    Path(file_path).write_text(content, encoding="utf-8")
            
            plan["status"] = ChangeStatus.ROLLED_BACK.value
            plan["audit_log"].append({
                "timestamp": datetime.now().isoformat(),
                "action": "rolled_back"
            })
            self._save_change(plan)
            
            return True
        except Exception as e:
            logger.error(f"Error rolling back change {plan['change_id']}: {e}")
            return False
    
    def _in_maintenance_window(self) -> bool:
        """Check if current time is within maintenance window."""
        if self.maintenance_window_start is None or self.maintenance_window_end is None:
            return True
        
        current_hour = datetime.now().hour
        if self.maintenance_window_start <= self.maintenance_window_end:
            return self.maintenance_window_start <= current_hour < self.maintenance_window_end
        else:
            return current_hour >= self.maintenance_window_start or current_hour < self.maintenance_window_end
    
    def _write_audit_log(self, plan: ChangePlan) -> None:
        """Write change to audit log."""
        try:
            audit_entry = {
                "timestamp": datetime.now().isoformat(),
                "change_id": plan["change_id"],
                "title": plan["title"],
                "change_type": plan["change_type"],
                "priority": plan["priority"],
                "status": plan["status"],
                "approved_by": plan["approved_by"],
                "affected_components": plan["affected_components"]
            }
            
            audit_log = []
            if self.audit_log_file.exists():
                try:
                    audit_log = json.loads(self.audit_log_file.read_text())
                except:
                    pass
            
            audit_log.append(audit_entry)
            
            if len(audit_log) > 1000:
                audit_log = audit_log[-1000:]
            
            self.audit_log_file.write_text(json.dumps(audit_log, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Error writing audit log: {e}")
    
    def get_pending_changes(self) -> List[ChangePlan]:
        """Get list of pending changes."""
        pending = []
        for change_file in self.changes_dir.glob("change_*.json"):
            try:
                plan = json.loads(change_file.read_text())
                if plan.get("status") == ChangeStatus.PENDING.value:
                    pending.append(plan)
            except:
                pass
        return pending
    
    def get_change_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Get change history from audit log."""
        if not self.audit_log_file.exists():
            return []
        
        try:
            audit_log = json.loads(self.audit_log_file.read_text())
            return audit_log[-limit:]
        except:
            return []


# Global instance
_change_management: Optional[ChangeManagementSystem] = None

def get_change_management() -> ChangeManagementSystem:
    """Get or create global change management instance."""
    global _change_management
    if _change_management is None:
        _change_management = ChangeManagementSystem()
    return _change_management


class LogAnalyzer:
    """Continuously ingests and analyzes logs to detect anomalies and failures."""

    def __init__(self):
        self.medic_dir = MEDIC_DIR
        self.log_sources: List[Path] = []
        self.anomaly_patterns = [
            r"ERROR", r"CRITICAL", r"FATAL", r"Exception", r"Traceback",
            r"timeout", r"failed", r"connection.*refused",
            r"permission denied", r"disk full", r"memory error", r"segmentation fault",
        ]
        self._evolver = EvolverEngine()
        self._configure_default_sources()
    
    def _configure_default_sources(self) -> None:
        """Configure default log sources."""
        self.log_sources = [
            Path.home() / ".myclaw" / "logs" / "agent.log",
            Path.home() / ".myclaw" / "task_logs",
        ]
        self.log_sources = [s for s in self.log_sources if s and s.exists()]
    
    def add_log_source(self, path: Path) -> None:
        """Add a new log source."""
        if path.exists() and path not in self.log_sources:
            self.log_sources.append(path)
            logger.info(f"Added log source: {path}")
    
    def analyze_logs(self, since_minutes: int = 60) -> Dict[str, Any]:
        """Analyze logs for anomalies (legacy regex-based)."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "sources_analyzed": 0,
            "total_lines": 0,
            "anomalies_detected": 0,
            "anomalies": [],
            "error_rate": 0.0,
            "trends": []
        }

        for source in self.log_sources:
            if not source.exists():
                continue

            try:
                if source.is_dir():
                    for log_file in source.glob("*.log"):
                        file_results = self._analyze_log_file(log_file)
                        self._merge_results(results, file_results)
                else:
                    file_results = self._analyze_log_file(source)
                    self._merge_results(results, file_results)
            except Exception as e:
                logger.error(f"Error analyzing log source {source}: {e}")

        if results["total_lines"] > 0:
            results["error_rate"] = results["anomalies_detected"] / results["total_lines"]

        results["trends"] = self._detect_trends(results["anomalies"])

        return results

    def analyze_logs_enhanced(self, since_minutes: int = 60) -> Dict[str, Any]:
        """
        Enhanced log analysis using the deterministic evolver engine.

        Returns structured result with patterns, health score, recommendations,
        and summary — compatible with capability-evolver output format.
        """
        raw_logs = self._collect_raw_logs(since_minutes)

        entries = []
        for log in raw_logs:
            level_str = self._detect_level(log["content"])
            entries.append(LogEntry(
                timestamp=log.get("timestamp", datetime.now().isoformat()),
                level=level_str,  # type: ignore[arg-type]
                message=log["content"][:200],
                context=str(log.get("file", "")),
            ))

        result = self._evolver.analyze(entries)

        return {
            "timestamp": datetime.now().isoformat(),
            "sources_analyzed": len(self.log_sources),
            "total_lines": len(raw_logs),
            "health_score": result.health_score,
            "patterns": [p.to_dict() for p in result.patterns],
            "recommendations": [r.to_dict() for r in result.recommendations],
            "summary": result.summary,
        }
    
    def _analyze_log_file(self, log_file: Path) -> Dict[str, Any]:
        """Analyze a single log file."""
        results = {"total_lines": 0, "anomalies_detected": 0, "anomalies": []}
        
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            lines = content.split("\n")
            
            for line_num, line in enumerate(lines, 1):
                results["total_lines"] += 1
                
                for pattern in self.anomaly_patterns:
                    if re.search(pattern, line, re.IGNORECASE):
                        results["anomalies_detected"] += 1
                        results["anomalies"].append({
                            "file": str(log_file),
                            "line": line_num,
                            "content": line[:200],
                            "pattern": pattern,
                            "timestamp": self._extract_timestamp(line)
                        })
                        break
        except Exception as e:
            logger.error(f"Error reading log file {log_file}: {e}")
        
        return results
    
    def _merge_results(self, main: Dict[str, Any], addition: Dict[str, Any]) -> None:
        """Merge analysis results."""
        main["sources_analyzed"] += 1
        main["total_lines"] += addition["total_lines"]
        main["anomalies_detected"] += addition["anomalies_detected"]
        main["anomalies"].extend(addition["anomalies"])
    
    def _extract_timestamp(self, line: str) -> Optional[str]:
        """Try to extract timestamp from log line."""
        patterns = [
            r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})",
            r"(\d{2}/\d{2}/\d{4}\s+\d{2}:\d{2}:\d{2})",
        ]

        for pattern in patterns:
            match = re.search(pattern, line)
            if match:
                return match.group(1)
        return None

    def _detect_level(self, content: str) -> str:
        """Detect log level from content."""
        upper = content.upper()
        if any(k in upper for k in ("ERROR", "CRITICAL", "FATAL", "EXCEPTION")):
            return "error"
        elif "WARN" in upper:
            return "warn"
        elif "DEBUG" in upper:
            return "debug"
        return "info"

    def _collect_raw_logs(self, since_minutes: int) -> List[Dict[str, Any]]:
        """Collect raw log entries from all sources."""
        raw_logs: List[Dict[str, Any]] = []
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

    def _parse_log_file(self, log_file: Path, cutoff: datetime) -> List[Dict[str, Any]]:
        """Parse a single log file and filter by time."""
        results: List[Dict[str, Any]] = []
        try:
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            for line_num, line in enumerate(content.split("\n"), 1):
                if not line.strip():
                    continue
                ts = self._extract_timestamp(line)
                if ts:
                    try:
                        log_time = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if log_time < cutoff:
                            continue
                    except Exception:
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

    def _detect_trends(self, anomalies: List[Dict]) -> List[Dict[str, Any]]:
        """Detect trends in anomalies."""
        trends = []
        
        if not anomalies:
            return trends
        
        pattern_counts = {}
        for anomaly in anomalies:
            pattern = anomaly.get("pattern", "unknown")
            pattern_counts[pattern] = pattern_counts.get(pattern, 0) + 1
        
        for pattern, count in pattern_counts.items():
            if count > 5:
                trends.append({
                    "type": "repeating_error",
                    "pattern": pattern,
                    "count": count,
                    "severity": "high" if count > 10 else "medium"
                })
        
        return trends
    
    def should_trigger_change(self, analysis: Dict[str, Any]) -> tuple:
        """Determine if analysis results should trigger a change."""
        if analysis.get("error_rate", 0) > 0.1:
            return True, f"High error rate detected: {analysis['error_rate']:.1%}"
        
        critical_patterns = ["FATAL", "CRITICAL", "segmentation fault", "disk full"]
        for anomaly in analysis.get("anomalies", []):
            for pattern in critical_patterns:
                if pattern.lower() in anomaly.get("content", "").lower():
                    return True, f"Critical error detected: {pattern}"
        
        for trend in analysis.get("trends", []):
            if trend.get("severity") == "high":
                return True, f"High severity trend detected: {trend['pattern']}"
        
        return False, "No action needed"


class ScheduledReviewSystem:
    """Manages scheduled and triggered log reviews."""
    
    def __init__(self):
        self.review_interval_minutes = 60
        self._last_review_time: Optional[datetime] = None
        self._running = False
    
    async def start_continuous_monitoring(self) -> None:
        """Start continuous log monitoring."""
        self._running = True
        logger.info("Starting continuous log monitoring")
        
        while self._running:
            try:
                await self.perform_review()
                await asyncio.sleep(self.review_interval_minutes * 60)
            except Exception as e:
                logger.error(f"Error in continuous monitoring: {e}")
                await asyncio.sleep(60)
    
    async def perform_review(self) -> Dict[str, Any]:
        """Perform a scheduled review."""
        self._last_review_time = datetime.now()
        
        logger.info("Performing scheduled system review")
        
        analyzer = LogAnalyzer()
        analysis = analyzer.analyze_logs(since_minutes=self.review_interval_minutes)
        
        should_trigger, reason = analyzer.should_trigger_change(analysis)
        
        review_result = {
            "timestamp": datetime.now().isoformat(),
            "analysis": analysis,
            "action_taken": None,
            "change_created": None
        }
        
        if should_trigger:
            logger.warning(f"Review triggered change: {reason}")
            
            change_mgmt = get_change_management()
            plan = change_mgmt.create_change_plan(
                title=f"Automated fix for: {reason[:50]}",
                description=f"Automatically generated change to address: {reason}",
                rationale=f"Detected during scheduled review: {analysis['anomalies_detected']} anomalies",
                change_type=ChangeType.PATCH,
                priority=ChangePriority.HIGH if "critical" in reason.lower() else ChangePriority.MEDIUM,
                affected_components=["system_health"],
                proposed_changes={},
                risks=["Automated change may not address root cause"],
                rollback_steps=["Restore from backup", "Restart services"]
            )
            
            review_result["action_taken"] = "change_created"
            review_result["change_created"] = plan["change_id"]
            
            if plan["status"] == ChangeStatus.APPROVED.value:
                execution_result = await change_mgmt.execute_change(plan["change_id"])
                review_result["execution_result"] = execution_result
        
        return review_result
    
    def stop_monitoring(self) -> None:
        """Stop continuous monitoring."""
        self._running = False


_review_system: Optional[ScheduledReviewSystem] = None

def start_continuous_monitoring() -> str:
    """Start continuous log monitoring and automatic remediation."""
    global _review_system
    
    if _review_system and _review_system._running:
        return "⚠️ Continuous monitoring is already running"
    
    _review_system = ScheduledReviewSystem()
    asyncio.create_task(_review_system.start_continuous_monitoring())
    
    return "✅ Continuous monitoring started (review interval: 60 minutes)"


def stop_continuous_monitoring() -> str:
    """Stop continuous monitoring."""
    global _review_system
    
    if _review_system:
        _review_system.stop_monitoring()
        return "⏹️ Continuous monitoring stopped"
    
    return "⚠️ Continuous monitoring is not running"


# Convenience functions for CLI/tools integration
async def create_change_plan(
    title: str,
    description: str,
    rationale: str,
    change_type: str,
    priority: str,
    affected_components: List[str],
    proposed_changes: Dict[str, str],
    risks: Optional[List[str]] = None,
    rollback_steps: Optional[List[str]] = None
) -> str:
    """Create a change plan."""
    change_mgmt = get_change_management()
    
    plan = change_mgmt.create_change_plan(
        title=title,
        description=description,
        rationale=rationale,
        change_type=ChangeType(change_type),
        priority=ChangePriority(priority),
        affected_components=affected_components,
        proposed_changes=proposed_changes,
        risks=risks,
        rollback_steps=rollback_steps
    )
    
    status_icon = "✅" if plan["status"] == "approved" else "⏳"
    return f"{status_icon} Change plan created: {plan['change_id']}\nStatus: {plan['status']}\nTitle: {title}"


async def approve_change(change_id: str, approved_by: str = "operator") -> str:
    """Approve a pending change plan."""
    change_mgmt = get_change_management()
    plan = change_mgmt.approve_change(change_id, approved_by)
    
    if plan:
        return f"✅ Change {change_id} approved by {approved_by}"
    return f"❌ Change {change_id} not found or already processed"


async def execute_change(change_id: str, dry_run: bool = False) -> str:
    """Execute an approved change plan."""
    change_mgmt = get_change_management()
    result = await change_mgmt.execute_change(change_id, dry_run)
    
    if dry_run:
        return f"🔍 Dry run for {change_id}:\n{json.dumps(result, indent=2)}"
    
    if result.get("success"):
        return f"✅ Change {change_id} executed successfully"
    else:
        return f"❌ Change {change_id} failed: {result.get('error', 'Unknown error')}"


def analyze_system_logs(since_minutes: int = 60) -> str:
    """Analyze system logs for anomalies."""
    analyzer = LogAnalyzer()
    analysis = analyzer.analyze_logs(since_minutes)
    
    lines = [
        "📊 Log Analysis Report",
        f"Timestamp: {analysis['timestamp']}",
        "",
        f"Sources Analyzed: {analysis['sources_analyzed']}",
        f"Total Lines: {analysis['total_lines']}",
        f"Anomalies Detected: {analysis['anomalies_detected']}",
        f"Error Rate: {analysis['error_rate']:.2%}",
    ]
    
    if analysis['anomalies']:
        lines.extend(["", "Recent Anomalies:"])
        for anomaly in analysis['anomalies'][:10]:
            lines.append(f"  • {anomaly['file']}:{anomaly['line']}: {anomaly['content'][:60]}...")
    
    if analysis['trends']:
        lines.extend(["", "Detected Trends:"])
        for trend in analysis['trends']:
            lines.append(f"  • {trend['type']}: {trend['pattern']} ({trend['count']} occurrences)")
    
    return "\n".join(lines)


def analyze_system_logs_enhanced(since_minutes: int = 60) -> str:
    """
    Analyze system logs using the deterministic evolver engine.

    Returns a structured report with health score, detected patterns,
    severity classification, and actionable recommendations.
    """
    analyzer = LogAnalyzer()
    analysis = analyzer.analyze_logs_enhanced(since_minutes)

    lines = [
        "📊 Enhanced Log Analysis Report (Evolver Engine)",
        f"Timestamp: {analysis['timestamp']}",
        "",
        f"Sources Analyzed: {analysis['sources_analyzed']}",
        f"Total Lines: {analysis['total_lines']}",
        f"Health Score: {analysis['health_score']}/100",
        "",
    ]

    if analysis['patterns']:
        lines.append("Detected Patterns:")
        for p in analysis['patterns'][:10]:
            icon = {
                "critical": "🔴", "high": "🟠",
                "medium": "🟡", "low": "🟢",
            }.get(p['severity'], "⚪")
            lines.append(
                f"  {icon} [{p['type'].upper()}] {p['description'][:60]}"
                f" ({p['occurrences']}x)"
            )
        lines.append("")

    if analysis['recommendations']:
        lines.append("Recommendations:")
        for r in analysis['recommendations'][:10]:
            icon = {
                "immediate": "🔴", "high": "🟠",
                "medium": "🟡", "low": "🟢",
            }.get(r['priority'], "⚪")
            lines.append(f"  {icon} [{r['category'].upper()}] {r['description'][:70]}")
        lines.append("")

    summary = analysis.get('summary', {})
    lines.extend([
        "Summary:",
        f"  Total logs: {summary.get('total_logs', 0)}",
        f"  Errors: {summary.get('error_count', 0)}",
        f"  Warnings: {summary.get('warn_count', 0)}",
        f"  Critical patterns: {summary.get('critical_count', 0)}",
    ])

    return "\n".join(lines)


def get_pending_changes() -> str:
    """Get list of pending changes awaiting approval."""
    change_mgmt = get_change_management()
    pending = change_mgmt.get_pending_changes()
    
    if not pending:
        return "📋 No pending changes"
    
    lines = [f"📋 Pending Changes ({len(pending)}):", ""]
    for plan in pending:
        lines.append(f"  • {plan['change_id']}")
        lines.append(f"    Title: {plan['title']}")
        lines.append(f"    Priority: {plan['priority']}")
        lines.append("")
    
    return "\n".join(lines)


def get_change_history(limit: int = 10) -> str:
    """Get recent change history."""
    change_mgmt = get_change_management()
    history = change_mgmt.get_change_history(limit)
    
    if not history:
        return "📜 No change history available"
    
    lines = [f"📜 Recent Changes (last {len(history)}):", ""]
    for entry in history:
        lines.append(f"  • {entry['change_id']}")
        lines.append(f"    Title: {entry['title']}")
        lines.append(f"    Type: {entry['change_type']} | Status: {entry['status']}")
        lines.append("")
    
    return "\n".join(lines)


__all__ = [
    'ChangeManagementSystem', 'ChangeStatus', 'ChangePriority', 'ChangeType',
    'LogAnalyzer', 'ScheduledReviewSystem',
    'get_change_management', 'create_change_plan', 'approve_change',
    'execute_change', 'analyze_system_logs', 'analyze_system_logs_enhanced',
    'get_pending_changes',
    'get_change_history', 'start_continuous_monitoring', 'stop_continuous_monitoring'
]
