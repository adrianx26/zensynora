"""Pluggable broker interface + in-process implementation.

The interface is small on purpose — register a recipient, publish, drain.
A Redis or NATS broker would slot in by re-implementing the three methods.

Backpressure: each recipient gets a bounded ``asyncio.Queue``. Senders
block when the recipient is slow rather than letting RAM blow up.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Dict, List, Optional

from .protocol import AgentMessage

logger = logging.getLogger(__name__)


class BrokerError(RuntimeError):
    """Raised on misuse — unknown recipient, double-register, etc."""


# Type alias for the recipient handler. Returning a message routes a reply.
Handler = Callable[[AgentMessage], Awaitable[Optional[AgentMessage]]]


class Broker(ABC):
    """Abstract message router.

    Implementations must handle these invariants:
    * ``publish`` is non-blocking from the caller's perspective beyond the
      time it takes to enqueue (modulo backpressure).
    * Handlers run concurrently across recipients but serially for one
      recipient (so each agent sees its mailbox in order).
    """

    @abstractmethod
    async def register(
        self,
        recipient: str,
        handler: Handler,
        queue_size: int = 100,
    ) -> None:
        """Subscribe ``recipient`` to its mailbox; ``handler`` is invoked
        per message. ``queue_size`` is the per-recipient backpressure cap."""

    @abstractmethod
    async def unregister(self, recipient: str) -> None: ...

    @abstractmethod
    async def publish(self, message: AgentMessage) -> None: ...

    @abstractmethod
    async def shutdown(self) -> None: ...


# ── In-process implementation ────────────────────────────────────────────


class _Shutdown:
    """Sentinel value pushed into a mailbox to wake the drain loop."""
    __slots__ = ()


_SHUTDOWN = _Shutdown()


class _MailboxRunner:
    """One queue + one drain task per recipient.

    Keeping a runner per recipient lets one slow handler back-pressure its
    own senders without affecting other recipients.
    """

    def __init__(
        self,
        recipient: str,
        handler: Handler,
        queue_size: int,
        broker: "InProcessBroker",
    ) -> None:
        self.recipient = recipient
        self.handler = handler
        # Mailbox holds AgentMessage values, plus the _Shutdown sentinel.
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.broker = broker
        self._task = asyncio.create_task(self._drain(), name=f"broker-{recipient}")

    async def stop(self) -> None:
        # Push sentinel; if the queue is full, fall back to cancelling.
        try:
            self.queue.put_nowait(_SHUTDOWN)
        except asyncio.QueueFull:
            self._task.cancel()
        try:
            await asyncio.wait_for(self._task, timeout=5.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    async def _drain(self) -> None:
        while True:
            item = await self.queue.get()
            if isinstance(item, _Shutdown):
                return
            msg: AgentMessage = item
            try:
                reply = await self.handler(msg)
            except Exception as e:
                logger.warning(
                    "Handler for %s raised on message %s",
                    self.recipient, msg.message_id, exc_info=e,
                )
                reply = None
            if reply is not None:
                # Best-effort: handler-side reply delivery should never
                # crash the drain loop.
                try:
                    await self.broker.publish(reply)
                except Exception as e:
                    logger.warning("Reply delivery failed", exc_info=e)


class InProcessBroker(Broker):
    """Asyncio-only broker. Single process, multiple agents."""

    def __init__(self) -> None:
        self._mailboxes: Dict[str, _MailboxRunner] = {}
        self._lock = asyncio.Lock()

    async def register(
        self,
        recipient: str,
        handler: Handler,
        queue_size: int = 100,
    ) -> None:
        async with self._lock:
            if recipient in self._mailboxes:
                raise BrokerError(f"Recipient already registered: {recipient!r}")
            self._mailboxes[recipient] = _MailboxRunner(
                recipient, handler, queue_size, self
            )

    async def unregister(self, recipient: str) -> None:
        async with self._lock:
            mb = self._mailboxes.pop(recipient, None)
        if mb is not None:
            await mb.stop()

    async def publish(self, message: AgentMessage) -> None:
        # Fetch under lock; enqueue without holding it so a full queue
        # doesn't block other registrations.
        async with self._lock:
            mb = self._mailboxes.get(message.recipient)
        if mb is None:
            raise BrokerError(f"No recipient registered: {message.recipient!r}")
        await mb.queue.put(message)

    async def shutdown(self) -> None:
        async with self._lock:
            mailboxes = list(self._mailboxes.values())
            self._mailboxes.clear()
        for mb in mailboxes:
            await mb.stop()

    async def list_recipients(self) -> List[str]:
        """Diagnostic helper — not part of the abstract contract."""
        async with self._lock:
            return sorted(self._mailboxes.keys())
