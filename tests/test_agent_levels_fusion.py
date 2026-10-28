"""2026-10-27: agent levels (a label), capabilities of every agent, agent fusion.

Offline, temporary HOME; agents and models are faked.
"""

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from myclaw.web.agents_api import _ORIGIN as WEB  # the Web UI's origin (only it and the CLI may apply)
from tests.test_v071_features import FakeAgent, _wait_turn, home  # noqa: F401  (fixture)


def _write_config(home_dir, named, defaults=None):
    raw = {"agents": {"defaults": {"model": "m0", "provider": "ollama", **(defaults or {})}, "named": named}}
    (home_dir / ".myclaw" / "config.json").write_text(json.dumps(raw), encoding="utf-8")
    import myclaw.config as cfg_mod

    cfg_mod._cached_config = None


AGENTS = [
    {"name": "Writer", "model": "w-model", "provider": "ollama", "description": "Writes texts",
     "system_prompt": "You write clear texts.", "skills": ["web-research"], "level": 5, "context_length": 4096},
    {"name": "Editor", "model": "e-model", "provider": "ollama", "description": "Edits texts",
     "system_prompt": "You fix grammar.", "skills": ["editing"], "level": 3, "context_length": 8192},
    {"name": "Other", "model": "o-model", "description": "Something else"},
]


# ── Levels ───────────────────────────────────────────────────────────────────
def test_level_defaults_to_1_and_is_validated():
    from myclaw.config import AgentDefaults, AppConfig, NamedAgentConfig

    assert AgentDefaults().level == 1 and NamedAgentConfig(name="x").level == 1
    assert AppConfig(**{"agents": {"named": [{"name": "old"}]}}).agents.named[0].level == 1  # old configs
    for bad in (0, 10, -1):
        with pytest.raises(ValidationError):
            NamedAgentConfig(name="x", level=bad)
        with pytest.raises(ValidationError):
            AgentDefaults(level=bad)
    assert NamedAgentConfig(name="x", level=9).level == 9


def test_level_round_trip_through_settings_api(home):  # noqa: F811
    from myclaw.config import load_config
    from myclaw.web import settings_api

    _write_config(home, [{"name": "Writer", "model": "w"}])
    app = FastAPI()
    app.include_router(settings_api.router)
    c = TestClient(app)
    named = c.get("/api/settings").json()["config"]["agents"]["named"]
    assert named[0]["level"] == 1
    named[0]["level"] = 7
    r = c.put("/api/settings", json={"changes": {"agents.named": named, "agents.defaults.level": 4}})
    assert r.status_code == 200, r.text
    cfg = load_config(force_reload=True)
    assert cfg.agents.named[0].level == 7 and cfg.agents.defaults.level == 4
    assert c.get("/api/settings").json()["config"]["agents"]["named"][0]["level"] == 7
    named[0]["level"] = 12
    assert c.put("/api/settings", json={"changes": {"agents.named": named}}).status_code == 422
    assert load_config(force_reload=True).agents.named[0].level == 7


def test_prompt_mentions_level_and_capabilities():
    from myclaw.agent_team import team_section
    from myclaw.config import AppConfig

    cfg = AppConfig(**{"agents": {"defaults": {"level": 2}, "named": [{"name": "Writer", "level": 6}]}})
    s = team_section(cfg, "Writer")
    assert "level 6 of 9" in s and "only a label" in s
    for tool in ("create_skill(", "assign_skill(", "register_tool(", "job_create(", "delegate(",
                 "write_to_knowledge(", "request_information("):
        assert tool in s, tool
    assert "level 2" in s  # the other agents' levels
    alone = team_section(AppConfig(**{"agents": {"allow_agent_creation": False, "allow_job_creation": False}}),
                         "default")
    assert "level 1 of 9" in alone and "request_information(" in alone and "turned off" in alone
    from myclaw.agent import SYSTEM_PROMPT

    assert "create_skill(" in SYSTEM_PROMPT and "request_information(" in SYSTEM_PROMPT


def test_capability_tools_are_registered():
    from myclaw.tools import TOOLS, ensure_tool_schemas, TOOL_SCHEMAS

    for name in ("create_skill", "assign_skill", "request_information", "register_tool", "job_create",
                 "delegate", "write_to_knowledge", "propose_agent_fusion", "list_agent_fusions"):
        assert name in TOOLS, name
    ensure_tool_schemas()
    names = {s["function"]["name"] for s in TOOL_SCHEMAS}
    assert {"create_skill", "request_information", "propose_agent_fusion"} <= names


# ── create_skill / assign_skill ──────────────────────────────────────────────
def test_create_skill_refuses_overwrite_and_builtins(home):  # noqa: F811
    from myclaw import agent_skills
    from myclaw.agent_caps import create_skill

    out = create_skill("Invoice Check", "Check invoices", "1. Read the invoice\n2. Check totals")
    assert out.startswith("Created skill 'invoice-check'")
    sk = agent_skills.get_skill("invoice-check")
    assert sk["source"] == "yours" and sk["description"] == "Check invoices" and "Check totals" in sk["body"]
    assert "invoice-check" in [s["name"] for s in agent_skills.list_skills()]  # on the Skills page
    again = create_skill("invoice-check", "Other", "Other text")
    assert again.startswith("Error") and "never overwritten" in again
    assert "Check totals" in agent_skills.get_skill("invoice-check")["body"]
    builtin = next(s for s in agent_skills.list_skills() if s["source"] == "builtin")
    out = create_skill(builtin["name"], "mine", "replace it")
    assert out.startswith("Error") and "built-in" in out
    assert agent_skills.get_skill(builtin["name"])["source"] == "builtin"
    assert create_skill("x y", "", "text").startswith("Error")
    assert create_skill("ok-name", "desc", "  ").startswith("Error")
    assert create_skill("!!!", "desc", "body").startswith("Error")


def test_skills_page_lists_a_skill_made_by_an_agent(home):  # noqa: F811
    from myclaw.agent_caps import create_skill
    from myclaw.web import skills_api

    create_skill("report-style", "How reports look", "Use headings.")
    app = FastAPI()
    app.include_router(skills_api.router)
    rows = TestClient(app).get("/api/skills").json()["skills"]
    assert any(s["name"] == "report-style" and s["source"] == "yours" for s in rows)


def test_assign_skill_only_to_yourself(home):  # noqa: F811
    from myclaw.agent_caps import assign_skill, create_skill
    from myclaw.config import load_config
    from myclaw.events import CURRENT_AGENT

    _write_config(home, [{"name": "Writer"}, {"name": "Editor"}])
    create_skill("tone", "Tone of voice", "Be friendly.")
    tok = CURRENT_AGENT.set("Writer")
    try:
        assert assign_skill("tone", agent="Editor").startswith("Error: you can only give skills to yourself")
        assert assign_skill("nope").startswith("Error: no skill")
        assert assign_skill("tone").startswith("Gave the skill 'tone' to @Writer")
        assert "already" in assign_skill("tone")
    finally:
        CURRENT_AGENT.reset(tok)
    cfg = load_config(force_reload=True)
    assert [a.skills for a in cfg.agents.named] == [["tone"], []]


# ── request_information ──────────────────────────────────────────────────────
@pytest.fixture
def gaps(home, monkeypatch):  # noqa: F811
    from myclaw.knowledge import researcher

    path = home / ".myclaw" / "knowledge_gaps.jsonl"
    monkeypatch.setattr(researcher, "GAP_FILE", path)
    return path


def test_request_information_posts_a_question_and_records_a_gap(gaps):
    from myclaw.agent_caps import QUESTION_BUDGET, request_information
    from myclaw.chat import store
    from myclaw.events import CURRENT_AGENT, CURRENT_CHANNEL

    store.create_channel("work", ["default", "Writer"], channel_id="work")

    async def run():
        toks = (CURRENT_CHANNEL.set("work"), CURRENT_AGENT.set("Writer"), QUESTION_BUDGET.set({"n": 0}))
        try:
            outs = [await request_information(f"topic {i}", f"question {i}?", "to finish") for i in range(4)]
            return outs
        finally:
            for var, t in zip((CURRENT_CHANNEL, CURRENT_AGENT, QUESTION_BUDGET), toks):
                var.reset(t)

    outs = asyncio.run(run())
    assert all(o.startswith("Question sent to the user") for o in outs[:3])
    assert outs[3].startswith("Not sent") and "3 questions" in outs[3]  # rate limit per turn
    msgs = [m for m in store.list_messages("work") if m["role"] == "agent"]
    assert len(msgs) == 3 and msgs[0]["sender"] == "Writer"
    assert "Question from @Writer" in msgs[0]["text"] and msgs[0]["meta"]["question"]["topic"] == "topic 0"
    lines = [json.loads(x) for x in gaps.read_text().splitlines()]
    assert [x["query"] for x in lines] == ["topic 0", "topic 1", "topic 2"]
    from myclaw.knowledge.researcher import GapResearcher

    assert {g["query"] for g in GapResearcher.__new__(GapResearcher)._get_unprocessed_gaps()} == \
        {"topic 0", "topic 1", "topic 2"}


def test_request_information_validates_and_works_without_a_chat(gaps):
    from myclaw.agent_caps import request_information

    assert asyncio.run(request_information("", "q?")).startswith("Error")
    assert asyncio.run(request_information("t", "")).startswith("Error")
    out = asyncio.run(request_information("printer model", "Which printer do you use?"))
    assert "no chat" in out and "printer model" in gaps.read_text()


def test_request_information_in_a_job_goes_into_the_run_result(gaps):
    from myclaw.agent_caps import QUESTION_BUDGET, new_turn_budget, request_information

    budget = new_turn_budget(job_run="abc-7")

    async def run():
        t = QUESTION_BUDGET.set(budget)
        try:
            return await request_information("budget", "What is the budget?")
        finally:
            QUESTION_BUDGET.reset(t)

    assert "job run's result" in asyncio.run(run())
    assert len(budget["questions"]) == 1 and "What is the budget?" in budget["questions"][0]


def test_the_users_reply_goes_to_the_agent_that_asked(gaps):
    """In a 'lead' channel the first member answers — but a reply to a question goes to the asker."""
    from myclaw.agent_caps import request_information
    from myclaw.chat import store
    from myclaw.web import chat_api

    class Asker(FakeAgent):
        async def stream_think(self, prompt, user_id="default"):
            self.prompts.append(prompt)
            if len(self.prompts) == 1:
                await request_information("deadline", "When is the deadline?")
            yield "ok"

    store.create_channel("work", ["default", "Writer"], channel_id="work")
    agents = {"default": FakeAgent("default"), "Writer": Asker("Writer")}
    chat_api.set_registry_getter(lambda: agents)
    app = FastAPI()
    app.include_router(chat_api.router)
    with TestClient(app) as c:
        c.post("/api/channels/work/messages", json={"text": "@Writer write the plan"})
        _wait_turn(c, "work", 2)  # the question + the answer
        c.post("/api/channels/work/messages", json={"text": "Friday"})
        msgs = _wait_turn(c, "work", 3)
    last = [m for m in msgs if m["role"] == "agent"][-1]
    assert last["sender"] == "Writer"
    assert agents["default"].prompts == []
    assert "deadline" in agents["Writer"].prompts[-1] and agents["Writer"].prompts[-1].endswith("Friday")


# ── Fusion ───────────────────────────────────────────────────────────────────
@pytest.fixture
def team(home):  # noqa: F811
    from myclaw.chat import store
    from myclaw.jobs import store as jobs

    _write_config(home, [dict(a) for a in AGENTS])
    store.create_channel("general", ["default", "Writer", "Editor"], channel_id="general")
    store.create_channel("docs", ["Editor"], channel_id="docs")
    store.add_message(store.dm_channel("Editor")["id"], "user", "you", "fix this")
    store.add_message("dm-editor", "agent", "Editor", "fixed")
    store.dm_channel("Writer")
    job = jobs.create_job({"name": "Proofread", "prompt": "proofread", "agent": "Editor",
                           "channel_id": "dm-editor"})
    return home, job


def test_fused_definition_takes_the_lower_level():
    from myclaw.agent_fusion import fused_definition

    a, b = dict(AGENTS[0]), dict(AGENTS[1])
    f = fused_definition(a, b)
    assert f["level"] == 3 and f["name"] == "Writer"
    assert f["skills"] == ["web-research", "editing"] and f["context_length"] == 8192
    assert f["model"] == "w-model"  # the higher-level agent's model
    assert "You write clear texts." in f["system_prompt"] and "You fix grammar." in f["system_prompt"]
    b["level"] = 9
    assert fused_definition(a, b, "Duo")["model"] == "e-model" and fused_definition(a, b)["level"] == 5


def test_fusion_is_only_a_proposal_until_approved(team):
    from myclaw import agent_fusion
    from myclaw.config import load_config

    prop = agent_fusion.create_fusion("writer", "Editor", "", "same job", use_model=False)
    assert prop["status"] == "pending" and prop["fused"]["level"] == 3
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Writer", "Editor", "Other"]
    pv = agent_fusion.preview(prop["id"])
    assert pv["impact"]["jobs"][0]["name"] == "Proofread"
    assert {c["id"] for c in pv["impact"]["channels"]} == {"general", "docs"}
    assert "+You fix grammar." in pv["diff_a"] and "will be removed" in pv["summary"]
    with pytest.raises(ValueError, match="already waiting"):
        agent_fusion.create_fusion("Editor", "Writer", use_model=False)
    assert agent_fusion.reject_fusion(prop["id"], WEB)["status"] == "rejected"
    with pytest.raises(ValueError, match="only pending"):
        agent_fusion.apply_fusion(prop["id"], WEB)
    assert len(load_config(force_reload=True).agents.named) == 3


def test_fusion_refuses_default_and_unknown_agents(team):
    from myclaw import agent_fusion

    with pytest.raises(ValueError, match="default agent"):
        agent_fusion.create_fusion("default", "Writer", use_model=False)
    with pytest.raises(ValueError, match="no agent called 'Ghost'"):
        agent_fusion.create_fusion("Writer", "Ghost", use_model=False)
    with pytest.raises(ValueError, match="two different"):
        agent_fusion.create_fusion("Writer", "writer", use_model=False)
    with pytest.raises(ValueError, match="already exists"):
        agent_fusion.create_fusion("Writer", "Editor", "Other", use_model=False)
    from myclaw.events import CURRENT_AGENT

    tok = CURRENT_AGENT.set("medic")
    try:
        out = asyncio.run(agent_fusion.propose_agent_fusion("default", "Writer", reason="x"))
        assert out.startswith("Error") and "default agent" in out
        assert asyncio.run(agent_fusion.propose_agent_fusion("Writer", "Editor")).startswith("Error")  # no reason
    finally:
        CURRENT_AGENT.reset(tok)
    assert agent_fusion.list_fusions() == []


def test_medic_tool_proposes_with_a_model_written_prompt(team, monkeypatch):
    from myclaw import agent_fusion, provider
    from myclaw.agents import medic_agent
    from myclaw.config import load_config

    seen = []

    async def fake_complete(cfg, messages, provider_name=None, model=None):
        seen.append(messages)
        return "You are Writer. You write clear texts and fix their grammar.", "ollama", "m0"

    from myclaw.events import CURRENT_AGENT

    monkeypatch.setattr(provider, "complete_with_fallback", fake_complete)
    tok = CURRENT_AGENT.set("medic")
    try:
        out = asyncio.run(medic_agent.propose_agent_fusion("Writer", "Editor", reason="they overlap"))
    finally:
        CURRENT_AGENT.reset(tok)
    assert out.startswith("Fusion proposal") and "Nothing changed yet" in out and "model ollama/m0" in out
    p = agent_fusion.list_fusions("pending")[0]
    assert p["author"].startswith("agent:") and p["fused"]["system_prompt"].endswith("fix their grammar.")
    assert "information to merge" in seen[0][1]["content"]  # the prompts are quoted as information
    assert len(load_config(force_reload=True).agents.named) == 3  # still only a proposal

    async def broken(*a, **k):
        raise RuntimeError("model down")

    monkeypatch.setattr(provider, "complete_with_fallback", broken)
    agent_fusion.reject_fusion(p["id"], WEB)
    tok = CURRENT_AGENT.set("medic")
    try:
        out = asyncio.run(agent_fusion.propose_agent_fusion("Writer", "Editor", reason="again"))
    finally:
        CURRENT_AGENT.reset(tok)
    assert "headings (the model did not answer)" in out
    assert "## From @Editor" in agent_fusion.list_fusions("pending")[0]["fused"]["system_prompt"]


def test_apply_moves_channels_jobs_conversations_and_undo_restores(team):
    from myclaw import agent_fusion
    from myclaw.chat import store
    from myclaw.config import load_config
    from myclaw.jobs import store as jobs

    _, job = team
    prop = agent_fusion.create_fusion("Writer", "Editor", "", "overlap", use_model=False)
    applied = agent_fusion.apply_fusion(prop["id"], WEB)
    assert applied["status"] == "applied" and applied["moved"]["jobs"] == 1
    cfg = load_config(force_reload=True)
    assert [a.name for a in cfg.agents.named] == ["Writer", "Other"]
    w = cfg.agents.named[0]
    assert w.level == 3 and w.skills == ["web-research", "editing"] and "You fix grammar." in w.system_prompt
    assert store.get_channel("general")["members"] == ["default", "Writer"]
    assert store.get_channel("docs")["members"] == ["Writer"]
    assert store.get_channel("dm-editor") is None
    texts = [m["text"] for m in store.list_messages("dm-writer")]
    assert "fix this" in texts and "fixed" in texts and any("moved here" in t for t in texts)
    j = jobs.get_job(job["id"])
    assert j["agent"] == "Writer" and j["channel_id"] == "dm-writer"
    backup = agent_fusion.get_backup(prop["id"])
    assert set(backup["originals"]) == {"Writer", "Editor"}
    assert backup["originals"]["Editor"]["system_prompt"] == "You fix grammar."

    agent_fusion.undo_fusion(prop["id"], WEB)
    cfg = load_config(force_reload=True)
    for a in cfg.agents.named[:2]:  # exactly the definitions from before
        assert {k: v for k, v in a.model_dump().items() if k in backup["originals"][a.name]} == \
            backup["originals"][a.name]
    assert [a.name for a in cfg.agents.named] == ["Writer", "Editor", "Other"]
    assert cfg.agents.named[0].level == 5 and cfg.agents.named[1].level == 3
    assert cfg.agents.named[1].system_prompt == "You fix grammar." and cfg.agents.named[0].skills == ["web-research"]
    assert store.get_channel("general")["members"] == ["default", "Writer", "Editor"]
    assert store.get_channel("docs")["members"] == ["Editor"]
    assert [m["text"] for m in store.list_messages("dm-editor")] == ["fix this", "fixed"]
    assert not any("moved here" in m["text"] for m in store.list_messages("dm-writer"))
    j = jobs.get_job(job["id"])
    assert j["agent"] == "Editor" and j["channel_id"] == "dm-editor"
    assert agent_fusion.get_fusion(prop["id"])["status"] == "undone"
    with pytest.raises(ValueError):
        agent_fusion.undo_fusion(prop["id"], WEB)


def test_fusion_under_a_new_name_and_with_the_default_agent(team):
    from myclaw import agent_fusion
    from myclaw.chat import store
    from myclaw.config import load_config

    prop = agent_fusion.create_fusion("Writer", "Editor", "Duo", use_model=False)
    agent_fusion.apply_fusion(prop["id"], WEB)
    cfg = load_config(force_reload=True)
    assert [a.name for a in cfg.agents.named] == ["Duo", "Other"]
    assert store.get_channel("general")["members"] == ["default", "Duo"]
    assert store.get_channel("dm-duo") is not None
    agent_fusion.undo_fusion(prop["id"], WEB)
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Writer", "Editor", "Other"]
    assert store.get_channel("general")["members"] == ["default", "Writer", "Editor"]
    assert store.get_channel("dm-duo") is None  # it was created for the fusion and is empty again

    prop = agent_fusion.create_fusion("default", "Other", reason="x", use_model=False, allow_default=True)
    assert prop["fused"]["name"] == "default"
    agent_fusion.apply_fusion(prop["id"], WEB)
    cfg = load_config(force_reload=True)
    assert [a.name for a in cfg.agents.named] == ["Writer", "Editor"] and cfg.agents.defaults.model == "m0"
    agent_fusion.undo_fusion(prop["id"], WEB)
    assert [a.name for a in load_config(force_reload=True).agents.named] == ["Writer", "Editor", "Other"]


def test_a_job_run_records_the_agents_questions(gaps):
    from myclaw.agent_caps import request_information
    from myclaw.jobs import store
    from myclaw.jobs.runner import JobRunner

    class Asker(FakeAgent):
        async def think(self, prompt, user_id="default", _depth=0):
            await request_information("server names", "Which servers should I check?")
            return "Checked what I could."

    job = store.create_job({"name": "check", "prompt": "check the servers"})
    asyncio.run(JobRunner(lambda: {"default": Asker("default")}).run_job(job["id"]))
    run = store.list_runs(job["id"])[0]
    assert run["status"] == "ok"
    assert run["output"].startswith("Checked what I could.") and "Which servers should I check?" in run["output"]
    assert "server names" in gaps.read_text()


def test_apply_refuses_when_an_agent_changed_since(team):
    from myclaw import agent_fusion

    home_dir, _ = team
    prop = agent_fusion.create_fusion("Writer", "Editor", use_model=False)
    changed = [dict(a) for a in AGENTS]
    changed[1]["system_prompt"] = "New prompt"
    _write_config(home_dir, changed)
    with pytest.raises(ValueError, match="changed or removed"):
        agent_fusion.apply_fusion(prop["id"], WEB)
    assert agent_fusion.get_fusion(prop["id"])["status"] == "pending"


def test_fusion_api_routes(team):
    from myclaw.web import agents_api

    app = FastAPI()
    app.include_router(agents_api.router)
    c = TestClient(app)
    r = c.post("/api/agents/fusions", json={"agent_a": "Writer", "agent_b": "Editor", "use_model": False})
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert r.json()["impact"]["jobs"] and r.json()["fused"]["level"] == 3
    assert c.post("/api/agents/fusions", json={"agent_a": "default", "agent_b": "Editor",
                                               "use_model": False}).status_code == 400
    assert [f["id"] for f in c.get("/api/agents/fusions?status=pending").json()["fusions"]] == [fid]
    assert c.get("/api/agents/fusions/nope").status_code == 404
    assert c.post(f"/api/agents/fusions/{fid}/approve").json()["status"] == "applied"
    assert c.post(f"/api/agents/fusions/{fid}/approve").status_code == 409
    assert c.post(f"/api/agents/fusions/{fid}/undo").json()["status"] == "undone"


def test_cli_fusion_commands(team):
    from click.testing import CliRunner

    from myclaw import agent_fusion
    from myclaw.cli import cli

    run = CliRunner()
    r = run.invoke(cli, ["agents", "fuse", "Writer", "Editor", "--no-model"])
    assert r.exit_code == 0, r.output
    fid = agent_fusion.list_fusions()[0]["id"]
    assert "L3" in run.invoke(cli, ["agents", "fusions"]).output
    assert "will be removed" in run.invoke(cli, ["agents", "show", fid]).output
    assert run.invoke(cli, ["agents", "approve", fid]).exit_code == 0
    assert run.invoke(cli, ["agents", "undo", fid]).exit_code == 0
    assert agent_fusion.get_fusion(fid)["status"] == "undone"
    assert "L5" in run.invoke(cli, ["agents", "list"]).output
