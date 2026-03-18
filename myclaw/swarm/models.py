"""
Data models for the Agent Swarm system.

Defines the core data structures for swarm configuration, tasks, results,
and inter-agent communication.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import json


class SwarmStrategy(Enum):
    """Available swarm execution strategies."""
    PARALLEL = "parallel"       # All agents work simultaneously
    SEQUENTIAL = "sequential"   # Agents work in pipeline
    HIERARCHICAL = "hierarchical"  # Coordinator + workers
    VOTING = "voting"           # Consensus-based decision


class AggregationMethod(Enum):
    """Methods for aggregating results from multiple agents."""
    CONSENSUS = "consensus"         # Most common answer
    BEST_PICK = "best_pick"         # Quality-based selection
    CONCATENATION = "concatenation" # Combine all outputs
    SYNTHESIS = "synthesis"         # LLM-based summarization


class TaskStatus(Enum):
    """Status of a swarm or task."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"


class MessageType(Enum):
    """Types of inter-agent messages."""
    TASK = "task"           # Task assignment
    RESULT = "result"       # Task completion
    QUERY = "query"         # Information request
    BROADCAST = "broadcast" # General message to all
    STATUS = "status"       # Status update


@dataclass
class SwarmConfig:
    """Configuration for a swarm instance.
    
    Attributes:
        name: Human-readable name for the swarm
        strategy: Execution strategy (parallel, sequential, etc.)
        coordinator: Name of coordinator agent (required for hierarchical)
        workers: List of worker agent names
        aggregation_method: How to combine results
        max_iterations: Maximum refinement iterations
        timeout_seconds: Execution timeout
        metadata: Additional configuration options
    """
    name: str
    strategy: SwarmStrategy
    workers: List[str]
    coordinator: Optional[str] = None
    aggregation_method: AggregationMethod = AggregationMethod.SYNTHESIS
    max_iterations: int = 1
    timeout_seconds: int = 300
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Validate configuration."""
        if self.strategy == SwarmStrategy.HIERARCHICAL and not self.coordinator:
            raise ValueError("Hierarchical strategy requires a coordinator agent")
        if not self.workers:
            raise ValueError("At least one worker agent is required")


@dataclass
class SwarmTask:
    """A task assigned to the swarm.
    
    Attributes:
        id: Unique task identifier
        swarm_id: Reference to parent swarm
        description: Task description/prompt
        input_data: Structured input data
        status: Current execution status
        assigned_agents: Agents assigned to this task
        created_at: Creation timestamp
        started_at: Execution start timestamp
        completed_at: Completion timestamp
    """
    id: str
    swarm_id: str
    description: str
    input_data: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING
    assigned_agents: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "swarm_id": self.swarm_id,
            "description": self.description,
            "input_data": self.input_data,
            "status": self.status.value,
            "assigned_agents": self.assigned_agents,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwarmTask":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            swarm_id=data["swarm_id"],
            description=data["description"],
            input_data=data.get("input_data", {}),
            status=TaskStatus(data.get("status", "pending")),
            assigned_agents=data.get("assigned_agents", []),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else None,
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
        )


@dataclass
class SwarmMessage:
    """Message for inter-agent communication.
    
    Attributes:
        id: Message identifier
        swarm_id: Reference to swarm
        from_agent: Sender agent name
        to_agent: Recipient agent name (None = broadcast)
        message_type: Type of message
        content: Message content
        timestamp: When message was sent
    """
    id: Optional[int] = None
    swarm_id: str = ""
    from_agent: str = ""
    to_agent: Optional[str] = None
    message_type: MessageType = MessageType.BROADCAST
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "swarm_id": self.swarm_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "message_type": self.message_type.value,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class AgentResult:
    """Result from a single agent.
    
    Attributes:
        agent_name: Name of the agent
        result: The agent's output
        execution_time_seconds: How long the agent took
        success: Whether execution succeeded
        error: Error message if failed
    """
    agent_name: str
    result: str = ""
    execution_time_seconds: float = 0.0
    success: bool = True
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agent_name": self.agent_name,
            "result": self.result,
            "execution_time_seconds": self.execution_time_seconds,
            "success": self.success,
            "error": self.error,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentResult":
        """Create from dictionary."""
        return cls(
            agent_name=data["agent_name"],
            result=data.get("result", ""),
            execution_time_seconds=data.get("execution_time_seconds", 0.0),
            success=data.get("success", True),
            error=data.get("error"),
        )


@dataclass
class SwarmResult:
    """Final aggregated result from a swarm execution.
    
    Attributes:
        swarm_id: Reference to swarm
        aggregation_method: Method used to aggregate
        individual_results: Results from each agent
        final_result: The aggregated final output
        confidence_score: Confidence in the result (0-1)
        execution_time_seconds: Total execution time
        created_at: When result was created
        metadata: Additional result data
    """
    swarm_id: str
    aggregation_method: AggregationMethod
    individual_results: Dict[str, AgentResult] = field(default_factory=dict)
    final_result: str = ""
    confidence_score: float = 0.0
    execution_time_seconds: float = 0.0
    created_at: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "swarm_id": self.swarm_id,
            "aggregation_method": self.aggregation_method.value,
            "individual_results": {
                k: v.to_dict() for k, v in self.individual_results.items()
            },
            "final_result": self.final_result,
            "confidence_score": self.confidence_score,
            "execution_time_seconds": self.execution_time_seconds,
            "created_at": self.created_at.isoformat(),
            "metadata": self.metadata,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwarmResult":
        """Create from dictionary."""
        individual_results = {
            k: AgentResult.from_dict(v) 
            for k, v in data.get("individual_results", {}).items()
        }
        return cls(
            swarm_id=data["swarm_id"],
            aggregation_method=AggregationMethod(data.get("aggregation_method", "synthesis")),
            individual_results=individual_results,
            final_result=data.get("final_result", ""),
            confidence_score=data.get("confidence_score", 0.0),
            execution_time_seconds=data.get("execution_time_seconds", 0.0),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            metadata=data.get("metadata", {}),
        )


@dataclass
class SwarmInfo:
    """Information about a running or completed swarm.
    
    Attributes:
        id: Unique swarm identifier
        name: Human-readable name
        strategy: Execution strategy
        status: Current status
        coordinator: Coordinator agent name
        workers: List of worker agent names
        created_at: Creation timestamp
        completed_at: Completion timestamp
        user_id: Owner of the swarm
    """
    id: str
    name: str
    strategy: SwarmStrategy
    status: TaskStatus
    coordinator: Optional[str] = None
    workers: List[str] = field(default_factory=list)
    aggregation_method: AggregationMethod = AggregationMethod.SYNTHESIS
    created_at: datetime = field(default_factory=datetime.now)
    completed_at: Optional[datetime] = None
    user_id: str = "default"
    
@dataclass
class ActiveExecution:
    """Represents an active async execution state for persistence.
    
    Optimization 4.4: Persistent active execution tracking
    
    This tracks async task executions that can survive orchestrator restarts.
    When the orchestrator restarts, it can reload these executions and either
    resume them or mark them as failed/terminated.
    
    Attributes:
        execution_id: Unique identifier for this execution
        swarm_id: Reference to the swarm being executed
        task_description: Description of the task being executed
        input_data: Input data for the task
        status: Current execution status
        started_at: When execution started
        updated_at: Last status update timestamp
    """
    execution_id: str
    swarm_id: str
    task_description: str
    input_data: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.RUNNING
    started_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "execution_id": self.execution_id,
            "swarm_id": self.swarm_id,
            "task_description": self.task_description,
            "input_data": self.input_data,
            "status": self.status.value,
            "started_at": self.started_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ActiveExecution":
        """Create from dictionary."""
        return cls(
            execution_id=data["execution_id"],
            swarm_id=data["swarm_id"],
            task_description=data.get("task_description", ""),
            input_data=data.get("input_data", {}),
            status=TaskStatus(data.get("status", "running")),
            started_at=datetime.fromisoformat(data["started_at"]) if data.get("started_at") else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.now(),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "strategy": self.strategy.value,
            "status": self.status.value,
            "coordinator": self.coordinator,
            "workers": self.workers,
            "aggregation_method": self.aggregation_method.value,
            "created_at": self.created_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "user_id": self.user_id,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SwarmInfo":
        """Create from dictionary."""
        return cls(
            id=data["id"],
            name=data["name"],
            strategy=SwarmStrategy(data.get("strategy", "parallel")),
            status=TaskStatus(data.get("status", "pending")),
            coordinator=data.get("coordinator"),
            workers=data.get("workers", []),
            aggregation_method=AggregationMethod(data.get("aggregation_method", "synthesis")),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.now(),
            completed_at=datetime.fromisoformat(data["completed_at"]) if data.get("completed_at") else None,
            user_id=data.get("user_id", "default"),
        )
