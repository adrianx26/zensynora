"""
Agent Discovery and Integration Module

Provides utilities for discovering and utilizing agents from the registry
in the MyClaw swarm system.
"""

import logging
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from .registry import (
    AGENT_REGISTRY,
    AgentDefinition,
    AgentCategory,
    AgentCapability,
    get_agent,
    list_agents,
    list_agents_by_category,
)

logger = logging.getLogger(__name__)


@dataclass
class AgentMatch:
    """A matched agent with relevance score."""
    agent: AgentDefinition
    relevance_score: float
    match_reason: str


class AgentDiscovery:
    """Discovers and recommends agents based on task requirements."""

    def __init__(self):
        self.registry = AGENT_REGISTRY

    def find_agents_for_task(
        self,
        task_description: str,
        required_capabilities: Optional[List[AgentCapability]] = None,
        category: Optional[AgentCategory] = None,
        limit: int = 5
    ) -> List[AgentMatch]:
        """
        Find the best agents for a given task.

        Args:
            task_description: Natural language description of the task
            required_capabilities: Optional list of required capabilities
            category: Optional category filter
            limit: Maximum number of agents to return

        Returns:
            List of AgentMatch objects sorted by relevance
        """
        candidates = list(self.registry.values())

        if category:
            candidates = [a for a in candidates if a.category == category]

        if required_capabilities:
            candidates = [
                a for a in candidates
                if any(cap in a.capabilities for cap in required_capabilities)
            ]

        scored = []
        task_lower = task_description.lower()

        for agent in candidates:
            score = 0.0
            reasons = []

            if any(word in agent.description.lower() for word in task_lower.split()):
                score += 0.3
                reasons.append("description match")

            if any(word in agent.name.lower() for word in task_lower.split()):
                score += 0.4
                reasons.append("name match")

            if any(word in tag.lower() for tag in agent.tags for word in task_lower.split()):
                score += 0.2
                reasons.append("tag match")

            if any(cap.value in task_lower for cap in agent.capabilities):
                score += 0.3
                reasons.append("capability match")

            if required_capabilities:
                matched_caps = [
                    cap for cap in required_capabilities
                    if cap in agent.capabilities
                ]
                score += len(matched_caps) * 0.2

            if score > 0:
                scored.append(AgentMatch(
                    agent=agent,
                    relevance_score=score,
                    match_reason=", ".join(reasons)
                ))

        scored.sort(key=lambda x: x.relevance_score, reverse=True)
        return scored[:limit]

    def get_swarm_composition(
        self,
        task: str,
        strategy: str = "parallel"
    ) -> List[str]:
        """
        Suggest a swarm composition for a task.

        Args:
            task: Task description
            strategy: Swarm strategy (parallel, sequential, hierarchical)

        Returns:
            List of agent names to include in swarm
        """
        if strategy == "parallel":
            matches = self.find_agents_for_task(task, limit=3)
            return [m.agent.name for m in matches]

        elif strategy == "hierarchical":
            matches = self.find_agents_for_task(task, limit=5)
            if len(matches) >= 2:
                return [matches[0].agent.name] + [
                    m.agent.name for m in matches[1:4]
                ]
            return [m.agent.name for m in matches]

        elif strategy == "sequential":
            matches = self.find_agents_for_task(task, limit=3)
            return [m.agent.name for m in matches]

        return []

    def list_capabilities(self) -> Dict[AgentCategory, List[str]]:
        """List all capabilities organized by category."""
        by_category = list_agents_by_category()
        result = {}
        for cat, agents in by_category.items():
            caps = set()
            for agent in agents:
                caps.update(cap.value for cap in agent.capabilities)
            result[cat] = sorted(caps)
        return result


def integrate_with_swarm(orchestrator) -> None:
    """
    Integrate agent discovery with the swarm orchestrator.

    This enhances the orchestrator with:
    - Intelligent agent recommendations
    - Task-based agent finding
    - Swarm composition suggestions
    """
    discovery = AgentDiscovery()

    orchestrator.discover_agents = discovery.find_agents_for_task
    orchestrator.suggest_swarm = discovery.get_swarm_composition
    orchestrator.list_capabilities = discovery.list_capabilities

    logger.info("Agent discovery integrated with swarm orchestrator")


async def delegate_to_agent(
    agent_name: str,
    task: str,
    context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Delegate a task to a specific agent.

    Args:
        agent_name: Name of the agent to delegate to
        task: Task description
        context: Optional additional context

    Returns:
        Agent's response
    """
    agent_def = get_agent(agent_name)
    if not agent_def:
        return f"Agent '{agent_name}' not found in registry"

    capabilities = [cap.value for cap in agent_def.capabilities]
    context_str = f"\n\nContext: {context}" if context else ""

    prompt = f"""You are acting as the **{agent_def.name}** agent.
Description: {agent_def.description}
Your capabilities include: {', '.join(capabilities)}

Task: {task}{context_str}

Follow your specialization guidelines and provide a thorough response.
"""

    return prompt


def get_agent_info(name: str) -> Optional[Dict[str, Any]]:
    """
    Get detailed information about an agent.

    Args:
        name: Agent name

    Returns:
        Dict with agent details or None if not found
    """
    agent = get_agent(name)
    if not agent:
        return None

    return {
        "name": agent.name,
        "description": agent.description,
        "category": agent.category.value,
        "capabilities": [cap.value for cap in agent.capabilities],
        "tags": list(agent.tags),
        "model_routing": agent.model_routing,
        "sandbox_mode": agent.sandbox_mode,
        "profile_path": str(agent.profile_name) + ".md",
    }


def list_all_agents_brief() -> List[Dict[str, str]]:
    """Get a brief list of all agents for display."""
    return [
        {
            "name": agent.name,
            "description": agent.description,
            "category": agent.category.value,
        }
        for agent in AGENT_REGISTRY.values()
    ]
