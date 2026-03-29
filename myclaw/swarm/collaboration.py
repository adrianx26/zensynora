"""
Team Collaboration for Multi-agent Systems

Enhances the swarm system with real-time team collaboration capabilities:
- Team chat for agent-to-agent communication
- Shared team context
- Collaboration tools (delegate, ask, share)
- Human-in-the-loop support
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Callable

from .models import SwarmInfo, SwarmMessage, MessageType

logger = logging.getLogger(__name__)


class CollaborationEventType(Enum):
    """Types of collaboration events."""
    MEMBER_JOINED = "member_joined"
    MEMBER_LEFT = "member_left"
    MESSAGE_SENT = "message_sent"
    TASK_ASSIGNED = "task_assigned"
    TASK_COMPLETED = "task_completed"
    CONTEXT_SHARED = "context_shared"
    MENTION = "mention"


@dataclass
class TeamMember:
    """Represents a member of a collaboration team."""
    name: str
    role: str = "worker"
    joined_at: datetime = field(default_factory=datetime.now)
    last_active: datetime = field(default_factory=datetime.now)
    is_online: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CollaborationEvent:
    """An event in the collaboration."""
    id: str = field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    event_type: CollaborationEventType = CollaborationEventType.MESSAGE_SENT
    team_id: str = ""
    member_name: str = ""
    content: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "event_type": self.event_type.value,
            "team_id": self.team_id,
            "member_name": self.member_name,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


@dataclass
class SharedContext:
    """Shared context among team members."""
    id: str = field(default_factory=lambda: f"ctx_{uuid.uuid4().hex[:8]}")
    team_id: str = ""
    key: str = ""
    value: Any = None
    shared_by: str = ""
    shared_at: datetime = field(default_factory=datetime.now)
    expires_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        if self.expires_at:
            return datetime.now() > self.expires_at
        return False


class TeamChat:
    """Team chat for real-time agent communication.
    
    Provides a real-time chat system where agents can communicate
    within their team, send direct messages, and mention other agents.
    """

    def __init__(self, storage=None):
        self._storage = storage
        self._channels: Dict[str, List[SwarmMessage]] = {}
        self._events: Dict[str, List[CollaborationEvent]] = {}
        self._member_presence: Dict[str, Dict[str, TeamMember]] = {}
        self._callbacks: List[Callable] = []

    def register_callback(self, callback: Callable):
        """Register a callback for chat events."""
        self._callbacks.append(callback)

    async def _notify(self, event: CollaborationEvent):
        for callback in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await callback(event)
                else:
                    callback(event)
            except Exception as e:
                logger.error(f"Chat callback error: {e}")

    def create_channel(self, team_id: str) -> str:
        """Create a new chat channel for a team."""
        if team_id not in self._channels:
            self._channels[team_id] = []
            self._events[team_id] = []
            self._member_presence[team_id] = {}
        return team_id

    def join_channel(self, team_id: str, member: TeamMember) -> bool:
        """Add a member to a team's chat channel."""
        if team_id not in self._channels:
            self.create_channel(team_id)
        
        self._member_presence[team_id][member.name] = member
        
        event = CollaborationEvent(
            event_type=CollaborationEventType.MEMBER_JOINED,
            team_id=team_id,
            member_name=member.name,
            content=f"{member.name} joined the team"
        )
        self._events[team_id].append(event)
        asyncio.create_task(self._notify(event))
        
        logger.info(f"{member.name} joined team channel {team_id}")
        return True

    def leave_channel(self, team_id: str, member_name: str) -> bool:
        """Remove a member from a team's chat channel."""
        if team_id not in self._member_presence:
            return False
        
        member = self._member_presence[team_id].pop(member_name, None)
        if member:
            event = CollaborationEvent(
                event_type=CollaborationEventType.MEMBER_LEFT,
                team_id=team_id,
                member_name=member_name,
                content=f"{member_name} left the team"
            )
            self._events[team_id].append(event)
            asyncio.create_task(self._notify(event))
            return True
        return False

    def send_message(
        self,
        team_id: str,
        from_agent: str,
        content: str,
        to_agent: Optional[str] = None,
        mentions: Optional[List[str]] = None
    ) -> SwarmMessage:
        """Send a message in the team channel."""
        if team_id not in self._channels:
            self.create_channel(team_id)
        
        message_type = MessageType.DIRECT if to_agent else MessageType.BROADCAST
        
        message = SwarmMessage(
            swarm_id=team_id,
            from_agent=from_agent,
            to_agent=to_agent,
            message_type=message_type,
            content=content
        )
        
        self._channels[team_id].append(message)
        
        event = CollaborationEvent(
            event_type=CollaborationEventType.MESSAGE_SENT,
            team_id=team_id,
            member_name=from_agent,
            content=content,
            metadata={
                "to_agent": to_agent,
                "mentions": mentions or []
            }
        )
        self._events[team_id].append(event)
        asyncio.create_task(self._notify(event))
        
        return message

    def get_messages(
        self,
        team_id: str,
        limit: int = 100,
        since: Optional[datetime] = None
    ) -> List[SwarmMessage]:
        """Get messages from a team channel."""
        if team_id not in self._channels:
            return []
        
        messages = self._channels[team_id]
        
        if since:
            messages = [m for m in messages if m.timestamp > since]
        
        return messages[-limit:]

    def get_events(
        self,
        team_id: str,
        event_type: Optional[CollaborationEventType] = None,
        limit: int = 50
    ) -> List[CollaborationEvent]:
        """Get collaboration events from a team."""
        if team_id not in self._events:
            return []
        
        events = self._events[team_id]
        
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        
        return events[-limit:]

    def get_online_members(self, team_id: str) -> List[TeamMember]:
        """Get online members of a team."""
        if team_id not in self._member_presence:
            return []
        return list(self._member_presence[team_id].values())

    def set_presence(
        self,
        team_id: str,
        member_name: str,
        is_online: bool
    ) -> bool:
        """Update member presence."""
        if team_id not in self._member_presence:
            return False
        
        member = self._member_presence[team_id].get(member_name)
        if member:
            member.is_online = is_online
            member.last_active = datetime.now()
            return True
        return False


class SharedTeamContext:
    """Shared context storage for team collaboration.
    
    Allows team members to share context, data, and information
    that persists throughout the team's lifecycle.
    """

    def __init__(self):
        self._contexts: Dict[str, Dict[str, SharedContext]] = {}

    def create_team_context(self, team_id: str) -> str:
        """Initialize shared context for a team."""
        if team_id not in self._contexts:
            self._contexts[team_id] = {}
        return team_id

    def share(
        self,
        team_id: str,
        key: str,
        value: Any,
        shared_by: str,
        ttl_seconds: Optional[int] = 3600
    ) -> SharedContext:
        """Share context with the team."""
        if team_id not in self._contexts:
            self.create_team_context(team_id)
        
        expires_at = None
        if ttl_seconds:
            expires_at = datetime.now() + timedelta(seconds=ttl_seconds)
        
        context = SharedContext(
            team_id=team_id,
            key=key,
            value=value,
            shared_by=shared_by,
            expires_at=expires_at
        )
        
        self._contexts[team_id][key] = context
        
        logger.info(f"{shared_by} shared context '{key}' with team {team_id}")
        return context

    def get(self, team_id: str, key: str) -> Optional[Any]:
        """Get shared context by key."""
        if team_id not in self._contexts:
            return None
        
        context = self._contexts[team_id].get(key)
        if context and not context.is_expired():
            return context.value
        return None

    def get_all(self, team_id: str) -> Dict[str, Any]:
        """Get all non-expired shared context for a team."""
        if team_id not in self._contexts:
            return {}
        
        return {
            k: v.value for k, v in self._contexts[team_id].items()
            if not v.is_expired()
        }

    def delete(self, team_id: str, key: str) -> bool:
        """Delete shared context."""
        if team_id not in self._contexts:
            return False
        
        if key in self._contexts[team_id]:
            del self._contexts[team_id][key]
            return True
        return False

    def cleanup_expired(self, team_id: str) -> int:
        """Remove expired context entries."""
        if team_id not in self._contexts:
            return 0
        
        expired_keys = [
            k for k, v in self._contexts[team_id].items()
            if v.is_expired()
        ]
        
        for key in expired_keys:
            del self._contexts[team_id][key]
        
        return len(expired_keys)


class TeamCollaboration:
    """Main team collaboration coordinator.
    
    Integrates team chat, shared context, and collaboration tools
    to enable seamless multi-agent collaboration.
    """

    def __init__(
        self,
        storage=None,
        agent_registry: Optional[Dict[str, Any]] = None
    ):
        self._storage = storage
        self._agent_registry = agent_registry or {}
        self._chat = TeamChat(storage)
        self._shared_context = SharedTeamContext()
        self._teams: Dict[str, Dict[str, Any]] = {}

    @property
    def chat(self) -> TeamChat:
        return self._chat

    @property
    def shared_context(self) -> SharedTeamContext:
        return self._shared_context

    def create_team(
        self,
        team_id: str,
        name: str,
        members: List[str]
    ) -> Dict[str, Any]:
        """Create a new collaboration team."""
        team_info = {
            "id": team_id,
            "name": name,
            "members": members,
            "created_at": datetime.now().isoformat()
        }
        
        self._teams[team_id] = team_info
        self._chat.create_channel(team_id)
        self._shared_context.create_team_context(team_id)
        
        for member_name in members:
            self._chat.join_channel(
                team_id,
                TeamMember(name=member_name, role="worker")
            )
        
        logger.info(f"Created collaboration team {team_id} ({name})")
        return team_info

    def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get team information."""
        return self._teams.get(team_id)

    def list_teams(self) -> List[Dict[str, Any]]:
        """List all teams."""
        return list(self._teams.values())

    def delegate_task(
        self,
        team_id: str,
        from_agent: str,
        to_agent: str,
        task_description: str,
        priority: str = "normal"
    ) -> bool:
        """Delegate a task to another agent in the team."""
        team = self._teams.get(team_id)
        if not team:
            return False
        
        if to_agent not in team["members"]:
            raise ValueError(f"Agent {to_agent} not in team {team_id}")
        
        self._chat.send_message(
            team_id=team_id,
            from_agent=from_agent,
            content=f"[DELEGATE:{priority}] {task_description}",
            to_agent=to_agent
        )
        
        logger.info(f"{from_agent} delegated task to {to_agent} in team {team_id}")
        return True

    def ask_team(
        self,
        team_id: str,
        from_agent: str,
        question: str,
        mentions: Optional[List[str]] = None
    ) -> bool:
        """Ask the team a question."""
        self._chat.send_message(
            team_id=team_id,
            from_agent=from_agent,
            content=f"[QUESTION] {question}",
            mentions=mentions
        )
        return True

    async def summarize_collaboration(
        self,
        team_id: str
    ) -> str:
        """Generate a collaboration summary for the team."""
        team = self._teams.get(team_id)
        if not team:
            return "Team not found"
        
        members = self._chat.get_online_members(team_id)
        messages = self._chat.get_messages(team_id, limit=10)
        context = self._shared_context.get_all(team_id)
        
        summary_parts = [
            f"Team: {team['name']}",
            f"Members: {len(members)} active",
            f"Messages: {len(messages)}",
            f"Shared Context: {len(context)} items"
        ]
        
        return " | ".join(summary_parts)


from datetime import timedelta

__all__ = [
    "CollaborationEventType",
    "TeamMember",
    "CollaborationEvent",
    "SharedContext",
    "TeamChat",
    "SharedTeamContext",
    "TeamCollaboration",
]