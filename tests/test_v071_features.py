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
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
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


def test_backup_and_restore_progress_reaches_published_archive_and_keeps_notes(home, monkeypatch):
    from myclaw import backup, maintenance
    from myclaw.web.api import app

    monkeypatch.setattr(backup, "install_root", lambda: home / "app")
    note = home / ".myclaw" / "knowledge" / "default" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# original")
    c = TestClient(app)
    response = c.post("/api/backups", json={"parts": ["settings", "knowledge"]})
    assert response.status_code == 200, response.text
    info = response.json()
    state = c.get("/api/backups/process").json()
    assert state["status"] == "ok"
    assert state["steps"]["backup-memory"]["status"] == "skipped"
    statuses = [(e["step"], e["status"]) for e in state["events"]]
    assert statuses.index(("backup-verify", "ok")) < statuses.index(("backup-publish", "ok"))
    assert backup.verify_backup(backup.resolve(info["name"])) == (True, [])
    note.write_text("# user edits")
    r = c.post(f"/api/backups/{info['name']}/restore", json={"parts": ["knowledge"]})
    assert r.status_code == 200, r.text
    assert note.read_text() == "# original"
    assert any(p.read_text() == "# user edits" for p in (note.parent / ".trash").rglob("note.md"))
    state = maintenance.status()
    assert state["action"] == "restore" and state["status"] == "ok"
    assert state["steps"]["restore-safety-copy"]["status"] == "ok"
    assert state["steps"]["restore-reload"]["status"] == "ok"
    assert not (home / ".myclaw" / ".lifecycle-restore-pending.json").exists()


def test_failed_verification_never_publishes_archive_and_records_failure(home, monkeypatch):
    from myclaw import backup, maintenance

    monkeypatch.setattr(backup, "install_root", lambda: home / "app")
    monkeypatch.setattr(backup, "verify_backup", lambda path: (False, ["checksum mismatch"]))
    with pytest.raises(ValueError, match="verification failed"):
        maintenance.run("backup", lambda: backup.create_backup(["settings"]))
    assert not list(backup.backup_dir().iterdir())
    state = maintenance.status()
    assert state["status"] == "failed"
    assert state["steps"]["backup-verify"]["status"] == "failed"
    assert "checksum mismatch" in state["error"]


def test_progress_failure_does_not_hide_original_operation_error(home, monkeypatch):
    from myclaw import maintenance
    from updater.progress import emit

    original = maintenance._atomic_write
    calls = 0

    def fail_progress(path, text):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise OSError("progress disk unavailable")
        return original(path, text)

    def fail_operation():
        emit("restore-files", "running", "Replacing files")
        raise ValueError("original restore failure")

    monkeypatch.setattr(maintenance, "_atomic_write", fail_progress)
    with pytest.raises(ValueError, match="original restore failure"):
        maintenance.run("restore", fail_operation)


def test_terminal_backup_interactive_selection_and_detailed_restore(home, monkeypatch):
    from click.testing import CliRunner
    from myclaw import backup
    from myclaw.cli import cli

    monkeypatch.setattr(backup, "install_root", lambda: home / "app")
    runner = CliRunner()
    result = runner.invoke(cli, ["backup", "create", "--interactive"], input="n\nn\nn\n")
    assert result.exit_code == 0, result.output
    assert "[SKIPPED] backup-knowledge" in result.output
    assert "[OK] backup-verify" in result.output
    info = backup.list_backups()[0]
    assert info["parts"] == ["settings"]
    restored = runner.invoke(cli, ["backup", "restore", info["name"], "--interactive", "--yes"], input="y\n")
    assert restored.exit_code == 0, restored.output
    assert "[OK] restore-safety-copy" in restored.output
    assert "[OK] restore-reload" in restored.output


def test_completed_restore_keeps_success_and_reload_hook_when_progress_write_fails(home, monkeypatch, caplog):
    from myclaw import backup, maintenance
    from myclaw.web import backup_api
    from myclaw.web.api import app

    monkeypatch.setattr(backup, "install_root", lambda: home / "app")
    note = home / ".myclaw" / "knowledge" / "default" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# original")
    info = backup.create_backup(["settings", "knowledge"])
    note.write_text("# changed")
    hooks = []
    monkeypatch.setattr(backup_api, "_on_restored", lambda: hooks.append("reload"))
    original_write = maintenance._atomic_write

    def fail_final_progress(path, text):
        state = json.loads(text)
        if state["status"] == "ok" or state["steps"].get("restore-reload", {}).get("status") == "ok":
            raise OSError("progress disk unavailable")
        return original_write(path, text)

    monkeypatch.setattr(maintenance, "_atomic_write", fail_final_progress)
    response = TestClient(app).post(f"/api/backups/{info['name']}/restore", json={})
    assert response.status_code == 200, response.text
    assert note.read_text() == "# original" and hooks == ["reload"]
    assert not (home / ".myclaw" / ".lifecycle-restore-pending.json").exists()
    assert "Progress could not be saved" in response.json()["progress_warning"]
    assert maintenance.status()["status"] == "progress-unavailable"
    assert caplog.text.count("Progress could not be saved") == 1


def test_backup_api_reports_the_actual_blocker(home, monkeypatch):
    from myclaw import backup, evolve, executions
    from myclaw.web import backup_api

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setattr(evolve, "pending_fusions", lambda: [{"id": "fusion-1"}])
    app = FastAPI()
    app.include_router(backup_api.router)
    with TestClient(app) as client:
        response = client.post("/api/backups", json={})
        assert response.status_code == 409
        assert "fusion-1" in response.json()["detail"]
        assert "/api/evolve/fuse/resume" in response.json()["detail"]

        monkeypatch.setattr(evolve, "pending_fusions", lambda: [])
        monkeypatch.setattr(executions, "active_for", lambda _: [("agent", "turn", 1)])
        response = client.post("/api/backups", json={})
        assert response.status_code == 409
        assert "agent turns, jobs or background work" in response.json()["detail"]
        assert "pending fusion" not in response.json()["detail"]
        assert not backup.list_backups()

        monkeypatch.setattr(executions, "active_for", lambda _: [])
        response = client.post("/api/backups", json={})
        assert response.status_code == 200
        assert backup.verify_backup(backup.resolve(response.json()["name"])) == (True, [])


@pytest.mark.parametrize("blocker", ["active", "unverifiable"])
def test_automatic_backup_retries_after_blocker_clears(home, monkeypatch, caplog, blocker):
    import logging

    from myclaw import backup, evolve, executions
    from myclaw.config import load_config
    from myclaw.web import backup_api

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setattr(backup_api, "_last_auto_failure", None)
    cfg = load_config()
    cfg.backup.auto_enabled = True
    monkeypatch.setattr(backup_api, "load_config", lambda: cfg)
    running = [("agent", "turn", 1)]

    def active_for(_):
        if running and blocker == "unverifiable":
            raise evolve.LifecycleError("Cannot verify execution lease lock")
        return running

    monkeypatch.setattr(executions, "active_for", active_for)

    async def tick():
        monkeypatch.setattr(backup_api, "_last_check", 0.0)
        await backup_api.auto_backup_tick()

    with caplog.at_level(logging.WARNING, logger=backup_api.__name__):
        asyncio.run(tick())
        asyncio.run(tick())
        assert len([r for r in caplog.records if "Automatic backup failed" in r.message]) == 1
        assert not backup.list_backups(cfg)
        running.clear()
        asyncio.run(tick())

    items = backup.list_backups(cfg)
    assert len(items) == 1 and items[0]["label"] == "auto"
    assert backup.verify_backup(backup.resolve(items[0]["name"], cfg)) == (True, [])


def test_automatic_backup_runs_from_job_runner(home, monkeypatch):
    from myclaw import backup
    from myclaw.config import load_config
    from myclaw.executions import execution
    from myclaw.jobs import runner as runner_module
    from myclaw.web import backup_api

    monkeypatch.setattr(backup, "install_root", lambda: home)
    monkeypatch.setattr(backup_api, "_last_auto_failure", None)
    cfg = load_config()
    cfg.backup.auto_enabled = True
    monkeypatch.setattr(backup_api, "load_config", lambda: cfg)
    monkeypatch.setattr(runner_module.store, "recover_interrupted", lambda: 0)
    monkeypatch.setattr(runner_module.store, "due_jobs", lambda: [])
    monkeypatch.setattr(runner_module, "TICK_SECONDS", 0.01)
    async def scenario():
        blocked = asyncio.Event()
        retry = asyncio.Event()
        completed = asyncio.Event()
        runner = runner_module.JobRunner(lambda: {})
        attempts = 0

        async def tick():
            nonlocal attempts
            monkeypatch.setattr(backup_api, "_last_check", 0.0)
            await backup_api.auto_backup_tick()
            attempts += 1
            if attempts == 1:
                blocked.set()
                await retry.wait()
            else:
                completed.set()

        runner.on_tick.append(tick)
        try:
            async with execution("default", "turn"):
                runner.start()
                await asyncio.wait_for(blocked.wait(), timeout=5)
                assert not backup.list_backups(cfg)
            retry.set()
            await asyncio.wait_for(completed.wait(), timeout=5)
        finally:
            retry.set()
            await runner.stop()

    asyncio.run(scenario())
    items = backup.list_backups(cfg)
    assert len(items) == 1 and items[0]["label"] == "auto"
    assert backup.verify_backup(backup.resolve(items[0]["name"], cfg)) == (True, [])


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


# ── v0.7.2: tool calls written as text ───────────────────────────────────────

def test_parse_text_tool_calls_recognises_only_real_tool_json():
    from myclaw.agent_internals.text_tools import parse_text_tool_calls

    known = {"search_knowledge", "list_tools"}
    calls = parse_text_tool_calls('{"tool": "search_knowledge", "args": {"query": "hello", "limit": 10}}', known)
    assert calls and calls[0]["function"]["name"] == "search_knowledge"
    assert calls[0]["function"]["arguments"] == {"query": "hello", "limit": 10} and calls[0]["text_call"]
    assert parse_text_tool_calls('```json\n{"name": "list_tools", "arguments": "{}"}\n```', known)
    assert len(parse_text_tool_calls('[{"tool": "list_tools"}, {"tool": "search_knowledge", "args": {}}]', known)) == 2
    for not_a_call in ['Hello! {"tool": "list_tools"}', '{"tool": "rm_rf", "args": {}}',
                       '{"a": 1}', "plain text", "", None, '{"tool": "list_tools", "args": [1]}']:
        assert parse_text_tool_calls(not_a_call, known) is None


def test_stream_runs_a_tool_call_written_as_text(home, monkeypatch):
    """The model writes {"tool": …} as text: it must run, not be shown."""
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    a = agent_mod.Agent(load_config(), name="default")
    seen = {}

    async def fake_chat(messages, model, stream=False):
        async def gen():
            for part in ['{"tool": "search_', 'knowledge", "args": {"query": "hello"}}']:
                yield part
        return gen(), []

    async def fake_tools(tool_calls, *args, **kwargs):
        seen["calls"] = tool_calls
        return "Nothing about hello in your notes yet."

    async def no_kb(*a, **k):
        return ""

    monkeypatch.setattr(a, "_provider_chat", fake_chat)
    monkeypatch.setattr(a, "_execute_tools", fake_tools)
    monkeypatch.setattr(a, "_search_knowledge_context", no_kb)

    async def collect():
        return [c async for c in a.stream_think("hello", user_id="t3")]

    chunks = asyncio.run(collect())
    text = "".join(c for c in chunks if not c.startswith("__") and not c.startswith('{"tool"'))
    assert seen["calls"][0]["function"]["name"] == "search_knowledge"
    assert '"args"' not in text and "Nothing about hello" in text


def test_stream_json_answer_that_is_not_a_tool_is_still_shown(home, monkeypatch):
    import myclaw.agent as agent_mod
    from myclaw.config import load_config

    a = agent_mod.Agent(load_config(), name="default")

    async def fake_chat(messages, model, stream=False):
        async def gen():
            yield '{"name": "Ana", '
            yield '"age": 30}'
        return gen(), []

    async def no_kb(*a, **k):
        return ""

    monkeypatch.setattr(a, "_provider_chat", fake_chat)
    monkeypatch.setattr(a, "_search_knowledge_context", no_kb)

    async def collect():
        return "".join([c async for c in a.stream_think("give me json", user_id="t4")])

    assert '{"name": "Ana", "age": 30}' in asyncio.run(collect())


def test_text_tool_followup_uses_plain_messages_and_allows_more_rounds(home, monkeypatch):
    from myclaw.agent_internals import tool_executor
    from myclaw.agent_internals.text_tools import parse_text_tool_calls
    from myclaw.tools import TOOLS

    calls_seen = []
    monkeypatch.setitem(TOOLS, "echo_tool", {"func": lambda text="": f"echo:{text}", "desc": "echo"})
    replies = iter(['{"tool": "echo_tool", "args": {"text": "two"}}', "Final answer: echo two"])

    class FakeAgent:
        model = "m"
        name = "default"
        _kb_auto_extract = False
        _current_task_id = None
        _task_timer = None

        def _detect_browse_failure(self, *a, **k):
            return False

        def _browse_alternative_hint(self, *a, **k):
            return ""

        def _record_kb_gap(self, *a, **k):
            pass

        async def _provider_chat(self, msgs, model):
            calls_seen.append(msgs)
            return next(replies), None

        def _is_empty_response(self, t):
            return not t

    class Mem:
        async def add(self, *a):
            pass

    first = parse_text_tool_calls('{"tool": "echo_tool", "args": {"text": "one"}}')
    out = asyncio.run(tool_executor.execute_tools(FakeAgent(), first, [{"role": "user", "content": "x"}],
                                                  "x", "u", Mem(), 0, True))
    assert out == "Final answer: echo two"
    assert all(m["role"] != "tool" for m in calls_seen[0])          # plain messages only
    assert "echo:one" in calls_seen[0][-1]["content"] and "echo:two" in calls_seen[1][-1]["content"]


# ── v0.7.2: old server / old terminal after an update ────────────────────────

def test_stale_release_is_detected(tmp_path, monkeypatch):
    from myclaw.doctor import paths

    base = tmp_path / "zensynora"
    old, new = base / "releases" / "0.7.0-a", base / "releases" / "9.9.9-b"
    for d, v in ((old, "0.7.0"), (new, "9.9.9")):
        d.mkdir(parents=True)
        (d / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{v}"\n')
    (base / "source").symlink_to(new)
    monkeypatch.setattr(paths, "install_root", lambda: old)
    monkeypatch.setattr(paths, "install_base", lambda: base)
    info = paths.stale_release()
    assert info and info["installed"] == "9.9.9" and info["folder"] == str(new.resolve())
    monkeypatch.setattr(paths, "install_root", lambda: new)
    assert paths.stale_release() is None


def test_spa_serving_no_cache_and_api_404():
    from pathlib import Path

    import myclaw.web.api as api_mod

    if not (Path(api_mod.__file__).parent.parent.parent / "webui" / "dist" / "index.html").exists():
        pytest.skip("web UI not built")
    c = TestClient(api_mod.app)
    r = c.get("/")
    assert r.status_code == 200 and "no-cache" in r.headers.get("cache-control", "")
    assert c.get("/api/does-not-exist").status_code == 404
    assert c.get("/some/page").status_code == 200  # client-side routes still get the app
