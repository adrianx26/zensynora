"""
Swarm Orchestrator - Main entry point for swarm operations.

Coordinates swarm creation, task assignment, execution, and result retrieval.
Integrates with the existing MyClaw agent registry and configuration.
"""

import asyncio
import logging
import uuid
from typing import Any, Dict, List, Optional

from .models import (
    SwarmConfig, SwarmInfo, SwarmTask, SwarmResult,
    SwarmStrategy, AggregationMethod, TaskStatus, MessageType,
    ActiveExecution
)
from .storage import SwarmStorage
from .strategies import AggregationEngine, get_strategy

# Import SQLitePool for shared connection (Optimization 4.2)
try:
    from myclaw.memory import SQLitePool
except ImportError:
    SQLitePool = None

logger = logging.getLogger(__name__)


class SwarmOrchestrator:
    """
    Orchestrates agent swarm operations.
    
    The orchestrator is responsible for:
    - Creating and managing swarm instances
    - Assigning tasks to swarms
    - Executing swarm strategies
    - Managing swarm lifecycle
    - Providing status and results
    
    Example:
        orchestrator = SwarmOrchestrator(agent_registry, config)
        
        # Create a swarm
        swarm_id = await orchestrator.create_swarm(
            SwarmConfig(
                name="research_team",
                strategy=SwarmStrategy.PARALLEL,
                workers=["agent1", "agent2", "agent3"],
                aggregation_method=AggregationMethod.SYNTHESIS
            ),
            user_id="user1"
        )
        
        # Execute a task
        result = await orchestrator.execute_task(
            swarm_id,
            "Research the latest AI developments"
        )
    """
    
    def __init__(
        self,
        agent_registry: Dict[str, Any],
        config: Optional[Any] = None,
        storage: Optional[SwarmStorage] = None
    ):
        """
        Initialize the orchestrator.
        
        Args:
            agent_registry: Mapping of agent names to Agent instances
            config: Application configuration (optional)
            storage: SwarmStorage instance (optional, creates default if None)
        """
        self.agent_registry = agent_registry
        self.config = config
        
        # Optimization 4.2: Use shared SQLite pool for swarm storage
        # This reduces connection overhead when used alongside other storage
        if storage is None:
            pool = SQLitePool if SQLitePool else None
            self.storage = SwarmStorage(pool=pool)
        else:
            self.storage = storage
        
        # Track active swarm executions
        self._active_executions: Dict[str, asyncio.Task] = {}
        
        # Semaphore for concurrency control
        self._semaphore: Optional[asyncio.Semaphore] = None
        
        # Result cache for faster retrieval
        self._result_cache: Dict[str, Any] = {}
        
        # Load swarm configuration
        self._load_config()
    
    def _load_config(self):
        """Load swarm configuration from app config."""
        self.enabled = True
        self.max_concurrent_swarms = 3
        self.default_timeout = 300
        
        if self.config and hasattr(self.config, 'swarm'):
            swarm_config = self.config.swarm
            self.enabled = getattr(swarm_config, 'enabled', True)
            self.max_concurrent_swarms = getattr(swarm_config, 'max_concurrent_swarms', 3)
            self.default_timeout = getattr(swarm_config, 'timeout_seconds', 300)
        
        # Initialize semaphore for concurrency control
        self._semaphore = asyncio.Semaphore(self.max_concurrent_swarms)
    
    async def create_swarm(
        self,
        config: SwarmConfig,
        user_id: str = "default"
    ) -> str:
        """
        Create a new swarm.
        
        Args:
            config: Swarm configuration
            user_id: User owning the swarm
            
        Returns:
            Swarm ID
            
        Raises:
            RuntimeError: If swarms are disabled or limits exceeded
            ValueError: If configuration is invalid
        """
        if not self.enabled:
            raise RuntimeError("Swarm functionality is disabled")
        
        # Check concurrent limit
        active_count = self.storage.count_active_swarms(user_id)
        if active_count >= self.max_concurrent_swarms:
            raise RuntimeError(
                f"Maximum concurrent swarms ({self.max_concurrent_swarms}) reached. "
                "Terminate some swarms before creating new ones."
            )
        
        # Validate agents exist
        all_agents = config.workers.copy()
        if config.coordinator:
            all_agents.append(config.coordinator)
        
        missing = [name for name in all_agents if name not in self.agent_registry]
        if missing:
            available = ", ".join(self.agent_registry.keys())
            raise ValueError(
                f"Agents not found in registry: {', '.join(missing)}. "
                f"Available: {available}"
            )
        
        # Create swarm in storage
        swarm_id = self.storage.create_swarm(
            name=config.name,
            strategy=config.strategy,
            workers=config.workers,
            coordinator=config.coordinator,
            aggregation_method=config.aggregation_method,
            user_id=user_id,
            max_iterations=config.max_iterations,
            timeout_seconds=config.timeout_seconds or self.default_timeout
        )
        
        logger.info(f"Created swarm {swarm_id} ({config.name}) for user {user_id}")
        return swarm_id
    
    async def execute(
        self,
        swarm_id: str,
        task_description: str,
        input_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ) -> SwarmResult:
        """4.1: Execute a task using the specified swarm with timeout enforcement.
        
        This is the primary method for executing swarm tasks with proper timeout
        and cancellation handling.
        
        Args:
            swarm_id: Swarm identifier
            task_description: Task description/prompt
            input_data: Optional structured input data
            timeout: Optional timeout in seconds (uses default if not specified)
            
        Returns:
            SwarmResult with aggregated output
        """
        return await self.execute_task(swarm_id, task_description, input_data, timeout)
    
    async def execute_task(
        self,
        swarm_id: str,
        task_description: str,
        input_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ) -> SwarmResult:
        """
        Execute a task using the specified swarm.
        
        Args:
            swarm_id: Swarm identifier
            task_description: Task description/prompt
            input_data: Optional structured input data
            timeout: Optional timeout in seconds (uses default if not specified)
            
        Returns:
            SwarmResult with aggregated output
        """
        # Get swarm info
        swarm_info = self.storage.get_swarm(swarm_id)
        if not swarm_info:
            raise ValueError(f"Swarm {swarm_id} not found")
        
        if swarm_info.status == TaskStatus.RUNNING:
            raise RuntimeError(f"Swarm {swarm_id} is already running a task")
        
        # Update status
        self.storage.update_swarm_status(swarm_id, TaskStatus.RUNNING)
        
        # Create task record
        task_id = self.storage.create_task(
            swarm_id=swarm_id,
            description=task_description,
            input_data=input_data
        )
        
        task = SwarmTask(
            id=task_id,
            swarm_id=swarm_id,
            description=task_description,
            input_data=input_data or {}
        )
        
        # Create aggregation engine
        aggregator = AggregationEngine(swarm_info.aggregation_method)
        
        # Get strategy
        strategy = get_strategy(
            swarm_info.strategy,
            aggregator,
            self.agent_registry,
            self.storage
        )
        
        # Execute with timeout (use provided timeout or fall back to default)
        timeout = timeout if timeout is not None else self.default_timeout
        try:
            result = await asyncio.wait_for(
                strategy.execute(
                    task=task,
                    workers=swarm_info.workers,
                    coordinator=swarm_info.coordinator
                ),
                timeout=timeout
            )
            
            # Update swarm status
            self.storage.update_swarm_status(swarm_id, TaskStatus.COMPLETED)
            
            # Save result
            self.storage.save_result(result)
            
            # Log completion
            logger.info(
                f"Swarm {swarm_id} completed in {result.execution_time_seconds:.2f}s "
                f"with confidence {result.confidence_score:.2f}"
            )
            
            return result
            
        except asyncio.TimeoutError:
            logger.error(f"Swarm {swarm_id} timed out after {timeout}s")
            self.storage.update_swarm_status(swarm_id, TaskStatus.FAILED)
            
            # Create timeout result
            result = SwarmResult(
                swarm_id=swarm_id,
                aggregation_method=swarm_info.aggregation_method,
                final_result=f"Error: Swarm execution timed out after {timeout} seconds",
                confidence_score=0.0,
                execution_time_seconds=timeout
            )
            self.storage.save_result(result)
            return result
            
        except Exception as e:
            logger.exception(f"Swarm {swarm_id} execution failed: {e}")
            self.storage.update_swarm_status(swarm_id, TaskStatus.FAILED)
            
            # Create error result
            result = SwarmResult(
                swarm_id=swarm_id,
                aggregation_method=swarm_info.aggregation_method,
                final_result=f"Error: {str(e)}",
                confidence_score=0.0
            )
            self.storage.save_result(result)
            return result
    
    async def execute_task_async(
        self,
        swarm_id: str,
        task_description: str,
        input_data: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None
    ) -> str:
        """
        Start async task execution and return immediately.
        
        Args:
            swarm_id: Swarm identifier
            task_description: Task description
            input_data: Optional input data
            timeout: Optional timeout in seconds
            
        Returns:
            Task ID for status tracking
        """
        task_id = f"async_task_{uuid.uuid4().hex[:8]}"
        
        # Save execution state for crash recovery (Optimization 4.4)
        self.storage.save_execution_state(
            execution_id=task_id,
            swarm_id=swarm_id,
            task_description=task_description,
            input_data=input_data
        )
        
        # Optimization 4.5: Semaphore-based concurrency control
        # Define the task to run with semaphore
        async def run_with_semaphore():
            async with self._semaphore:
                return await self.execute_task(swarm_id, task_description, input_data, timeout)

        # Create async task
        execution_task = asyncio.create_task(run_with_semaphore())
        
        self._active_executions[task_id] = execution_task
        
        # Clean up when done
        def cleanup(done_task):
            # Remove from active executions
            self._active_executions.pop(task_id, None)
            # Remove execution state from persistent storage (Optimization 4.4)
            self.storage.remove_execution_state(task_id)
        
        execution_task.add_done_callback(cleanup)
        
        return task_id
    
    def get_status(self, swarm_id: str) -> Optional[SwarmInfo]:
        """
        Get current status of a swarm.
        
        Args:
            swarm_id: Swarm identifier
            
        Returns:
            SwarmInfo or None if not found
        """
        return self.storage.get_swarm(swarm_id)
    
    def get_result(self, swarm_id: str) -> Optional[SwarmResult]:
        """
        Get the result of a completed swarm execution.
        
        Args:
            swarm_id: Swarm identifier
            
        Returns:
            SwarmResult or None if not found
        """
        return self.storage.get_result(swarm_id)
    
    async def terminate_swarm(self, swarm_id: str) -> bool:
        """
        Terminate a running swarm.
        
        Args:
            swarm_id: Swarm identifier
            
        Returns:
            True if terminated, False if not running or not found
        """
        swarm_info = self.storage.get_swarm(swarm_id)
        if not swarm_info:
            return False
        
        if swarm_info.status != TaskStatus.RUNNING:
            return False
        
        # Cancel any active executions for this swarm
        # Note: This requires tracking task-to-swarm mapping
        # For now, just update status
        
        self.storage.update_swarm_status(swarm_id, TaskStatus.TERMINATED)
        
        # Save termination result
        result = SwarmResult(
            swarm_id=swarm_id,
            aggregation_method=swarm_info.aggregation_method,
            final_result="Swarm terminated by user",
            confidence_score=0.0
        )
        self.storage.save_result(result)
        
        logger.info(f"Terminated swarm {swarm_id}")
        return True
    
    def list_swarms(
        self,
        user_id: str = "default",
        status: Optional[TaskStatus] = None
    ) -> List[SwarmInfo]:
        """
        List swarms for a user.
        
        Args:
            user_id: User identifier
            status: Optional status filter
            
        Returns:
            List of SwarmInfo
        """
        return self.storage.list_swarms(user_id, status)
    
    def send_message(
        self,
        swarm_id: str,
        from_agent: str,
        message: str,
        to_agent: Optional[str] = None
    ) -> int:
        """
        Send a message within a swarm.
        
        Args:
            swarm_id: Swarm identifier
            from_agent: Sending agent name
            message: Message content
            to_agent: Recipient agent (None for broadcast)
            
        Returns:
            Message ID
        """
        return self.storage.add_message(
            swarm_id=swarm_id,
            from_agent=from_agent,
            content=message,
            to_agent=to_agent,
            message_type=MessageType.BROADCAST if not to_agent else MessageType.QUERY
        )
    
    def get_messages(
        self,
        swarm_id: str,
        to_agent: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Get messages from a swarm.
        
        Args:
            swarm_id: Swarm identifier
            to_agent: Filter by recipient (None for all)
            limit: Maximum messages to return
            
        Returns:
            List of message dictionaries
        """
        messages = self.storage.get_messages(swarm_id, to_agent, limit)
        return [msg.to_dict() for msg in messages]
    
    def delete_swarm(self, swarm_id: str) -> bool:
        """
        Delete a swarm and all its data.
        
        Args:
            swarm_id: Swarm identifier
            
        Returns:
            True if deleted, False if not found
        """
        return self.storage.delete_swarm(swarm_id)
    
    def cleanup_old_swarms(self, days: int = 30) -> int:
        """
        Clean up swarms older than specified days.
        
        Args:
            days: Age threshold in days
            
        Returns:
            Number of swarms deleted
        """
        return self.storage.cleanup_old_swarms(days)
    
    def get_stats(self, user_id: str = "default") -> Dict[str, Any]:
        """
        Get swarm statistics for a user.
        
        Args:
            user_id: User identifier
            
        Returns:
            Statistics dictionary
        """
        all_swarms = self.storage.list_swarms(user_id)
        
        total = len(all_swarms)
        pending = sum(1 for s in all_swarms if s.status == TaskStatus.PENDING)
        running = sum(1 for s in all_swarms if s.status == TaskStatus.RUNNING)
        completed = sum(1 for s in all_swarms if s.status == TaskStatus.COMPLETED)
        failed = sum(1 for s in all_swarms if s.status == TaskStatus.FAILED)
        
        # Get recent results
        recent_results = [
            self.storage.get_result(s.id)
            for s in all_swarms[-10:]  # Last 10
            if s.status == TaskStatus.COMPLETED
        ]
        
        avg_confidence = 0.0
        avg_execution_time = 0.0
        
        if recent_results:
            avg_confidence = sum(r.confidence_score for r in recent_results if r) / len(recent_results)
            avg_execution_time = sum(r.execution_time_seconds for r in recent_results if r) / len(recent_results)
        
        return {
            "total_swarms": total,
            "pending": pending,
            "running": running,
            "completed": completed,
            "failed": failed,
            "max_concurrent": self.max_concurrent_swarms,
            "active_slots": running,
            "available_slots": self.max_concurrent_swarms - self.storage.count_active_swarms(user_id),
            "avg_confidence": round(avg_confidence, 2),
            "avg_execution_time": round(avg_execution_time, 2),
        }
    
    def load_active_executions(self) -> List[Dict[str, Any]]:
        """Load and recover active executions from persistent storage.
        
        Optimization 4.4: Persistent active execution tracking
        
        This method is called on orchestrator startup to recover any executions
        that were running when the orchestrator last stopped. It loads the
        execution states from SQLite and returns information about them.
        
        The caller can use this information to decide whether to:
        - Reschedule/resume the execution
        - Mark the execution as failed/terminated
        - Notify users about interrupted executions
        
        Returns:
            List of dictionaries containing execution information:
            - execution_id: Unique identifier
            - swarm_id: Swarm being executed
            - task_description: Task description
            - input_data: Input data
            - started_at: When execution started
            - updated_at: Last status update
        """
        # First, recover any stale executions (likely crashed)
        recovered_count = self.storage.recover_stale_executions()
        
        # Load all active executions
        active_executions = self.storage.load_active_executions()
        
        logger.info(f"Loaded {len(active_executions)} active executions from storage")
        
        # Convert to dictionaries for return
        return [
            {
                "execution_id": exec.execution_id,
                "swarm_id": exec.swarm_id,
                "task_description": exec.task_description,
                "input_data": exec.input_data,
                "started_at": exec.started_at.isoformat(),
                "updated_at": exec.updated_at.isoformat(),
            }
            for exec in active_executions
        ]
