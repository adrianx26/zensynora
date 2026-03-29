"""
Agent Swarm System for MyClaw

This package provides multi-agent coordination capabilities allowing
multiple AI agents to collaborate on complex tasks using various
strategies: parallel, sequential, hierarchical, and voting.

Example Usage:
    from myclaw.swarm import SwarmOrchestrator, SwarmConfig
    
    config = SwarmConfig(
        name="research_team",
        strategy="parallel",
        workers=["agent1", "agent2", "agent3"],
        aggregation_method="synthesis"
    )
    
    orchestrator = SwarmOrchestrator()
    swarm_id = await orchestrator.create_swarm(config, user_id="user1")
    result = await orchestrator.execute_task(swarm_id, "Research AI trends")
"""

from .models import (
    SwarmConfig,
    SwarmTask,
    SwarmResult,
    SwarmStrategy,
    AggregationMethod,
    TaskStatus,
    MessageType,
    SwarmMessage,
)
from .orchestrator import SwarmOrchestrator
from .storage import SwarmStorage
from .strategies import (
    ParallelStrategy,
    SequentialStrategy,
    HierarchicalStrategy,
    VotingStrategy,
    get_strategy,
)
from .collaboration import (
    TeamCollaboration,
    TeamChat,
    SharedTeamContext,
    TeamMember,
    CollaborationEvent,
    CollaborationEventType,
)

__all__ = [
    # Models
    "SwarmConfig",
    "SwarmTask",
    "SwarmResult",
    "SwarmStrategy",
    "AggregationMethod",
    "TaskStatus",
    "MessageType",
    "SwarmMessage",
    # Core
    "SwarmOrchestrator",
    "SwarmStorage",
    # Strategies
    "ParallelStrategy",
    "SequentialStrategy",
    "HierarchicalStrategy",
    "VotingStrategy",
    "get_strategy",
    # Collaboration
    "TeamCollaboration",
    "TeamChat",
    "SharedTeamContext",
    "TeamMember",
    "CollaborationEvent",
    "CollaborationEventType",
]
