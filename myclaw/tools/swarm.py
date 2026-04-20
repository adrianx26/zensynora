"""
Tools — Agent Swarm & Delegation
"""

import asyncio
import logging
from typing import Any, Dict, List, Optional

from .core import (
    _agent_registry,
    TOOLS,
)

logger = logging.getLogger(__name__)

# ── Feature 3: Sub-Agent Delegation ──────────────────────────────────────────

async def delegate(agent_name: str, task: str, _depth: int = 0) -> str:
    """Delegate a task to another named agent and return its response.

    agent_name: name of the agent (see /agents for available names)
    task: the instruction to send to that agent
    """
    if _depth >= 2:
        return "Error: Maximum delegation depth (2) reached — cannot delegate further."
    if not _agent_registry:
        return "Error: Agent registry not initialized."
    if agent_name not in _agent_registry:
        available = ", ".join(_agent_registry.keys())
        return f"Error: Unknown agent '{agent_name}'. Available: {available}"
    try:
        return await _agent_registry[agent_name].think(task, user_id="__delegate__", _depth=_depth)
    except Exception as e:
        logger.error(f"Delegation error: {e}")
        return f"Delegation failed: {e}"


# ── Agent Swarm Tools ──────────────────────────────────────────────────────────

_swarm_orchestrator = None  # Lazy-initialized orchestrator instance


def _get_swarm_orchestrator():
    """Get or create the swarm orchestrator instance."""
    global _swarm_orchestrator
    if _swarm_orchestrator is None:
        from .swarm import SwarmOrchestrator
        from .config import load_config
        config = load_config()
        _swarm_orchestrator = SwarmOrchestrator(_agent_registry, config)
    return _swarm_orchestrator


async def swarm_create(
    name: str,
    strategy: str,
    workers: str,
    coordinator: str = None,
    aggregation: str = "synthesis",
    user_id: str = "default"
) -> str:
    """Create a new agent swarm for collaborative task execution.

    name: A descriptive name for the swarm (e.g., "research_team", "code_reviewers")
    strategy: Execution strategy - one of: parallel, sequential, hierarchical, voting
        - parallel: All agents work simultaneously, results aggregated
        - sequential: Agents work in pipeline (output feeds to next input)
        - hierarchical: Coordinator delegates tasks to workers
        - voting: Multiple agents solve same problem, consensus wins
    workers: Comma-separated list of agent names (e.g., "agent1,agent2,agent3")
    coordinator: Coordinator agent name (required for hierarchical strategy)
    aggregation: How to combine results - one of: consensus, best_pick, concatenation, synthesis
    user_id: User identifier for multi-user isolation

    Returns:
        Success message with swarm ID, or error message.

    Example:
        swarm_create("research_team", "parallel", "agent1,agent2,agent3")
    """
    try:
        from .swarm import SwarmConfig, SwarmStrategy, AggregationMethod

        orchestrator = _get_swarm_orchestrator()

        # Parse workers list
        worker_list = [w.strip() for w in workers.split(",") if w.strip()]
        if not worker_list:
            return "Error: At least one worker agent is required"

        # Validate strategy
        try:
            strategy_enum = SwarmStrategy(strategy.lower())
        except ValueError:
            return f"Error: Invalid strategy '{strategy}'. Use: parallel, sequential, hierarchical, voting"

        # Validate aggregation
        try:
            aggregation_enum = AggregationMethod(aggregation.lower())
        except ValueError:
            return f"Error: Invalid aggregation '{aggregation}'. Use: consensus, best_pick, concatenation, synthesis"

        # Create config
        config = SwarmConfig(
            name=name,
            strategy=strategy_enum,
            workers=worker_list,
            coordinator=coordinator,
            aggregation_method=aggregation_enum
        )

        # Create swarm
        swarm_id = await orchestrator.create_swarm(config, user_id)

        return (
            f"✅ Swarm created successfully!\n"
            f"   ID: {swarm_id}\n"
            f"   Name: {name}\n"
            f"   Strategy: {strategy}\n"
            f"   Workers: {', '.join(worker_list)}\n"
            f"   Coordinator: {coordinator or 'N/A'}\n"
            f"   Aggregation: {aggregation}"
        )
    except ValueError as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Error: {e}"
    except Exception as e:
        logger.error(f"Failed to create swarm: {e}")
        return f"Error creating swarm: {e}"


async def swarm_assign(swarm_id: str, task: str, user_id: str = "default") -> str:
    """Assign a task to a swarm for execution.

    swarm_id: The swarm ID returned by swarm_create()
    task: The task description/prompt for the swarm
    user_id: User identifier for multi-user isolation

    Returns:
        The aggregated result from all swarm agents.

    Example:
        swarm_assign("swarm_abc123", "Research the latest AI developments in 2024")
    """
    try:
        orchestrator = _get_swarm_orchestrator()

        # Execute task
        result = await orchestrator.execute_task(swarm_id, task)

        # Format response
        lines = [
            f"🐝 Swarm Execution Complete",
            f"   Swarm: {result.swarm_id}",
            f"   Aggregation: {result.aggregation_method.value}",
            f"   Confidence: {result.confidence_score:.2f}",
            f"   Execution Time: {result.execution_time_seconds:.2f}s",
            f"",
            f"📊 Individual Results:",
        ]

        for agent_name, agent_result in result.individual_results.items():
            status = "✅" if agent_result.success else "❌"
            lines.append(f"   {status} {agent_name}: {len(agent_result.result)} chars")

        lines.extend([
            f"",
            f"🎯 Final Result:",
            f"{result.final_result}"
        ])

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to assign task to swarm: {e}")
        return f"Error assigning task: {e}"


def swarm_status(swarm_id: str) -> str:
    """Get the current status of a swarm.

    swarm_id: The swarm ID

    Returns:
        Status information including current state and configuration.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        info = orchestrator.get_status(swarm_id)

        if not info:
            return f"Swarm not found: {swarm_id}"

        lines = [
            f"🐝 Swarm Status: {info.name}",
            f"   ID: {info.id}",
            f"   Status: {info.status.value}",
            f"   Strategy: {info.strategy.value}",
            f"   Workers: {', '.join(info.workers)}",
            f"   Coordinator: {info.coordinator or 'N/A'}",
            f"   Aggregation: {info.aggregation_method.value}",
            f"   Created: {info.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
        ]

        if info.completed_at:
            lines.append(f"   Completed: {info.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get swarm status: {e}")
        return f"Error getting status: {e}"


def swarm_result(swarm_id: str) -> str:
    """Get the final result of a completed swarm execution.

    swarm_id: The swarm ID

    Returns:
        The aggregated result or error message.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        result = orchestrator.get_result(swarm_id)

        if not result:
            return f"No result found for swarm: {swarm_id}"

        lines = [
            f"🎯 Swarm Result: {result.swarm_id}",
            f"   Aggregation Method: {result.aggregation_method.value}",
            f"   Confidence Score: {result.confidence_score:.2f}",
            f"   Execution Time: {result.execution_time_seconds:.2f}s",
            f"",
            f"📊 Individual Agent Results:",
        ]

        for agent_name, agent_result in result.individual_results.items():
            status = "✅" if agent_result.success else "❌"
            lines.append(f"   {status} {agent_name} ({agent_result.execution_time_seconds:.2f}s):")
            preview = agent_result.result[:200].replace('\n', ' ')
            lines.append(f"      {preview}...")

        lines.extend([
            f"",
            f"🎯 Final Aggregated Result:",
            f"{result.final_result}"
        ])

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get swarm result: {e}")
        return f"Error getting result: {e}"


async def swarm_terminate(swarm_id: str) -> str:
    """Terminate a running swarm.

    swarm_id: The swarm ID to terminate

    Returns:
        Success or error message.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        success = await orchestrator.terminate_swarm(swarm_id)

        if success:
            return f"✅ Swarm {swarm_id} terminated successfully"
        else:
            return f"❌ Could not terminate swarm {swarm_id} (may not be running or doesn't exist)"
    except Exception as e:
        logger.error(f"Failed to terminate swarm: {e}")
        return f"Error terminating swarm: {e}"


def swarm_list(status: str = None, user_id: str = "default") -> str:
    """List all swarms for a user.

    status: Optional filter - pending, running, completed, failed, terminated
    user_id: User identifier

    Returns:
        List of swarms with their status.
    """
    try:
        from .swarm import TaskStatus

        orchestrator = _get_swarm_orchestrator()

        # Parse status filter
        status_filter = None
        if status:
            try:
                status_filter = TaskStatus(status.lower())
            except ValueError:
                return f"Error: Invalid status '{status}'. Use: pending, running, completed, failed, terminated"

        swarms = orchestrator.list_swarms(user_id, status_filter)

        if not swarms:
            filter_str = f" with status '{status}'" if status else ""
            return f"No swarms found{filter_str}."

        lines = [f"🐝 Swarms ({len(swarms)} total):", ""]

        for swarm in swarms:
            status_icon = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
                "terminated": "🛑"
            }.get(swarm.status.value, "❓")

            lines.append(
                f"{status_icon} {swarm.name} ({swarm.id})"
            )
            lines.append(f"   Strategy: {swarm.strategy.value} | Workers: {len(swarm.workers)}")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to list swarms: {e}")
        return f"Error listing swarms: {e}"


def swarm_stats(user_id: str = "default") -> str:
    """Get swarm statistics for a user.

    user_id: User identifier

    Returns:
        Statistics about swarm usage.
    """
    try:
        orchestrator = _get_swarm_orchestrator()
        stats = orchestrator.get_stats(user_id)

        lines = [
            f"📊 Swarm Statistics",
            f"",
            f"Total Swarms: {stats['total_swarms']}",
            f"  ⏳ Pending: {stats['pending']}",
            f"  🔄 Running: {stats['running']}",
            f"  ✅ Completed: {stats['completed']}",
            f"  ❌ Failed: {stats['failed']}",
            f"",
            f"Concurrent Slots: {stats['active_slots']}/{stats['max_concurrent']} used",
            f"Available Slots: {stats['available_slots']}",
            f"",
            f"Average Confidence: {stats['avg_confidence']:.2f}",
            f"Average Execution Time: {stats['avg_execution_time']:.2f}s"
        ]

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Failed to get swarm stats: {e}")
        return f"Error getting stats: {e}"


async def swarm_message(
    swarm_id: str,
    message: str,
    from_agent: str = "user",
    to_agent: str = None
) -> str:
    """Send a message to agents in a swarm for inter-agent communication.

    swarm_id: The swarm ID to send message to
    message: The message content to send
    from_agent: The sender agent name (default: "user")
    to_agent: Optional specific recipient agent (None for broadcast to all)

    Returns:
        Success or error message.
    """
    try:
        orchestrator = _get_swarm_orchestrator()

        # Validate swarm exists
        swarm_info = orchestrator.get_status(swarm_id)
        if not swarm_info:
            return f"❌ Swarm {swarm_id} not found"

        # Validate from_agent exists in the swarm
        all_agents = swarm_info.workers.copy()
        if swarm_info.coordinator:
            all_agents.append(swarm_info.coordinator)

        if from_agent != "user" and from_agent not in all_agents:
            return f"❌ Agent '{from_agent}' is not part of swarm {swarm_id}"

        # Validate to_agent if specified
        if to_agent and to_agent not in all_agents:
            return f"❌ Agent '{to_agent}' is not part of swarm {swarm_id}"

        message_id = orchestrator.send_message(
            swarm_id=swarm_id,
            from_agent=from_agent,
            message=message,
            to_agent=to_agent
        )

        recipient = f"all agents" if not to_agent else f"agent '{to_agent}'"
        return f"✅ Message sent to {recipient} in swarm {swarm_id} (ID: {message_id})"
    except Exception as e:
        logger.error(f"Failed to send swarm message: {e}")
        return f"Error sending message: {e}"

