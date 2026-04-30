"""Inter-agent messaging protocol — primitives for distributed swarms.

The existing ``swarm/`` orchestrator runs everything in-process and calls
agent coroutines directly. As soon as agents need to live in different
processes (or different machines), you need a message-passing layer.
This module provides the protocol envelope and a pluggable broker.

The shipped broker is in-process (asyncio queues). A Redis-backed
broker can be added later without changing any caller code.
"""

from .protocol import (
    AgentMessage,
    MessageType,
    MessageEnvelopeError,
)
from .broker import (
    Broker,
    InProcessBroker,
    BrokerError,
)

__all__ = [
    "AgentMessage",
    "MessageType",
    "MessageEnvelopeError",
    "Broker",
    "InProcessBroker",
    "BrokerError",
]
