"""Chat message actions: edit (and resend), delete, regenerate, export.

Agents are faked, so these run offline in a temporary HOME.
"""

import asyncio
import json
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myclaw import events


class RecordingMemory:
    """Stands in for myclaw.memory.Memory: records what the agent remembers."""

    def __init__(self):
        self.items = []
        self.cleared = 0

    async def clear(self):
        self.cleared += 1
        self.items = []
        return 0

    async def add(self, role, content):
        self.items.append((role, content))


class FakeAgent:
    def __init__(self, name, delay=0.0):
        self.name = name
        self.model = f"{name}-model"
        self._provider_name = "ollama"
        self.prompts = []
        self.delay = delay
        self.memories = {}  # scope -> RecordingMemory

    async def stream_think(self, prompt, user_id="default"):
        self.prompts.append(prompt)
        n = len(self.prompts)
        mem = await self._get_memory(user_id)
        mem.items.append(("user", prompt))
        words = f"{self.name} answer {n}".split(" ")
        for w in words:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield w + " "
        mem.items.append(("assistant", " ".join(words)))

    async def _get_memory(self, user_id):
        from myclaw.agent import MEMORY_SCOPE

        return self.memories.setdefault(MEMORY_SCOPE.get(), RecordingMemory())


@pytest.fixture
def home(tmp_path, monkeypatch):
    import myclaw.config as cfg_mod
    from myclaw.web import settings_api

    monkeypatch.setenv("HOME", str(tmp_path))
    cfg_dir = tmp_path / ".myclaw"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "config.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(settings_api, "CONFIG_FILE", cfg_file)
    cfg_mod._cached_config = None
    yield tmp_path
    cfg_mod._cached_config = None


@pytest.fixture
def chat(home):
    from myclaw.web import chat_api

    agents = {"default": FakeAgent("default"), "writer": FakeAgent("writer"),
              "slow": FakeAgent("slow", delay=0.3)}
    chat_api.set_registry_getter(lambda: agents)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as c:
        yield c, agents


def _msgs(c, cid):
    return c.get(f"/api/channels/{cid}/messages").json()["messages"]


def _idle(c, cid, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        res = c.get(f"/api/channels/{cid}/messages").json()
        if not res["busy"] and not any(m["meta"].get("streaming") for m in res["messages"]):
            return res["messages"]
        time.sleep(0.03)
    raise AssertionError("agents did not finish")


def _say(c, cid, text):
    c.post(f"/api/channels/{cid}/messages", json={"text": text})
    return _idle(c, cid)


def _recv_until(ws, kind, limit=200):
    for _ in range(limit):
        ev = json.loads(ws.receive_text())
        if ev.get("type") == kind:
            return ev
    raise AssertionError(f"no {kind} event")


def _channel(c, members=("default",), mode="lead"):
    return c.post("/api/channels", json={"name": "t", "members": list(members), "reply_mode": mode}).json()["id"]


def test_edit_and_resend_removes_later_messages_and_answers_again(chat):
    c, agents = chat
    cid = _channel(c)
    _say(c, cid, "first question")
    msgs = _say(c, cid, "second question")
    first = msgs[0]
    assert [m["role"] for m in msgs] == ["user", "agent", "user", "agent"]

    with c.websocket_connect(f"/ws/channels/{cid}") as ws:
        ws.receive_text()  # hello
        r = c.patch(f"/api/channels/{cid}/messages/{first['id']}", json={"text": "edited question", "resend": True})
        assert r.status_code == 200, r.text
        assert r.json()["message"]["meta"]["edited_at"]
        assert sorted(r.json()["deleted"]) == [m["id"] for m in msgs[1:]]
        assert _recv_until(ws, "message_updated")["message"]["text"] == "edited question"
        assert sorted(_recv_until(ws, "messages_deleted")["ids"]) == [m["id"] for m in msgs[1:]]
        _recv_until(ws, "stream_end")

    msgs = _idle(c, cid)
    assert [m["role"] for m in msgs] == ["user", "agent"]
    assert msgs[0]["id"] == first["id"] and msgs[0]["text"] == "edited question"  # not stored twice
    assert msgs[0]["meta"].get("edited_at")
    assert msgs[1]["text"].startswith("default answer 3")
    assert agents["default"].prompts[-1] == "edited question"
    # The agent's memory was rebuilt: the old questions are gone.
    mem = next(iter(agents["default"].memories.values()))
    assert all("first question" not in t and "second question" not in t for _, t in mem.items)
    assert ("user", "edited question") in mem.items


def test_edit_and_resend_keeps_mention_routing(chat):
    c, agents = chat
    cid = _channel(c, ("default", "writer"))
    msgs = _say(c, cid, "hello")
    r = c.patch(f"/api/channels/{cid}/messages/{msgs[0]['id']}", json={"text": "@writer hello", "resend": True})
    assert r.status_code == 200
    msgs = _idle(c, cid)
    assert [m["sender"] for m in msgs if m["role"] == "agent"] == ["writer"]


def test_edit_agent_message_in_place(chat):
    c, agents = chat
    cid = _channel(c)
    msgs = _say(c, cid, "q")
    answer = msgs[1]
    r = c.patch(f"/api/channels/{cid}/messages/{answer['id']}", json={"text": "corrected answer"})
    assert r.status_code == 200
    msgs = _msgs(c, cid)
    assert len(msgs) == 2 and msgs[1]["text"] == "corrected answer" and msgs[1]["meta"]["edited_at"]
    assert msgs[1]["meta"]["model"] == "default-model"  # the rest of meta is kept
    assert len(agents["default"].prompts) == 1  # no re-run
    mem = next(iter(agents["default"].memories.values()))
    assert mem.items == [("user", "q"), ("assistant", "corrected answer")]
    # Agent messages can't be "resent"; empty text is refused.
    assert c.patch(f"/api/channels/{cid}/messages/{answer['id']}", json={"text": "x", "resend": True}).status_code == 400
    assert c.patch(f"/api/channels/{cid}/messages/{answer['id']}", json={"text": "  "}).status_code == 400
    assert c.patch(f"/api/channels/{cid}/messages/999999", json={"text": "x"}).status_code == 404


def test_delete_with_and_without_replies(chat):
    c, _ = chat
    cid = _channel(c, ("default", "writer"), "all")
    _say(c, cid, "one")
    msgs = _say(c, cid, "two")
    assert [m["role"] for m in msgs] == ["user", "agent", "agent", "user", "agent", "agent"]

    with c.websocket_connect(f"/ws/channels/{cid}") as ws:
        ws.receive_text()
        r = c.delete(f"/api/channels/{cid}/messages/{msgs[0]['id']}", params={"with_replies": "true"})
        assert r.status_code == 200
        assert r.json()["deleted"] == [m["id"] for m in msgs[:3]]
        assert _recv_until(ws, "messages_deleted")["ids"] == [m["id"] for m in msgs[:3]]

    r = c.delete(f"/api/channels/{cid}/messages/{msgs[3]['id']}")
    assert r.json()["deleted"] == [msgs[3]["id"]]
    left = _msgs(c, cid)
    assert [m["id"] for m in left] == [msgs[4]["id"], msgs[5]["id"]]
    # Deleting an agent message deletes only it.
    c.delete(f"/api/channels/{cid}/messages/{msgs[4]['id']}")
    assert [m["id"] for m in _msgs(c, cid)] == [msgs[5]["id"]]
    # Another channel's message id is not found here.
    other = _channel(c)
    assert c.delete(f"/api/channels/{other}/messages/{msgs[5]['id']}").status_code == 404


def test_regenerate_rewrites_one_answer_in_place(chat):
    c, agents = chat
    cid = _channel(c, ("default", "writer"), "all")
    _say(c, cid, "one")
    msgs = _say(c, cid, "two")
    old = msgs[1]  # default's answer to "one"
    assert old["sender"] == "default"

    with c.websocket_connect(f"/ws/channels/{cid}") as ws:
        ws.receive_text()
        r = c.post(f"/api/channels/{cid}/messages/{old['id']}/regenerate")
        assert r.status_code == 200, r.text
        assert r.json()["answers"] == msgs[0]["id"]
        start = _recv_until(ws, "stream_start")
        assert start["message"]["id"] == old["id"]
        end = _recv_until(ws, "stream_end")
        assert end["id"] == old["id"]

    after = _idle(c, cid)
    assert [m["id"] for m in after] == [m["id"] for m in msgs]  # same place, nothing added
    assert after[1]["text"].startswith("default answer 3")
    assert after[2]["text"] == msgs[2]["text"]  # writer untouched
    # Only that agent ran, for the original question.
    assert len(agents["writer"].prompts) == 2
    assert agents["default"].prompts[-1].endswith("User: one")
    # Its memory holds both turns, with the new answer.
    mem = next(iter(agents["default"].memories.values()))
    assert [r for r, _ in mem.items] == ["user", "assistant", "user", "assistant"]
    assert mem.items[1][1].startswith("default answer 3")
    # Only agent answers can be regenerated.
    assert c.post(f"/api/channels/{cid}/messages/{msgs[0]['id']}/regenerate").status_code == 400


def test_actions_refused_while_agents_answer(chat):
    c, _ = chat
    cid = _channel(c, ("slow",))
    msgs = _say(c, cid, "first")
    c.post(f"/api/channels/{cid}/messages", json={"text": "second"})
    time.sleep(0.1)
    assert c.get(f"/api/channels/{cid}/messages").json()["busy"]
    for r in (c.patch(f"/api/channels/{cid}/messages/{msgs[0]['id']}", json={"text": "x"}),
              c.delete(f"/api/channels/{cid}/messages/{msgs[0]['id']}"),
              c.post(f"/api/channels/{cid}/messages/{msgs[1]['id']}/regenerate")):
        assert r.status_code == 409
        assert "Wait until the agents have finished" in r.json()["detail"]
    assert c.post(f"/api/channels/{cid}/stop").json()["stopped"]
    _idle(c, cid)
    assert c.delete(f"/api/channels/{cid}/messages/{msgs[0]['id']}").status_code == 200


def test_export_markdown(chat):
    c, _ = chat
    cid = _channel(c)
    msgs = _say(c, cid, "how are you?")
    c.patch(f"/api/channels/{cid}/messages/{msgs[1]['id']}", json={"text": "fine, *thanks*"})
    r = c.get(f"/api/channels/{cid}/export.md")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    assert "attachment" in r.headers["content-disposition"]
    md = r.text
    assert md.startswith("# #t")
    assert "**You** ·" in md and "how are you?" in md
    assert "**@default** ·" in md and "(edited)" in md and "fine, *thanks*" in md
    assert c.get("/api/channels/nope/export.md").status_code == 404


def test_events_are_published_to_subscribers(chat):
    """Other open browsers learn about deletions through myclaw.events."""
    c, _ = chat
    cid = _channel(c)
    msgs = _say(c, cid, "q")
    seen = []

    def listener(ev):
        if ev.get("channel") == cid:
            seen.append(ev)

    events.add_listener(listener)
    try:
        c.patch(f"/api/channels/{cid}/messages/{msgs[0]['id']}", json={"text": "q2"})
        c.delete(f"/api/channels/{cid}/messages/{msgs[1]['id']}")
    finally:
        events._listeners.remove(listener)
    kinds = [e["type"] for e in seen]
    assert kinds == ["message_updated", "messages_deleted"]
    assert seen[1]["ids"] == [msgs[1]["id"]]
