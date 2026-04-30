"""Wire-format-stable message envelope for agent-to-agent communication.

The envelope is intentionally tiny. ``payload`` is opaque to the broker
— callers are responsible for whatever schema lives inside.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, Optional


class MessageType(str, Enum):
    """Stable string values — they appear on the wire."""
    REQUEST = "request"
    RESPONSE = "response"
    NOTIFY = "notify"
    HANDOFF = "handoff"
    ERROR = "error"


class MessageEnvelopeError(ValueError):
    """Raised on malformed envelopes (missing required fields, bad type)."""


@dataclass
class AgentMessage:
    """Envelope routed by the broker.

    Field rationale:

    * ``message_id`` is a stable id for tracing.
    * ``correlation_id`` ties responses back to their request.
    * ``trace_id`` survives across hops (matches OTel semantics).
    * ``payload`` is whatever the producer wants to send; consumers must
      validate it against their own schema.
    """
    sender: str
    recipient: str
    message_type: MessageType
    payload: Any = None
    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    correlation_id: Optional[str] = None
    trace_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # ── (de)serialization ──────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["message_type"] = self.message_type.value
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentMessage":
        required = ("sender", "recipient", "message_type")
        for k in required:
            if k not in data:
                raise MessageEnvelopeError(f"Missing required field: {k}")
        try:
            mtype = MessageType(data["message_type"])
        except ValueError as e:
            raise MessageEnvelopeError(f"Unknown message_type: {data['message_type']}") from e
        return cls(
            sender=str(data["sender"]),
            recipient=str(data["recipient"]),
            message_type=mtype,
            payload=data.get("payload"),
            message_id=str(data.get("message_id") or uuid.uuid4().hex),
            correlation_id=data.get("correlation_id"),
            trace_id=data.get("trace_id"),
            created_at=float(data.get("created_at", time.time())),
            metadata=dict(data.get("metadata", {})),
        )

    @classmethod
    def from_json(cls, text: str) -> "AgentMessage":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise MessageEnvelopeError(f"Invalid JSON envelope: {e}") from e
        return cls.from_dict(data)

    # ── Reply convenience ──────────────────────────────────────────────

    def reply(
        self,
        payload: Any = None,
        message_type: MessageType = MessageType.RESPONSE,
    ) -> "AgentMessage":
        """Build a response envelope back to the sender, preserving correlation."""
        return AgentMessage(
            sender=self.recipient,
            recipient=self.sender,
            message_type=message_type,
            payload=payload,
            correlation_id=self.message_id,
            trace_id=self.trace_id,
        )
