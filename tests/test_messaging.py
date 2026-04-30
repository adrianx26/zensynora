"""Tests for the inter-agent messaging protocol and in-process broker."""

from __future__ import annotations

import asyncio
import json

import pytest

from myclaw.messaging import (
    AgentMessage,
    BrokerError,
    InProcessBroker,
    MessageEnvelopeError,
    MessageType,
)


# ── Envelope ──────────────────────────────────────────────────────────────


def test_envelope_roundtrip_via_dict():
    m = AgentMessage(
        sender="a", recipient="b", message_type=MessageType.REQUEST,
        payload={"x": 1},
    )
    out = AgentMessage.from_dict(m.to_dict())
    assert out.sender == "a"
    assert out.recipient == "b"
    assert out.message_type is MessageType.REQUEST
    assert out.payload == {"x": 1}
    assert out.message_id == m.message_id


def test_envelope_roundtrip_via_json():
    m = AgentMessage(sender="a", recipient="b", message_type=MessageType.NOTIFY)
    out = AgentMessage.from_json(m.to_json())
    assert out.sender == "a"


def test_envelope_rejects_missing_fields():
    with pytest.raises(MessageEnvelopeError):
        AgentMessage.from_dict({"sender": "a"})


def test_envelope_rejects_unknown_type():
    with pytest.raises(MessageEnvelopeError):
        AgentMessage.from_dict({
            "sender": "a", "recipient": "b", "message_type": "not-a-type",
        })


def test_envelope_rejects_invalid_json():
    with pytest.raises(MessageEnvelopeError):
        AgentMessage.from_json("{not json")


def test_reply_preserves_correlation():
    req = AgentMessage(sender="a", recipient="b", message_type=MessageType.REQUEST, payload="hi")
    resp = req.reply("hello back")
    assert resp.sender == "b"
    assert resp.recipient == "a"
    assert resp.message_type is MessageType.RESPONSE
    assert resp.correlation_id == req.message_id
    assert resp.payload == "hello back"


# ── Broker ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_broker_register_and_publish():
    broker = InProcessBroker()
    received = asyncio.Event()
    captured: list = []

    async def handler(msg: AgentMessage):
        captured.append(msg)
        received.set()
        return None

    await broker.register("worker", handler)
    await broker.publish(AgentMessage(
        sender="cli", recipient="worker",
        message_type=MessageType.REQUEST, payload="ping",
    ))
    await asyncio.wait_for(received.wait(), timeout=1.0)
    assert captured[0].payload == "ping"
    await broker.shutdown()


@pytest.mark.asyncio
async def test_broker_publish_to_unknown_recipient():
    broker = InProcessBroker()
    with pytest.raises(BrokerError, match="No recipient"):
        await broker.publish(AgentMessage(
            sender="a", recipient="ghost", message_type=MessageType.NOTIFY,
        ))
    await broker.shutdown()


@pytest.mark.asyncio
async def test_broker_double_register_rejected():
    broker = InProcessBroker()

    async def h(_msg): return None

    await broker.register("dup", h)
    with pytest.raises(BrokerError, match="already registered"):
        await broker.register("dup", h)
    await broker.shutdown()


@pytest.mark.asyncio
async def test_broker_routes_handler_replies():
    """When a handler returns an AgentMessage, the broker delivers it."""
    broker = InProcessBroker()
    cli_inbox: list = []
    cli_done = asyncio.Event()

    async def worker_handler(msg: AgentMessage):
        return msg.reply(payload=f"echo: {msg.payload}")

    async def cli_handler(msg: AgentMessage):
        cli_inbox.append(msg)
        cli_done.set()
        return None

    await broker.register("worker", worker_handler)
    await broker.register("cli", cli_handler)

    await broker.publish(AgentMessage(
        sender="cli", recipient="worker",
        message_type=MessageType.REQUEST, payload="ping",
    ))
    await asyncio.wait_for(cli_done.wait(), timeout=1.0)
    assert cli_inbox[0].payload == "echo: ping"
    assert cli_inbox[0].correlation_id is not None  # correlation preserved
    await broker.shutdown()


@pytest.mark.asyncio
async def test_broker_handler_exceptions_dont_crash_drain():
    """A handler that raises must not stop the recipient from receiving subsequent messages."""
    broker = InProcessBroker()
    seen: list = []
    second_received = asyncio.Event()

    async def h(msg: AgentMessage):
        if msg.payload == "boom":
            raise RuntimeError("intentional")
        seen.append(msg.payload)
        second_received.set()
        return None

    await broker.register("w", h)
    await broker.publish(AgentMessage(
        sender="cli", recipient="w", message_type=MessageType.NOTIFY, payload="boom",
    ))
    await broker.publish(AgentMessage(
        sender="cli", recipient="w", message_type=MessageType.NOTIFY, payload="ok",
    ))
    await asyncio.wait_for(second_received.wait(), timeout=1.0)
    assert seen == ["ok"]
    await broker.shutdown()


@pytest.mark.asyncio
async def test_broker_unregister_stops_delivery():
    broker = InProcessBroker()

    async def h(_msg): return None

    await broker.register("w", h)
    await broker.unregister("w")
    with pytest.raises(BrokerError):
        await broker.publish(AgentMessage(
            sender="a", recipient="w", message_type=MessageType.NOTIFY,
        ))
    await broker.shutdown()


@pytest.mark.asyncio
async def test_list_recipients():
    broker = InProcessBroker()
    async def h(_m): return None
    await broker.register("a", h)
    await broker.register("b", h)
    assert await broker.list_recipients() == ["a", "b"]
    await broker.shutdown()
