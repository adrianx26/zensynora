"""v0.7.1 features: multi-agent channels, agent-to-agent events, jobs, model
switching/rotation, custom providers, prompt generator and backups.

Agents and LLM calls are faked, so these tests run offline in a temporary HOME.
"""

import asyncio
import json
import tarfile

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from myclaw import events


class FakeMemory:
    async def clear(self):
        return None


class FakeAgent:
    def __init__(self, name, reply=None, delegate_to=None):
        self.name = name
        self.model = f"{name}-model"
        self._provider_name = "ollama"
        self.prompts = []
        self._reply = reply or (lambda p: f"{name} says hi")
        self._delegate_to = delegate_to

    async def stream_think(self, prompt, user_id="default"):
        self.prompts.append(prompt)
        if self._delegate_to:
            # What the real delegate tool emits (myclaw/tools/swarm.py).
            events.emit({"type": "delegation_start", "from": self.name, "to": self._delegate_to,
                         "task": "check the facts"})
            events.emit({"type": "delegation_end", "from": self.name, "to": self._delegate_to,
                         "result": "facts ok"})
        for word in self._reply(prompt).split(" "):
            yield word + " "

    async def think(self, prompt, user_id="default", _depth=0):
        self.prompts.append(prompt)
        return self._reply(prompt)

    async def _get_memory(self, user_id):
        return FakeMemory()


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


def _wait_turn(client, channel_id, n_agent_msgs, timeout=5.0):
    import time

    end = time.time() + timeout
    while time.time() < end:
        msgs = client.get(f"/api/channels/{channel_id}/messages").json()["messages"]
        done = [m for m in msgs if m["role"] == "agent" and not m.get("meta", {}).get("streaming")
                and m["text"]]
        if len(done) >= n_agent_msgs:
            return msgs
        time.sleep(0.05)
    raise AssertionError(f"agents did not answer: {msgs}")


@pytest.fixture
def chat(home):
    from myclaw.web import chat_api

    agents = {"default": FakeAgent("default"), "coder": FakeAgent("coder", delegate_to="default"),
              "writer": FakeAgent("writer")}
    chat_api.set_registry_getter(lambda: agents)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as c:
        yield c, agents


def test_channel_with_several_agents_and_agent_to_agent_events(chat):
    c, agents = chat
    ch = c.post("/api/channels", json={"name": "team", "members": ["default", "coder"],
                                       "reply_mode": "all"}).json()
    assert ch["members"] == ["default", "coder"]
    assert c.post("/api/channels", json={"name": "x", "members": ["ghost"]}).status_code == 400

    with c.websocket_connect(f"/ws/channels/{ch['id']}") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello" and hello["mfa_required"] is False
        ws.send_text(json.dumps({"type": "message", "text": "plan the release"}))
        msgs = _wait_turn(c, ch["id"], 2)

    agent_msgs = [m for m in msgs if m["role"] == "agent"]
    assert [m["sender"] for m in agent_msgs] == ["default", "coder"]
    # The second agent sees the first one's answer (group context).
    assert "@default: default says hi" in agents["coder"].prompts[-1]
    # coder delegated to default: saved as events in the channel history.
    evs = [m for m in msgs if m["role"] == "event"]
    assert {e["meta"]["type"] for e in evs} >= {"delegation_start", "delegation_end"}
    assert evs[0]["meta"]["from"] == "coder" and evs[0]["meta"]["to"] == "default"
    # The global activity feed has them too.
    feed = c.get("/api/activity").json()["events"]
    assert any(e["meta"].get("type") == "delegation_start" for e in feed)


def test_mention_picks_and_adds_agents(chat):
    c, agents = chat
    ch = c.post("/api/channels", json={"name": "solo", "members": ["default"]}).json()
    with c.websocket_connect(f"/ws/channels/{ch['id']}") as ws:
        ws.receive_text()
        ws.send_text(json.dumps({"type": "message", "text": "@writer draft a haiku"}))
        msgs = _wait_turn(c, ch["id"], 1)
    assert [m["sender"] for m in msgs if m["role"] == "agent"] == ["writer"]
    assert "writer" in c.get("/api/channels").json()["channels"][0]["members"] or \
        any("writer" in x["members"] for x in c.get("/api/channels").json()["channels"])
    dm = c.get("/api/channels/dm/coder").json()
    assert dm["kind"] == "dm" and dm["members"] == ["coder"]


def test_jobs_run_evaluate_and_history(home, monkeypatch):
    import myclaw.provider as prov
    from myclaw.jobs import store
    from myclaw.jobs.runner import JobRunner

    agents = {"default": FakeAgent("default", reply=lambda p: "Report: all 3 checks passed")}
    runner = JobRunner(lambda: agents)

    async def fake_complete(provider, messages, model):
        return '{"score": 8, "verdict": "pass", "reasons": "meets the criteria"}'

    monkeypatch.setattr(prov, "complete_text", fake_complete)
    monkeypatch.setattr(prov, "get_provider", lambda cfg, name=None: object())

    job = store.create_job({"name": "Daily check", "prompt": "run the checks", "schedule": "daily",
                            "at_time": "07:30", "success_criteria": "mentions all checks",
                            "auto_evaluate": True})
    assert job["next_run"] and job["schedule"] == "daily"
    with pytest.raises(ValueError):
        store.create_job({"name": "bad", "prompt": "x", "schedule": "daily", "at_time": "25:99"})

    run_id = asyncio.run(runner.run_job(job["id"]))
    run = store.get_run(run_id)
    assert run["status"] == "ok" and "3 checks" in run["output"]
    assert run["score"] == 8
    assert store.get_job(job["id"])["last_status"] == "ok"

    store.update_job(job["id"], {"enabled": False})
    assert store.get_job(job["id"])["enabled"] in (False, 0)


def test_models_switch_rotate_and_custom_provider(home):
    from myclaw.web import models_api, settings_api

    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(models_api.router)
    c = TestClient(app)

    # Add a free online provider (OpenAI-compatible) with its key.
    r = c.put("/api/settings", json={"changes": {"providers.custom": [
        {"name": "deep", "label": "DeepSeek", "kind": "openai",
         "base_url": "https://api.deepseek.com/v1", "api_key": "sk-x"}]}})
    assert r.status_code == 200, r.text
    names = [p["name"] for p in c.get("/api/models").json()["providers"]]
    assert "deep" in names and "ollama" in names
    # The key is stored but never returned.
    assert "sk-x" not in json.dumps(c.get("/api/settings").json())

    start = c.get("/api/models").json()["default"]
    r = c.post("/api/models/default", json={"provider": "deep", "model": "deepseek-chat"})
    assert r.json()["default"] == {"provider": "deep", "model": "deepseek-chat"}
    assert start in r.json()["rotation"]["entries"]  # previous default remembered

    r = c.post("/api/models/rotate").json()
    assert r["default"] == start
    assert {"provider": "deep", "model": "deepseek-chat"} in r["rotation"]["entries"]


def test_generate_prompt_uses_main_llm(home, monkeypatch):
    import myclaw.provider as prov
    from myclaw.web import models_api

    seen = {}

    async def fake_complete(provider, messages, model):
        seen["messages"] = messages
        return "```\nYou are Researcher, an agent that finds sources.\n- Cite everything.\n```"

    monkeypatch.setattr(prov, "complete_text", fake_complete)
    monkeypatch.setattr(prov, "get_provider", lambda cfg, name=None: object())
    app = FastAPI()
    app.include_router(models_api.router)
    c = TestClient(app)
    assert c.post("/api/agents/generate-prompt", json={"name": "", "description": ""}).status_code == 400
    r = c.post("/api/agents/generate-prompt", json={"name": "Researcher", "description": "finds sources"})
    assert r.status_code == 200
    assert r.json()["prompt"].startswith("You are Researcher") and "```" not in r.json()["prompt"]
    assert "Researcher" in seen["messages"][1]["content"]


def test_backup_create_verify_restore(home):
    from myclaw import backup

    data = home / ".myclaw"
    (data / "config.json").write_text('{"agents": {"defaults": {"model": "m1"}}}')
    (data / "knowledge" / "default").mkdir(parents=True)
    (data / "knowledge" / "default" / "note.md").write_text("# hello")

    info = backup.create_backup(["settings", "knowledge"], "manual", "before test")
    assert set(info["parts"]) == {"settings", "knowledge"}
    ok, problems = backup.verify_backup(backup.resolve(info["name"]))
    assert ok, problems
    with tarfile.open(backup.resolve(info["name"])) as t:
        names = t.getnames()
    assert any(n.endswith("config.json") for n in names) and any(n.endswith("note.md") for n in names)

    (data / "config.json").write_text('{"agents": {"defaults": {"model": "changed"}}}')
    (data / "knowledge" / "default" / "note.md").unlink()
    r = backup.restore_backup(info["name"])
    assert '"m1"' in (data / "config.json").read_text()
    assert (data / "knowledge" / "default" / "note.md").read_text() == "# hello"
    assert r["safety_backup"]  # the state before the restore was saved first
    with pytest.raises((FileNotFoundError, ValueError)):
        backup.resolve("../../etc/passwd")


def test_rotation_falls_back_to_the_next_model(home, monkeypatch):
    """A failing default model hands over to the next model in the rotation list."""
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    cfg = load_config()
    cfg.agents.defaults.provider, cfg.agents.defaults.model = "ollama", "broken"
    cfg.agents.rotation.enabled = True
    cfg.agents.rotation.entries = [type(cfg.agents.rotation).model_fields["entries"].annotation.__args__[0](
        provider="lmstudio", model="good")]

    class Prov:
        def __init__(self, name):
            self.name = name

        async def chat(self, messages, model, stream=False):
            if model == "broken":
                raise RuntimeError("model not found")
            return f"answer from {self.name}/{model}", None

    monkeypatch.setattr(agent_mod, "get_provider", lambda c, name: Prov(name))
    a = agent_mod.Agent(cfg, name="default")
    assert a._rotation_candidates() == [("ollama", "broken"), ("lmstudio", "good")]
    result = asyncio.run(a._rotating_chat(a._rotation_candidates(), [{"role": "user", "content": "hi"}], False))
    assert result[0] == "answer from lmstudio/good"
    assert a.last_model_used == ("lmstudio", "good")

    cfg.agents.rotation.entries = []
    assert a._rotation_candidates() == []  # rotation off without entries


def test_stream_with_only_a_tool_call_is_not_an_empty_answer(home, monkeypatch):
    """v0.7.1 regression: a streamed reply that is only a tool call (no text)
    produced "I wasn't able to generate a response… knowledge base has no
    entries" before the tool even ran."""
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    a = agent_mod.Agent(load_config(), name="default")

    async def fake_chat(messages, model, stream=False):
        collector = []

        async def gen():
            # Real providers fill the collector while the stream is consumed.
            collector.append({"id": "c1", "type": "function",
                              "function": {"name": "list_tools", "arguments": {}}})
            if False:
                yield ""

        return gen(), collector

    async def fake_tools(*args, **kwargs):
        return "Here are the tools."

    async def no_kb(*a, **k):
        return ""

    monkeypatch.setattr(a, "_provider_chat", fake_chat)
    monkeypatch.setattr(a, "_execute_tools", fake_tools)
    monkeypatch.setattr(a, "_search_knowledge_context", no_kb)

    async def collect():
        return [c async for c in a.stream_think("which tools do you have?", user_id="t1")]

    out = "".join(c for c in asyncio.run(collect()) if not c.startswith("__") and not c.startswith('{"tool"'))
    assert "wasn't able" not in out and "no entries" not in out
    assert "Here are the tools." in out


def test_stream_truly_empty_retries_then_explains(home, monkeypatch):
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    a = agent_mod.Agent(load_config(), name="default")
    calls = {"n": 0}

    async def fake_chat(messages, model, stream=False):
        calls["n"] += 1
        if stream:
            async def gen():
                if False:
                    yield ""
            return gen(), []
        return ("Recovered answer" if calls["n"] == 2 else ""), None

    async def no_kb(*a, **k):
        return ""

    monkeypatch.setattr(a, "_provider_chat", fake_chat)
    monkeypatch.setattr(a, "_search_knowledge_context", no_kb)

    async def collect():
        return "".join([c async for c in a.stream_think("hi", user_id="t2")])

    assert "Recovered answer" in asyncio.run(collect())


def test_delegate_sees_the_registry_set_later(home):
    """v0.7.1 regression: set_registry rebound a global that swarm.py had
    already imported, so delegate always said "Agent registry not initialized"."""
    from myclaw.tools import core, set_registry
    from myclaw.tools.swarm import delegate

    saved = dict(core._agent_registry)
    try:
        set_registry({"default": FakeAgent("default"), "writer": FakeAgent("writer", reply=lambda p: "a haiku")})
        assert asyncio.run(delegate("writer", "write a haiku")) == "a haiku"
        assert "Unknown agent" in asyncio.run(delegate("ghost", "x"))
    finally:
        set_registry(saved)


def test_scheduler_reads_the_job_queue_set_later(home):
    from myclaw.tools import core, scheduler

    class JQ:
        def get_jobs_by_name(self, name):
            return []

        def jobs(self):
            return []

    old = core._job_queue
    try:
        core.set_job_queue(None)
        assert "not available" in scheduler.cancel_schedule("x").lower()
        core.set_job_queue(JQ())
        assert "not available" not in scheduler.cancel_schedule("x").lower()
        assert "not available" not in scheduler.list_schedules().lower()
    finally:
        core.set_job_queue(old)
