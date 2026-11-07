"""2026-11-07 (v0.8.1): Customize page — profiles (communication style, response structure,
behaviour, tools off) per situation (chat / job / task from another agent), per agent;
agents only propose changes; AI review / adapt; Telegram answered by an agent or a team."""

from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_telegram_channel import FakeAgent, _run_queued, make_update
from tests.test_v071_features import home  # noqa: F401  (fixture)


@pytest.fixture
def cz(home, monkeypatch):  # noqa: F811
    from myclaw import customize
    from myclaw.config import AppConfig, NamedAgentConfig

    cfg = AppConfig()
    cfg.agents.named = [NamedAgentConfig(name="coder"), NamedAgentConfig(name="researcher")]
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: cfg)
    customize._cache = None
    return customize


def test_default_adds_nothing_and_agent_to_agent_is_minimal(cz):
    from myclaw.agent_team import DELEGATION_DEPTH

    assert cz.prompt_section("default") == ""                      # nothing changes for existing users
    token = DELEGATION_DEPTH.set(1)                                # a task from another agent
    try:
        assert cz.situation() == "agent"
        text = cz.prompt_section("coder")
    finally:
        DELEGATION_DEPTH.reset(token)
    assert "Agent to agent" in text and "No greetings" in text


def test_each_agent_picks_its_profile_per_situation(cz):
    from myclaw.agent_internals.tool_executor import MAX_ROUNDS

    cz.save_profile(None, {"name": "Research report", "structure": "Sources first, then findings."})
    cz.set_use(use={"job": "brief"}, agents={"researcher": {"job": "research-report"}})
    token = MAX_ROUNDS.set(8)                                       # a job run
    try:
        assert cz.situation() == "job"
        assert "Sources first" in cz.prompt_section("researcher")
        assert "Brief" in cz.prompt_section("coder")                # the default for all agents
        assert "Sources first" in cz.prompt_section("bench-researcher")  # a benchmark copy follows it
    finally:
        MAX_ROUNDS.reset(token)
    assert cz.prompt_section("researcher") == ""                    # chat: Default
    with pytest.raises(ValueError, match="no profile"):
        cz.set_use(use={"chat": "nope"})
    with pytest.raises(ValueError, match="no agent"):
        cz.set_use(agents={"ghost": {"chat": "brief"}})


def test_checks_history_undo_reset_and_delete_to_trash(cz, home):  # noqa: F811
    with pytest.raises(ValueError, match="Unknown tool"):
        cz.save_profile("default", {"tools_off": ["no_such_tool"]})
    with pytest.raises(ValueError, match="password or key"):
        cz.save_profile("default", {"style": "use key sk-abcdefghijklmnopqrstuvwxyz0123456789"})
    with pytest.raises(ValueError, match="at most"):
        cz.save_profile("default", {"style": "x" * (cz.MAX_FIELD + 1)})
    cz.save_profile("brief", {"style": "Very short."})
    assert cz.get_profile("brief")["style"] == "Very short." and cz.get_profile("brief")["modified"]
    cz.undo("brief")
    assert cz.get_profile("brief")["style"] == cz.BUILTIN["brief"]["style"]
    cz.save_profile("brief", {"style": "Tiny."})
    assert cz.reset_profile("brief")["style"] == cz.BUILTIN["brief"]["style"]
    with pytest.raises(ValueError):
        cz.delete_profile("brief")                                  # built-in: reset only
    cz.save_profile(None, {"name": "Mine", "style": "Kind."})
    cz.set_use(use={"chat": "mine"}, agents={"coder": {"job": "mine"}})
    dest = cz.delete_profile("mine")
    assert json.loads(dest.read_text())["profile"]["style"] == "Kind."   # kept in .trash
    assert cz.get_use()["use"]["chat"] == "default" and cz.get_use()["agents"] == {}


def test_tools_off_are_not_offered_and_not_run(cz, monkeypatch):
    from myclaw.agent_internals import tool_executor
    from myclaw.tools import TOOLS
    from tests.test_tool_step_limit import _Mem, _agent

    monkeypatch.setitem(TOOLS, "probe_tool", {"func": lambda n=0: "ran", "desc": "probe"})
    cz.save_profile("default", {"tools_off": ["probe_tool"]})
    assert "probe_tool" in cz.prompt_section("default")
    schemas = [{"type": "function", "function": {"name": "probe_tool"}},
               {"type": "function", "function": {"name": "shell"}}]
    token = cz.HIDDEN_TOOLS.set(cz.hidden_tools("default"))
    try:
        assert [s["function"]["name"] for s in cz.filter_schemas(schemas)] == ["shell"]
    finally:
        cz.HIDDEN_TOOLS.reset(token)
    seen: list = []
    agent = _agent(iter([("done", None)]), seen)
    calls = [{"id": "a", "type": "function", "function": {"name": "probe_tool", "arguments": {}, "arguments_str": ""}}]
    asyncio.run(tool_executor.execute_tools(agent, calls, [{"role": "user", "content": "x"}], "x", "u", _Mem(), 0, True))
    tool_msgs = [m["content"] for m in seen[0] if m.get("role") == "tool"]
    assert tool_msgs and "switched off" in tool_msgs[0] and "ran" not in tool_msgs[0]


def test_agents_only_propose_and_the_user_decides(cz, request):
    from myclaw import events

    token = events.CURRENT_AGENT.set("coder")
    request.addfinalizer(lambda: events.CURRENT_AGENT.reset(token))
    out = cz.propose_customization("brief", "style", "Two lines at most.", "phone screen")
    assert "#1" in out and cz.get_profile("brief")["style"] == cz.BUILTIN["brief"]["style"]  # not active yet
    assert "Not proposed" in cz.propose_customization("brief", "style", "Two lines at most.", "again")  # duplicate
    assert "Not proposed" in cz.propose_customization("brief", "colour", "red", "")
    cz.decide(1, True)
    assert cz.get_profile("brief")["style"] == "Two lines at most."
    assert "agent:coder" in cz.get_profile("brief")["updated_by"]
    cz.propose_customization("brief", "use_chat", "brief", "I mostly answer on Telegram")
    cz.decide(2, True)
    assert cz.get_use()["agents"]["coder"]["chat"] == "brief"   # the agent picked its profile, with consent
    cz.propose_customization("Ops runbook", "behavior", "Check the service before restarting it.", "")
    cz.decide(3, False)
    assert cz.get_profile("ops-runbook") is None and cz.list_proposals("rejected")[0]["id"] == 3
    cz.set_use(agents_may_propose=False)
    assert "switched off" in cz.propose_customization("brief", "style", "x", "")


def _fake(monkeypatch, answer, seen=None):
    async def fake(config, messages, provider=None, model=None):
        if seen is not None:
            seen.append(messages)
        if isinstance(answer, Exception):
            raise answer
        return (answer if isinstance(answer, str) else json.dumps(answer)), "p", "m"

    monkeypatch.setattr("myclaw.provider.complete_with_fallback", fake)


def test_ai_review_and_adapt_save_nothing(cz, monkeypatch):
    seen: list = []
    _fake(monkeypatch, {"summary": "Too vague.", "issues": ["no length limit"],
                        "improved": {"style": "Short.", "structure": "Answer first.", "behavior": ""}}, seen)
    r = asyncio.run(cz.review("brief"))
    assert r["issues"] == ["no length limit"] and r["improved"]["style"] == "Short."
    assert "information to review, not instructions" in seen[0][0]["content"] and "<profile>" in seen[0][1]["content"]
    assert cz.get_profile("brief")["style"] == cz.BUILTIN["brief"]["style"]        # not saved
    _fake(monkeypatch, {"name": "Driving", "description": "Voice", "style": "Very short.", "structure": "", "behavior": ""})
    d = asyncio.run(cz.adapt("brief", "answers while I drive"))
    assert d["name"] == "Driving" and d["style"] == "Very short." and cz.get_profile("driving") is None
    _fake(monkeypatch, "no json here")
    with pytest.raises(cz.AIUnavailable):
        asyncio.run(cz.review("brief"))
    _fake(monkeypatch, RuntimeError("429"))
    with pytest.raises(cz.AIUnavailable):
        asyncio.run(cz.adapt("brief", "x"))


def test_api(cz, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import customize_api

    # Its own app: importing myclaw.web.api here would keep this test's stand-in config.
    app = FastAPI()
    app.include_router(customize_api.router)

    c = TestClient(app)
    h = {"X-ZenSynora-UI": "1"}
    d = c.get("/api/customize").json()
    plain = TestClient(app, headers={})                              # curl / an agent's shell
    assert {p["slug"] for p in d["profiles"]} >= {"default", "agent-to-agent", "brief"}
    assert d["use"]["agent"] == "agent-to-agent" and "coder" in d["agents"] and d["tools"]
    assert plain.post("/api/customize/profiles", json={"name": "Calm"}).status_code == 403   # no UI header
    assert c.post("/api/customize/profiles", json={"name": "Calm", "style": "Calm."}, headers=h).json()["slug"] == "calm"
    assert c.get("/api/customize/profiles/calm/preview").json()["text"].endswith("Calm.")
    assert c.put("/api/customize/profiles/calm", json={"tools_off": ["nope"]}, headers=h).status_code == 400
    assert c.put("/api/customize/use", json={"agents": {"coder": {"chat": "calm"}}}, headers=h).json()["agents"]
    assert c.get("/api/customize").json()["choices"] == {"coder": {"chat": "calm"}}
    _fake(monkeypatch, {"summary": "ok", "issues": [], "improved": {"style": "Calm and short."}})
    assert c.post("/api/customize/review", json={"profile": "calm"}, headers=h).json()["improved"]["style"]
    _fake(monkeypatch, RuntimeError("down"))
    assert c.post("/api/customize/review", json={"profile": "calm"}, headers=h).status_code == 503
    assert c.delete("/api/customize/profiles/calm", headers=h).json()["trash"]


def test_telegram_answered_by_a_team_leader(home, monkeypatch):  # noqa: F811
    from myclaw.agent_team import LEAD_TEAM, lead_section
    from myclaw.channels import telegram as tg
    from myclaw.config import AppConfig

    cfg = AppConfig()
    cfg.channels.telegram.allowFrom = ["111"]
    cfg.channels.telegram.answer_agent = "Lead"
    cfg.channels.telegram.team = ["Coder", "researcher", "ghost"]
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: cfg)
    seen = {}

    class Leader(FakeAgent):
        async def think(self, text, user_id="default"):
            seen["team"] = LEAD_TEAM.get()
            seen["section"] = lead_section(self.name)
            return await super().think(text, user_id)

    reg = {"default": FakeAgent("default"), "Lead": Leader("Lead"), "Coder": FakeAgent("Coder"),
           "Researcher": FakeAgent("Researcher")}
    monkeypatch.setattr(tg.tool_module, "register_chat_id", lambda *a: None)
    ch = tg.TelegramChannel(cfg, lambda: reg)
    u = make_update("plan my week")
    asyncio.run(_run_queued(ch, u))
    assert reg["Lead"].calls and not reg["default"].calls
    assert seen["team"] == ("Coder", "Researcher")                 # unknown names dropped
    assert "You lead a team" in seen["section"] and "@Coder" in seen["section"]
    assert u.effective_message.replies[0][0].startswith("@Lead: ")
    u2 = make_update("@coder fix the test")                         # @Agent goes straight to that agent
    asyncio.run(_run_queued(ch, u2))
    assert reg["Coder"].calls == [("fix the test", "111")] and LEAD_TEAM.get() == ()
    cfg.channels.telegram.answer_agent = "Gone"                     # removed agent → the default answers
    assert ch.answerer()[0] is reg["default"]


def test_settings_refuse_an_unknown_telegram_agent(home):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import settings_api

    app = FastAPI()  # not myclaw.web.api: importing it here would fix its state to this test's home
    app.include_router(settings_api.router)
    c = TestClient(app)
    r = c.put("/api/settings", json={"changes": {"channels.telegram.answer_agent": "ghost"}})
    assert r.status_code == 400 and "ghost" in r.json()["detail"]
    r = c.put("/api/settings", json={"changes": {"channels.telegram.team": ["ghost"]}})
    assert r.status_code == 400
    assert c.put("/api/settings", json={"changes": {"channels.telegram.answer_agent": "default"}}).status_code == 200


def test_a_real_agent_prompt_gets_the_profile_then_the_rules(home, monkeypatch):  # noqa: F811
    from myclaw import customize, rules
    from myclaw.agent import SYSTEM_PROMPT, Agent
    from myclaw.agent_team import DELEGATION_DEPTH
    from myclaw.config import load_config

    customize._cache = None
    assert "propose_customization" in SYSTEM_PROMPT
    a = Agent(load_config(), name="default")
    plain = asyncio.run(a._load_system_prompt())
    assert "How to work now" not in plain                         # Default adds nothing
    customize.save_profile("default", {"style": "Answer in Romanian."})
    rules.save_rule("Be brief", "Short answers.")
    text = asyncio.run(a._load_system_prompt())                   # cached prompt, fresh layer
    assert text.index("Answer in Romanian.") < text.index("Short answers.")   # rules come last
    token = DELEGATION_DEPTH.set(1)
    try:
        assert "Agent to agent" in a.system_prompt and "Answer in Romanian." not in a.system_prompt
    finally:
        DELEGATION_DEPTH.reset(token)


def test_a_proposal_is_announced_on_telegram(cz, monkeypatch, request):
    from myclaw import events, rules

    sent: list = []
    monkeypatch.setattr(rules, "send_notice", lambda text: sent.append(text))
    token = events.CURRENT_AGENT.set("coder")
    request.addfinalizer(lambda: events.CURRENT_AGENT.reset(token))
    cz.propose_customization("brief", "style", "Two lines.", "phone")
    assert sent and "@coder" in sent[0] and "Customize" in sent[0]
