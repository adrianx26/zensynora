"""ZenSynora Agent System - Specialized agents for skill management, health monitoring, and tech intelligence."""

from .skill_adapter import SkillAdapter
from .medic_agent import MedicAgent
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
