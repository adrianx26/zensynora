"""MyClaw - Personal AI Agent

Task Timer Integration (v3):
---------------------------
The agent now includes automatic task timing with the following features:
- 300-second maximum timeout for any user question
- Status updates at 60s, 120s, 180s, and 240s thresholds
- Automatic task failure and logging at 300s
- User notifications at each threshold with diagnostic information

The timer starts automatically when agent.think() is called and tracks:
  1. Memory loading
  2. Knowledge base search
  3. System prompt building
  4. LLM call
  5. Tool execution (if needed)
  6. Response generation

Configuration:
- Logs are stored in ~/.myclaw/task_logs/
- Thresholds are configurable via TaskThresholdConfig
- Status updates are printed to console with color coding
"""
__version__ = "0.4.1"

# Export task timer for external use
from .task_timer import (
    TaskTimerOrchestrator,
    TaskThresholdConfig,
    TaskStatus,
    get_task_timer_orchestrator,
)

__all__ = [
    "TaskTimerOrchestrator",
    "TaskThresholdConfig", 
    "TaskStatus",
    "get_task_timer_orchestrator",
]
