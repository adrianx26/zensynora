"""ZenSynora Agent System - Specialized agents for skill management, health monitoring, and tech intelligence."""

from .skill_adapter import SkillAdapter
from .medic_agent import MedicAgent
from .medic_change_mgmt import (
    # Change management
    ChangeManagementSystem,
    ChangeStatus,
    ChangePriority,
    ChangeType,
    # Log analysis
    LogAnalyzer,
    ScheduledReviewSystem,
    # Functions
    get_change_management,
    create_change_plan,
    approve_change,
    execute_change,
    analyze_system_logs,
    get_pending_changes,
    get_change_history,
    start_continuous_monitoring,
    stop_continuous_monitoring,
)
from .newtech_agent import NewTechAgent
from .registry import (
    AGENT_REGISTRY,
    AgentCategory,
    AgentCapability,
    AgentDefinition,
    get_agent,
    list_agents,
    list_agents_by_category,
    get_agent_count,
    get_categories_with_count,
)
from .discovery import (
    AgentDiscovery,
    AgentMatch,
    integrate_with_swarm,
    delegate_to_agent,
    get_agent_info,
    list_all_agents_brief,
)

__all__ = [
    # Core agents
    "SkillAdapter",
    "MedicAgent",
    "NewTechAgent",
    
    # Change Management (Medic Agent Extension)
    "ChangeManagementSystem",
    "ChangeStatus",
    "ChangePriority",
    "ChangeType",
    "LogAnalyzer",
    "ScheduledReviewSystem",
    "get_change_management",
    "create_change_plan",
    "approve_change",
    "execute_change",
    "analyze_system_logs",
    "get_pending_changes",
    "get_change_history",
    "start_continuous_monitoring",
    "stop_continuous_monitoring",

    # Registry
    "AGENT_REGISTRY",
    "AgentCategory",
    "AgentCapability",
    "AgentDefinition",
    "get_agent",
    "list_agents",
    "list_agents_by_category",
    "get_agent_count",
    "get_categories_with_count",

    # Discovery
    "AgentDiscovery",
    "AgentMatch",
    "integrate_with_swarm",
    "delegate_to_agent",
    "get_agent_info",
    "list_all_agents_brief",
]
