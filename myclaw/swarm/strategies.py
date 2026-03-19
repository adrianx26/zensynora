"""
Swarm execution strategies.

Implements different patterns for coordinating multiple agents:
- Parallel: All agents work simultaneously
- Sequential: Agents work in a pipeline
- Hierarchical: Coordinator delegates to workers
- Voting: Consensus-based decision making
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any

from .models import (
    SwarmTask, SwarmResult, AgentResult,
    AggregationMethod, TaskStatus, SwarmStrategy
)

logger = logging.getLogger(__name__)


class AggregationEngine:
    """Engine for aggregating results from multiple agents."""
    
    def __init__(self, method: AggregationMethod, provider=None, model: str = None):
        """
        Initialize aggregation engine.
        
        Args:
            method: Aggregation method to use
            provider: LLM provider for synthesis (optional)
            model: Model name for synthesis (optional)
        """
        self.method = method
        self.provider = provider
        self.model = model
    
    def aggregate(self, results: Dict[str, AgentResult]) -> tuple[str, float]:
        """
        Aggregate agent results into final output.
        
        Returns:
            Tuple of (final_result, confidence_score)
        """
        # Filter successful results only
        successful = {
            k: v for k, v in results.items() 
            if v.success and v.result.strip()
        }
        
        if not successful:
            return "All agents failed to produce results.", 0.0
        
        if self.method == AggregationMethod.CONSENSUS:
            return self._consensus(successful)
        elif self.method == AggregationMethod.BEST_PICK:
            return self._best_pick(successful)
        elif self.method == AggregationMethod.CONCATENATION:
            return self._concatenation(successful)
        elif self.method == AggregationMethod.SYNTHESIS:
            return self._synthesis(successful)
        else:
            return self._synthesis(successful)  # Default
    
    def _consensus(self, results: Dict[str, AgentResult]) -> tuple[str, float]:
        """Pick the most common answer."""
        # Simple exact-match consensus
        answers = [r.result.strip() for r in results.values()]
        
        # Count occurrences
        from collections import Counter
        counts = Counter(answers)
        
        if not counts:
            return "No consensus could be reached.", 0.0
        
        winner, count = counts.most_common(1)[0]
        confidence = count / len(answers)
        
        # Add consensus summary
        result_text = f"[Consensus: {count}/{len(answers)} agents agree]\n\n{winner}"
        return result_text, confidence
    
    def _best_pick(self, results: Dict[str, AgentResult]) -> tuple[str, float]:
        """Select the best result based on length and quality heuristics."""
        scored = []
        
        for agent_name, result in results.items():
            text = result.result.strip()
            # Simple scoring: prefer longer but not too long responses
            # that contain structured elements
            score = 0
            
            # Length score (prefer medium-length)
            length = len(text)
            if 100 < length < 2000:
                score += 10
            elif length >= 2000:
                score += 5
            else:
                score += length / 100
            
            # Structure bonus
            if any(marker in text for marker in ['```', '**', '#', '- ', '1. ']):
                score += 5
            
            # Completeness markers
            if text.endswith('.') or text.endswith('!'):
                score += 2
            
            scored.append((agent_name, result, score))
        
        if not scored:
            return "Could not determine best result.", 0.0
        
        # Sort by score
        scored.sort(key=lambda x: x[2], reverse=True)
        winner_name, winner_result, winner_score = scored[0]
        
        # Confidence based on margin over second place
        if len(scored) > 1:
            margin = winner_score - scored[1][2]
            confidence = min(0.5 + (margin / 20), 1.0)
        else:
            confidence = 0.7
        
        result_text = f"[Best result from {winner_name}]\n\n{winner_result.result}"
        return result_text, confidence
    
    def _concatenation(self, results: Dict[str, AgentResult]) -> tuple[str, float]:
        """Concatenate all results with clear separation."""
        parts = []
        for agent_name, result in sorted(results.items()):
            parts.append(f"## {agent_name}\n{result.result}\n")
        
        combined = "\n---\n\n".join(parts)
        confidence = sum(1 for r in results.values() if r.success) / len(results)
        
        header = f"[Combined results from {len(results)} agents]\n\n"
        return header + combined, confidence
    
    def _synthesis(self, results: Dict[str, AgentResult]) -> tuple[str, float]:
        """Synthesize results into coherent output."""
        # For now, use concatenation with a synthesis header
        # Full LLM-based synthesis would require provider access
        
        if not self.provider or not self.model:
            # Fall back to concatenation with summary
            return self._concatenation_with_summary(results)
        
        # If provider available, we could do LLM synthesis
        # For now, use the concatenation approach
        return self._concatenation_with_summary(results)
    
    def _concatenation_with_summary(self, results: Dict[str, AgentResult]) -> tuple[str, float]:
        """Concatenate with a simple summary."""
        parts = []
        key_points = []
        
        for agent_name, result in sorted(results.items()):
            parts.append(f"## {agent_name}\n{result.result}\n")
            # Extract first line as key point
            first_line = result.result.strip().split('\n')[0][:100]
            key_points.append(f"- {agent_name}: {first_line}...")
        
        summary = "## Summary of Agent Responses\n" + "\n".join(key_points)
        combined = "\n---\n\n".join(parts)
        
        confidence = sum(1 for r in results.values() if r.success) / len(results)
        
        full_result = f"[Synthesized from {len(results)} agents]\n\n{summary}\n\n---\n\n{combined}"
        return full_result, confidence


class BaseStrategy(ABC):
    """Abstract base class for swarm strategies."""
    
    def __init__(
        self,
        aggregator: AggregationEngine,
        agent_registry: Dict[str, Any],
        storage=None
    ):
        """
        Initialize strategy.
        
        Args:
            aggregator: Result aggregation engine
            agent_registry: Mapping of agent names to Agent instances
            storage: Optional storage for progress tracking
        """
        self.aggregator = aggregator
        self.agent_registry = agent_registry
        self.storage = storage
    
    @abstractmethod
    async def execute(
        self,
        task: SwarmTask,
        workers: List[str],
        coordinator: Optional[str] = None
    ) -> SwarmResult:
        """Execute the swarm strategy."""
        pass
    
    def _get_agent(self, name: str):
        """Get agent from registry."""
        return self.agent_registry.get(name)
    
    async def _execute_agent(
        self,
        agent_name: str,
        prompt: str,
        user_id: str = "__swarm__"
    ) -> AgentResult:
        """Execute a single agent and return result."""
        agent = self._get_agent(agent_name)
        if not agent:
            return AgentResult(
                agent_name=agent_name,
                success=False,
                error=f"Agent '{agent_name}' not found in registry"
            )
        
        start_time = time.time()
        try:
            result_text = await agent.think(prompt, user_id=user_id, _depth=1)
            execution_time = time.time() - start_time
            
            return AgentResult(
                agent_name=agent_name,
                result=result_text,
                execution_time_seconds=execution_time,
                success=True
            )
        except Exception as e:
            logger.error(f"Agent {agent_name} execution failed: {e}")
            return AgentResult(
                agent_name=agent_name,
                success=False,
                error=str(e),
                execution_time_seconds=time.time() - start_time
            )


class ParallelStrategy(BaseStrategy):
    """
    Parallel execution strategy.
    
    All workers receive the same task and execute simultaneously.
    Results are aggregated using the configured method.
    """
    
    async def execute(
        self,
        task: SwarmTask,
        workers: List[str],
        coordinator: Optional[str] = None
    ) -> SwarmResult:
        """Execute task in parallel across all workers."""
        logger.info(f"Executing parallel strategy with {len(workers)} workers")
        
        start_time = time.time()
        
        # Create execution tasks for all workers
        agent_tasks = [
            self._execute_agent(worker, task.description)
            for worker in workers
        ]
        
        # Execute all in parallel
        results_list = await asyncio.gather(*agent_tasks, return_exceptions=True)
        
        # Convert to dict
        results = {}
        for i, worker in enumerate(workers):
            if isinstance(results_list[i], Exception):
                results[worker] = AgentResult(
                    agent_name=worker,
                    success=False,
                    error=str(results_list[i])
                )
            else:
                results[worker] = results_list[i]
        
        # Aggregate results
        final_result, confidence = self.aggregator.aggregate(results)
        
        execution_time = time.time() - start_time
        
        return SwarmResult(
            swarm_id=task.swarm_id,
            aggregation_method=self.aggregator.method,
            individual_results=results,
            final_result=final_result,
            confidence_score=confidence,
            execution_time_seconds=execution_time
        )


class SequentialStrategy(BaseStrategy):
    """
    Sequential execution strategy (pipeline).
    
    Workers execute in sequence, with each worker's output becoming
    the next worker's input. Useful for multi-stage processing.
    """
    
    async def execute(
        self,
        task: SwarmTask,
        workers: List[str],
        coordinator: Optional[str] = None
    ) -> SwarmResult:
        """Execute task through worker pipeline."""
        logger.info(f"Executing sequential strategy with {len(workers)} stages")
        
        start_time = time.time()
        current_input = task.description
        results = {}
        
        for i, worker in enumerate(workers):
            logger.debug(f"Pipeline stage {i+1}/{len(workers)}: {worker}")
            
            # Create stage-specific prompt
            stage_prompt = f"[Stage {i+1}/{len(workers)}] {current_input}"
            
            # Execute worker
            result = await self._execute_agent(worker, stage_prompt)
            results[worker] = result
            
            if not result.success:
                logger.warning(f"Pipeline failed at stage {i+1} ({worker})")
                break
            
            # Output becomes next input
            current_input = result.result
        
        # For sequential, final result is the last successful output
        final_result = current_input
        
        # Confidence based on completion rate
        successful = sum(1 for r in results.values() if r.success)
        confidence = successful / len(workers)
        
        execution_time = time.time() - start_time
        
        return SwarmResult(
            swarm_id=task.swarm_id,
            aggregation_method=self.aggregator.method,
            individual_results=results,
            final_result=final_result,
            confidence_score=confidence,
            execution_time_seconds=execution_time
        )


class HierarchicalStrategy(BaseStrategy):
    """
    Hierarchical execution strategy.
    
    A coordinator agent decomposes the task, assigns subtasks to workers,
    and synthesizes the final result.
    """
    
    async def execute(
        self,
        task: SwarmTask,
        workers: List[str],
        coordinator: Optional[str] = None
    ) -> SwarmResult:
        """Execute task using coordinator and workers."""
        if not coordinator:
            from ..exceptions import SwarmValidationError
            raise SwarmValidationError("Hierarchical strategy requires a coordinator agent")
        
        logger.info(f"Executing hierarchical strategy with coordinator {coordinator} and {len(workers)} workers")
        
        start_time = time.time()
        
        # Step 1: Coordinator creates execution plan
        plan_prompt = f"""You are the coordinator of an agent swarm. Create a clear execution plan for this task.

Task: {task.description}

Available workers: {', '.join(workers)}

Create a plan that:
1. Breaks the task into subtasks
2. Assigns each subtask to appropriate workers
3. Specifies how results should be combined

Respond with a simple numbered list of subtasks and worker assignments."""
        
        plan_result = await self._execute_agent(coordinator, plan_prompt)
        
        if not plan_result.success:
            return SwarmResult(
                swarm_id=task.swarm_id,
                aggregation_method=self.aggregator.method,
                individual_results={coordinator: plan_result},
                final_result=f"Coordinator failed: {plan_result.error}",
                confidence_score=0.0,
                execution_time_seconds=time.time() - start_time
            )
        
        # Step 2: Execute with all workers in parallel (simplified)
        # In a more complex version, we'd parse the plan and assign specific subtasks
        worker_prompt = f"""Execute this task according to the coordinator's plan:

Original Task: {task.description}

Coordinator's Plan:
{plan_result.result}

Your role: Execute your part of this plan and provide your results."""
        
        worker_tasks = [
            self._execute_agent(worker, worker_prompt)
            for worker in workers
        ]
        
        worker_results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        
        # Convert to dict
        results = {coordinator: plan_result}
        for i, worker in enumerate(workers):
            if isinstance(worker_results[i], Exception):
                results[worker] = AgentResult(
                    agent_name=worker,
                    success=False,
                    error=str(worker_results[i])
                )
            else:
                results[worker] = worker_results[i]
        
        # Step 3: Coordinator synthesizes final result
        synthesis_prompt = f"""As coordinator, synthesize the final result from all worker outputs.

Original Task: {task.description}

Your Plan:
{plan_result.result}

Worker Results:
{chr(10).join(f"{name}: {r.result[:500]}..." for name, r in results.items() if r.success and name != coordinator)}

Provide a coherent final result that integrates all the worker outputs."""
        
        synthesis_result = await self._execute_agent(coordinator, synthesis_prompt)
        
        if synthesis_result.success:
            final_result = synthesis_result.result
        else:
            # Fall back to aggregation
            final_result, _ = self.aggregator.aggregate(
                {k: v for k, v in results.items() if k != coordinator}
            )
        
        successful = sum(1 for r in results.values() if r.success)
        confidence = successful / len(results)
        
        execution_time = time.time() - start_time
        
        return SwarmResult(
            swarm_id=task.swarm_id,
            aggregation_method=self.aggregator.method,
            individual_results=results,
            final_result=final_result,
            confidence_score=confidence,
            execution_time_seconds=execution_time
        )


class VotingStrategy(BaseStrategy):
    """
    Voting execution strategy.
    
    Multiple agents solve the same problem independently.
    The most common answer wins (consensus).
    """
    
    async def execute(
        self,
        task: SwarmTask,
        workers: List[str],
        coordinator: Optional[str] = None
    ) -> SwarmResult:
        """Execute task with voting across workers."""
        logger.info(f"Executing voting strategy with {len(workers)} voters")
        
        start_time = time.time()
        
        # Add voting instructions to prompt
        voting_prompt = f"""{task.description}

Provide a concise, clear answer. Your response will be compared with other agents' responses to reach consensus. Be specific and direct."""
        
        # Get solutions from all workers
        worker_tasks = [
            self._execute_agent(worker, voting_prompt)
            for worker in workers
        ]
        
        results_list = await asyncio.gather(*worker_tasks, return_exceptions=True)
        
        # Convert to dict
        results = {}
        for i, worker in enumerate(workers):
            if isinstance(results_list[i], Exception):
                results[worker] = AgentResult(
                    agent_name=worker,
                    success=False,
                    error=str(results_list[i])
                )
            else:
                results[worker] = results_list[i]
        
        # Use consensus aggregation
        consensus_aggregator = AggregationEngine(AggregationMethod.CONSENSUS)
        final_result, confidence = consensus_aggregator.aggregate(results)
        
        execution_time = time.time() - start_time
        
        return SwarmResult(
            swarm_id=task.swarm_id,
            aggregation_method=AggregationMethod.CONSENSUS,
            individual_results=results,
            final_result=final_result,
            confidence_score=confidence,
            execution_time_seconds=execution_time
        )


# Strategy factory
def get_strategy(
    strategy_type: SwarmStrategy,
    aggregator: AggregationEngine,
    agent_registry: Dict[str, Any],
    storage=None
) -> BaseStrategy:
    """
    Get strategy instance by type.
    
    Args:
        strategy_type: Type of strategy to create
        aggregator: Aggregation engine
        agent_registry: Agent registry mapping
        storage: Optional storage instance
        
    Returns:
        Strategy instance
    """
    strategies = {
        SwarmStrategy.PARALLEL: ParallelStrategy,
        SwarmStrategy.SEQUENTIAL: SequentialStrategy,
        SwarmStrategy.HIERARCHICAL: HierarchicalStrategy,
        SwarmStrategy.VOTING: VotingStrategy,
    }
    
    strategy_class = strategies.get(strategy_type)
    if not strategy_class:
        from ..exceptions import SwarmValidationError
        raise SwarmValidationError(f"Unknown strategy type: {strategy_type}")
    
    return strategy_class(aggregator, agent_registry, storage)
