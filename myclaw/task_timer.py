"""
Task Timer and Deployment Orchestrator for MyClaw/Zensynora.

This module provides:
- Task-level timing with 300-second maximum timeout
- Status updates at 60s, 120s, 180s, 240s thresholds
- Automatic failure handling and logging at 300s
- Deployment orchestration with step tracking
"""

import asyncio
import time
import logging
import json
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Callable, Optional, Any, Union
from enum import Enum, auto
from datetime import datetime
from pathlib import Path
import copy
import sys

# =============================================================================
# COLOR CODES FOR CONSOLE OUTPUT
# =============================================================================

class Colors:
    """ANSI color codes for console output."""
    RESET = "\033[0m"
    INFO = "\033[36m"      # Cyan
    SUCCESS = "\033[32m"   # Green
    WARNING = "\033[33m"   # Yellow
    ERROR = "\033[31m"     # Red
    CRITICAL = "\033[35m"  # Magenta
    DIAGNOSTIC = "\033[34m"    # Blue
    USER_PROMPT = "\033[93m"   # Bright Yellow
    TIMESTAMP = "\033[90m"     # Gray
    STEP_NAME = "\033[1;97m"   # Bold White
    METRIC = "\033[96m"        # Bright Cyan

# =============================================================================
# ENUMS AND DATA CLASSES
# =============================================================================

class TaskStatus(Enum):
    """Status of a task."""
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()
    TIMEOUT = auto()

class ErrorSeverity(Enum):
    """Severity levels for errors."""
    TRANSIENT = "transient"
    FATAL = "fatal"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"

class UserChoice(Enum):
    """User choices at 240s threshold."""
    WAIT = "wait"
    CANCEL = "cancel"
    ALTERNATIVE = "alternative"

@dataclass
class TaskThresholdConfig:
    """Configuration for time thresholds and messages."""
    threshold_60s: int = 60
    threshold_120s: int = 120
    threshold_180s: int = 180
    threshold_240s: int = 240
    max_timeout: int = 300  # Maximum time before marking task as failed
    
    msg_working: str = "Working on it, please wait..."
    msg_progress: str = "Still processing your request..."
    msg_diagnostic_intro: str = "Analysis: The task is taking longer than expected. Here's what might be happening:"
    msg_timeout_warning: str = "The task has been running for an extended period and may not be progressing."
    msg_max_timeout: str = "TASK FAILED: Maximum time limit (300s) reached. The task could not be completed."
    
    enable_diagnostics: bool = True
    enable_user_choices: bool = True
    verbose_logging: bool = False

@dataclass
class TaskTiming:
    """Timing information for a task."""
    task_id: str
    user_question: str
    start_time: float
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    status: TaskStatus = TaskStatus.PENDING
    current_step: Optional[str] = None
    steps_completed: int = 0
    steps_total: int = 0
    error_message: Optional[str] = None
    threshold_reached: Optional[int] = None

@dataclass
class StatusUpdate:
    """Status update sent to the user."""
    timestamp: float
    elapsed_seconds: float
    threshold: Optional[int]
    message: str
    message_type: str
    task_id: str
    step_name: Optional[str] = None
    color: str = Colors.INFO

# =============================================================================
# TASK TIMER ORCHESTRATOR
# =============================================================================

class TaskTimerOrchestrator:
    """
    Manages task execution with timing, status updates at thresholds,
    and automatic failure at 300 seconds.
    
    This class wraps around agent task execution to provide:
    - Per-task timing from question to answer
    - Status updates at 60s, 120s, 180s, 240s
    - Automatic task failure and logging at 300s
    - User notifications at each threshold
    """
    
    def __init__(self, config: Optional[TaskThresholdConfig] = None):
        self.config = config or TaskThresholdConfig()
        self._logger = logging.getLogger("myclaw.task_timer")
        self._active_timers: Dict[str, asyncio.Task] = {}
        self._task_status: Dict[str, TaskStatus] = {}
        self._task_timings: Dict[str, TaskTiming] = {}
        self._thresholds_reached: Dict[str, set] = {}
        self._lock = asyncio.Lock()
        
        # Ensure log directory exists
        self._log_dir = Path.home() / ".myclaw" / "task_logs"
        self._log_dir.mkdir(parents=True, exist_ok=True)
    
    def _colorize(self, text: str, color: str) -> str:
        """Apply color to text for console output."""
        return f"{color}{text}{Colors.RESET}"
    
    async def start_task_timer(
        self,
        task_id: str,
        user_question: str,
        on_status_update: Callable[[StatusUpdate], None],
        steps_total: int = 1
    ) -> None:
        """
        Start a task timer for a user question.
        
        Args:
            task_id: Unique identifier for this task
            user_question: The original user question
            on_status_update: Callback for status updates
            steps_total: Total number of steps in the task
        """
        async with self._lock:
            start_time = time.time()
            self._task_timings[task_id] = TaskTiming(
                task_id=task_id,
                user_question=user_question,
                start_time=start_time,
                status=TaskStatus.RUNNING,
                steps_total=steps_total
            )
            self._task_status[task_id] = TaskStatus.RUNNING
            self._thresholds_reached[task_id] = set()
        
        self._logger.info(f"Task timer started for '{task_id}': {user_question[:60]}...")
        
        # Start threshold monitoring
        thresholds = [
            self.config.threshold_60s,
            self.config.threshold_120s,
            self.config.threshold_180s,
            self.config.threshold_240s,
            self.config.max_timeout
        ]
        
        for threshold in thresholds:
            timer_task = asyncio.create_task(
                self._threshold_monitor(task_id, threshold, on_status_update),
                name=f"timer_{task_id}_{threshold}s"
            )
            self._active_timers[f"{task_id}_{threshold}"] = timer_task
    
    async def _threshold_monitor(
        self,
        task_id: str,
        threshold: int,
        on_status_update: Callable[[StatusUpdate], None]
    ) -> None:
        """
        Monitor a specific threshold and emit status updates.
        """
        try:
            # Wait until threshold
            await asyncio.sleep(threshold)
            
            async with self._lock:
                # Check if task is still running
                if task_id not in self._task_status:
                    return
                
                status = self._task_status[task_id]
                if status not in [TaskStatus.RUNNING, TaskStatus.PENDING]:
                    return
                
                # Check if already emitted for this threshold
                if threshold in self._thresholds_reached.get(task_id, set()):
                    return
                
                self._thresholds_reached[task_id].add(threshold)
                timing = self._task_timings.get(task_id)
                
                if not timing:
                    return
                
                elapsed = time.time() - timing.start_time
            
            # Handle max timeout (300s) - task failure
            if threshold >= self.config.max_timeout:
                await self._handle_max_timeout(task_id, elapsed, on_status_update)
                return
            
            # Emit appropriate message for other thresholds
            await self._emit_threshold_update(task_id, threshold, elapsed, on_status_update)
            
        except asyncio.CancelledError:
            # Normal cancellation when task completes
            pass
        except Exception as e:
            self._logger.error(f"Error in threshold monitor for {task_id}: {e}")
    
    async def _emit_threshold_update(
        self,
        task_id: str,
        threshold: int,
        elapsed: float,
        on_status_update: Callable[[StatusUpdate], None]
    ) -> None:
        """Emit status update for a threshold."""
        timing = self._task_timings.get(task_id)
        if not timing:
            return
        
        cfg = self.config
        
        if threshold == cfg.threshold_60s:
            message = cfg.msg_working
            msg_type = "info"
            color = Colors.INFO
        elif threshold == cfg.threshold_120s:
            message = f"{cfg.msg_progress} Currently processing: {timing.current_step or 'initializing'}"
            msg_type = "warning"
            color = Colors.WARNING
        elif threshold == cfg.threshold_180s:
            message = self._build_diagnostic_message(timing)
            msg_type = "diagnostic"
            color = Colors.DIAGNOSTIC
        elif threshold == cfg.threshold_240s:
            message = self._build_timeout_warning_message(timing)
            msg_type = "choice"
            color = Colors.USER_PROMPT
        else:
            return
        
        update = StatusUpdate(
            timestamp=time.time(),
            elapsed_seconds=elapsed,
            threshold=threshold,
            message=message,
            message_type=msg_type,
            task_id=task_id,
            step_name=timing.current_step,
            color=color
        )
        
        self._logger.info(f"Threshold {threshold}s reached for task '{task_id}'")
        on_status_update(update)
    
    async def _handle_max_timeout(
        self,
        task_id: str,
        elapsed: float,
        on_status_update: Callable[[StatusUpdate], None]
    ) -> None:
        """
        Handle maximum timeout (300s) - mark task as failed and log everything.
        """
        async with self._lock:
            timing = self._task_timings.get(task_id)
            if not timing:
                return
            
            # Mark task as failed due to timeout
            self._task_status[task_id] = TaskStatus.TIMEOUT
            timing.status = TaskStatus.TIMEOUT
            timing.end_time = time.time()
            timing.duration_ms = (timing.end_time - timing.start_time) * 1000
            timing.error_message = f"Task exceeded maximum timeout of {self.config.max_timeout} seconds"
            timing.threshold_reached = self.config.max_timeout
        
        # Build failure message
        failure_message = f"""
{self._colorize('='*70, Colors.CRITICAL)}
{self._colorize(self.config.msg_max_timeout, Colors.CRITICAL)}
{self._colorize('='*70, Colors.CRITICAL)}

Task ID: {task_id}
Duration: {elapsed:.1f} seconds
Status: FAILED (TIMEOUT)

The task could not be completed within the maximum allowed time.
This may be due to:
  - Complex processing requirements
  - External service delays
  - Network connectivity issues
  - Resource constraints

{self._colorize('Please try again with:', Colors.INFO)}
  - A simpler or more specific question
  - Breaking the task into smaller parts
  - Checking system resources and connectivity
"""
        
        update = StatusUpdate(
            timestamp=time.time(),
            elapsed_seconds=elapsed,
            threshold=self.config.max_timeout,
            message=failure_message,
            message_type="fatal",
            task_id=task_id,
            color=Colors.CRITICAL
        )
        
        # Log comprehensive failure information
        await self._log_task_failure(task_id, timing, elapsed)
        
        self._logger.critical(f"Task '{task_id}' FAILED - exceeded {self.config.max_timeout}s timeout")
        on_status_update(update)
    
    async def _log_task_failure(self, task_id: str, timing: TaskTiming, elapsed: float) -> None:
        """
        Log comprehensive failure information for debugging.
        """
        log_entry = {
            "event": "TASK_TIMEOUT_FAILURE",
            "task_id": task_id,
            "timestamp": datetime.utcnow().isoformat(),
            "user_question": timing.user_question,
            "duration_seconds": elapsed,
            "max_timeout_configured": self.config.max_timeout,
            "thresholds_reached": list(self._thresholds_reached.get(task_id, set())),
            "steps_completed": timing.steps_completed,
            "steps_total": timing.steps_total,
            "current_step": timing.current_step,
            "status": timing.status.name,
            "error_message": timing.error_message,
        }
        
        # Write to dedicated failure log file
        log_file = self._log_dir / f"task_failure_{task_id}_{int(time.time())}.json"
        try:
            with open(log_file, 'w') as f:
                json.dump(log_entry, f, indent=2, default=str)
            self._logger.info(f"Failure log written to: {log_file}")
        except Exception as e:
            self._logger.error(f"Failed to write failure log: {e}")
        
        # Also log to main log
        self._logger.error(f"Task failure details: {json.dumps(log_entry, default=str)}")
    
    def _build_diagnostic_message(self, timing: TaskTiming) -> str:
        """Build diagnostic message at 180s threshold."""
        lines = [
            self.config.msg_diagnostic_intro,
            "",
            "Potential causes for the delay:",
            f"  1. Complex question requiring extensive processing",
            f"  2. External API calls or database queries taking longer than expected",
            f"  3. High system load or resource contention",
            f"  4. Network latency or connectivity issues",
            "",
            f"Progress: {timing.steps_completed}/{timing.steps_total} steps completed",
        ]
        
        if timing.current_step:
            lines.append(f"Current step: {timing.current_step}")
        
        lines.extend([
            "",
            "You can:",
            "  - Continue waiting (the task may complete soon)",
            "  - Type 'cancel' to stop the current task",
            "  - Type 'status' for detailed progress information"
        ])
        
        return "\n".join(lines)
    
    def _build_timeout_warning_message(self, timing: TaskTiming) -> str:
        """Build timeout warning message at 240s threshold."""
        lines = [
            self.config.msg_timeout_warning,
            "",
            f"Task has been running for an extended period.",
            f"Progress: {timing.steps_completed}/{timing.steps_total} steps",
            f"Current step: {timing.current_step or 'Unknown'}",
            "",
            "At 300 seconds, the task will be automatically marked as failed.",
            "",
            "Please choose an option:",
            "  [wait]     - Continue waiting (may complete before 300s)",
            "  [cancel]   - Cancel the current task",
            "  [retry]    - Try a different or simpler question"
        ]
        return "\n".join(lines)
    
    async def complete_task(self, task_id: str, success: bool = True, 
                          error_message: Optional[str] = None) -> None:
        """
        Mark a task as completed successfully or failed.
        Cancels all pending threshold timers.
        """
        async with self._lock:
            if task_id not in self._task_timings:
                return
            
            timing = self._task_timings[task_id]
            timing.end_time = time.time()
            timing.duration_ms = (timing.end_time - timing.start_time) * 1000
            
            if success:
                timing.status = TaskStatus.COMPLETED
                self._task_status[task_id] = TaskStatus.COMPLETED
                self._logger.info(f"Task '{task_id}' completed in {timing.duration_ms/1000:.1f}s")
            else:
                timing.status = TaskStatus.FAILED
                timing.error_message = error_message
                self._task_status[task_id] = TaskStatus.FAILED
                self._logger.error(f"Task '{task_id}' failed after {timing.duration_ms/1000:.1f}s: {error_message}")
            
            # Cancel all pending timers for this task
            await self._cancel_task_timers(task_id)
    
    async def update_step(self, task_id: str, step_name: str, 
                         step_number: int, steps_total: int) -> None:
        """Update the current step being processed."""
        async with self._lock:
            if task_id in self._task_timings:
                timing = self._task_timings[task_id]
                timing.current_step = step_name
                timing.steps_completed = step_number - 1
                timing.steps_total = steps_total
    
    async def _cancel_task_timers(self, task_id: str) -> None:
        """Cancel all pending timers for a task."""
        thresholds = [60, 120, 180, 240, 300]
        for threshold in thresholds:
            timer_key = f"{task_id}_{threshold}"
            if timer_key in self._active_timers:
                timer_task = self._active_timers.pop(timer_key)
                if not timer_task.done():
                    timer_task.cancel()
                    try:
                        await timer_task
                    except asyncio.CancelledError:
                        pass
    
    async def cancel_task(self, task_id: str) -> bool:
        """Cancel a running task."""
        async with self._lock:
            if task_id not in self._task_timings:
                return False
            
            timing = self._task_timings[task_id]
            timing.status = TaskStatus.CANCELLED
            timing.end_time = time.time()
            timing.duration_ms = (timing.end_time - timing.start_time) * 1000
            self._task_status[task_id] = TaskStatus.CANCELLED
        
        await self._cancel_task_timers(task_id)
        self._logger.info(f"Task '{task_id}' cancelled by user")
        return True
    
    def get_task_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get the current status of a task."""
        return self._task_status.get(task_id)
    
    def get_task_timing(self, task_id: str) -> Optional[TaskTiming]:
        """Get timing information for a task."""
        return self._task_timings.get(task_id)
    
    def is_task_active(self, task_id: str) -> bool:
        """Check if a task is still active (running or pending)."""
        status = self._task_status.get(task_id)
        return status in [TaskStatus.RUNNING, TaskStatus.PENDING]

# =============================================================================
# GLOBAL INSTANCE
# =============================================================================

# Singleton instance for use across the application
_task_timer_orchestrator: Optional[TaskTimerOrchestrator] = None

def get_task_timer_orchestrator(config: Optional[TaskThresholdConfig] = None) -> TaskTimerOrchestrator:
    """Get or create the global task timer orchestrator instance."""
    global _task_timer_orchestrator
    if _task_timer_orchestrator is None:
        _task_timer_orchestrator = TaskTimerOrchestrator(config)
    return _task_timer_orchestrator

def reset_task_timer_orchestrator() -> None:
    """Reset the global orchestrator (useful for testing)."""
    global _task_timer_orchestrator
    _task_timer_orchestrator = None
