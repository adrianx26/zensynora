"""Rules (2026-10-27): the user's rules, agents' proposals, the prompt section,
the API, the CLI and the doctor check. Real files in a temporary HOME."""

from __future__ import annotations

import asyncio
import json
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def home(tmp_path, monkeypatch):
    import myclaw.config as cfg_mod
    from myclaw import rules
    from myclaw.web import settings_api

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw").mkdir()
    cfg_file = tmp_path / ".myclaw" / "config.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(settings_api, "CONFIG_FILE", cfg_file)
    cfg_mod._cached_config = None
    monkeypatch.setattr(rules, "_known_agents", lambda: ["default", "Researcher", "Coder"])
    rules._invalidate()
    yield tmp_path
    cfg_mod._cached_config = None
    rules._invalidate()


@pytest.fixture
def as_agent():
    from myclaw.events import CURRENT_AGENT

    tokens = []

    def _set(name):
        tokens.append(CURRENT_AGENT.set(name))

    yield _set
    for t in reversed(tokens):
        CURRENT_AGENT.reset(t)


def _md_files(home):
    d = home / ".myclaw" / "rules"
    return sorted(p.name for p in d.glob("*.md")) if d.exists() else []


def _trash(home):
    d = home / ".myclaw" / "rules" / ".trash"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# ── store ────────────────────────────────────────────────────────────────────
def test_save_read_round_trip_and_file_format(home):
    from myclaw import rules

    r = rules.save_rule("Answer in Romanian", "Always answer in Romanian.\n\nEven for English questions.",
                        scope=["Researcher", "coder"], priority="high", enabled=False)
    assert r["slug"] == "answer-in-romanian"
    back = rules.get_rule("answer-in-romanian")
    assert back["title"] == "Answer in Romanian"
    assert back["text"] == "Always answer in Romanian.\n\nEven for English questions."
    assert back["scope"] == ["Researcher", "Coder"]  # names normalised to the agents' spelling
    assert back["priority"] == "high" and back["enabled"] is False and back["author"] == "user"
    assert back["created"] and back["updated"]
    raw = (home / ".myclaw" / "rules" / "answer-in-romanian.md").read_text()
    assert raw.startswith("---\ntitle: Answer in Romanian\nscope: Researcher, Coder\npriority: high\n"
                          "enabled: false\nauthor: user\n")


def test_update_and_delete_keep_old_versions_in_trash(home):
    from myclaw import rules

    rules.save_rule("Be brief", "Keep answers short.")
    rules.update_rule("be-brief", text="Keep answers under five lines.")
    assert rules.get_rule("be-brief")["text"] == "Keep answers under five lines."
    rules.update_rule("be-brief", enabled=False)
    assert rules.get_rule("be-brief")["enabled"] is False
    trash = _trash(home)
    assert len(trash) == 2 and all(n.startswith("be-brief.") and "replaced" in n for n in trash)
    assert "Keep answers short." in (home / ".myclaw" / "rules" / ".trash" / sorted(trash)[0]).read_text()
    where = rules.delete_rule("be-brief")
    assert _md_files(home) == [] and rules.get_rule("be-brief") is None
    assert "five lines" in open(where).read() and "deleted" in where
    with pytest.raises(KeyError):
        rules.delete_rule("be-brief")


def test_create_refuses_duplicate_and_validation_messages(home):
    from myclaw import rules

    rules.save_rule("Be brief", "x", create=True)
    with pytest.raises(ValueError, match="already exists"):
        rules.save_rule("Be brief", "y", create=True)
    with pytest.raises(ValueError, match="title"):
        rules.save_rule("", "y")
    with pytest.raises(ValueError, match="text"):
        rules.save_rule("T", "  ")
    with pytest.raises(ValueError, match="too long"):
        rules.save_rule("T", "x" * (rules.MAX_TEXT + 1))
    with pytest.raises(ValueError, match="Priority"):
        rules.save_rule("T", "x", priority="urgent")
    with pytest.raises(ValueError, match="Unknown agent"):
        rules.save_rule("T", "x", scope="Nobody")


@pytest.mark.parametrize("slug", ["../config", "..", "a/b", "/etc/passwd", ".proposals", "x.y", "A B", ""])
def test_path_traversal_is_refused_everywhere(home, as_agent, slug):
    from myclaw import rules

    with pytest.raises(ValueError):
        rules.get_rule(slug)
    with pytest.raises(ValueError):
        rules.delete_rule(slug)
    as_agent("Researcher")
    assert rules.propose_rule_deletion(slug, "x").startswith("Not proposed")
    assert rules.agent_read_rule(slug).startswith("Error")
    for pid in ("../x", "0", "-1", "abc"):
        assert rules.get_proposal(pid) is None


@pytest.mark.parametrize("secret", [
    "Use api_key = sk-abcdefghijklmnopqrstuvwxyz0123456789",
    "The bot token: 123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw",
    "password: hunter2hunter2",
])
def test_secrets_are_refused_for_user_and_agents(home, as_agent, secret):
    from myclaw import rules

    with pytest.raises(ValueError, match="secret"):
        rules.save_rule("Keys", secret)
    as_agent("Researcher")
    assert "secret" in rules.propose_rule("Keys", secret, reason="r")
    assert rules.list_proposals() == []


# ── proposals ────────────────────────────────────────────────────────────────
def test_agent_tools_only_propose(home, as_agent):
    from myclaw import rules

    as_agent("Researcher")
    out = rules.propose_rule("Cite sources", "Always cite the pages you used.", scope="all", reason="quality")
    assert out == "Proposal #1 sent to the user for approval — it is not active yet."
    assert _md_files(home) == [] and rules.prompt_section("default") == ""
    p = rules.get_proposal(1)
    assert p["action"] == "create" and p["slug"] == "cite-sources" and p["proposed_by"] == "Researcher"
    assert p["status"] == "pending" and p["reason"] == "quality" and p["decided_at"] is None
    assert "author: agent:Researcher" in p["content"]
    assert "Proposal #1" in rules.agent_list_rules() or "#1" in rules.agent_list_rules()


def test_create_modify_delete_proposals_approved(home, as_agent):
    from myclaw import rules

    as_agent("Coder")
    rules.propose_rule("Run tests", "Run the tests before saying it works.", reason="bugs", priority="high")
    rules.approve(1, by="user")
    r = rules.get_rule("run-tests")
    assert r["text"] == "Run the tests before saying it works." and r["author"] == "agent:Coder"
    assert r["priority"] == "high" and rules.get_proposal(1)["status"] == "approved"
    assert rules.get_proposal(1)["decided_by"] == "user" and rules.get_proposal(1)["decided_at"]

    rules.update_rule("run-tests", enabled=False)  # the user switched it off meanwhile
    out = rules.propose_rule_change("run-tests", "Run the full test suite before saying it works.", "be thorough")
    assert out.startswith("Proposal #2")
    p2 = rules.get_proposal(2)
    diff = rules.proposal_diff(p2)
    assert "-Run the tests before" in diff and "+Run the full test suite" in diff
    assert rules.get_rule("run-tests")["text"].startswith("Run the tests")  # not active yet
    rules.approve(2)
    r = rules.get_rule("run-tests")
    assert r["text"].startswith("Run the full test suite") and r["enabled"] is False  # switch kept
    assert any("replaced" in n for n in _trash(home))

    assert rules.propose_rule_deletion("run-tests", "not needed").startswith("Proposal #3")
    assert rules.get_rule("run-tests") is not None
    rules.approve(3)
    assert rules.get_rule("run-tests") is None and _md_files(home) == []
    assert any("deleted" in n for n in _trash(home))
    with pytest.raises(ValueError, match="already approved"):
        rules.approve(3)


def test_rejected_proposals_are_history(home, as_agent):
    from myclaw import rules

    rules.save_rule("Be brief", "Short answers.")
    as_agent("Researcher")
    rules.propose_rule_deletion("be-brief", "I like long answers")
    p = rules.reject(1, by="user", note="no")
    assert p["status"] == "rejected" and p["note"] == "no"
    assert rules.get_rule("be-brief") is not None and _trash(home) == []
    assert [x["id"] for x in rules.list_proposals("rejected")] == [1]
    with pytest.raises(ValueError):
        rules.approve(1)


def test_proposal_checks(home, as_agent, monkeypatch):
    from myclaw import rules

    rules.save_rule("Be brief", "Short answers.")
    as_agent("Researcher")
    assert "already exists" in rules.propose_rule("Be brief", "x", reason="r")
    assert "no rule 'nope'" in rules.propose_rule_change("nope", "x", "r")
    assert "same as rule" in rules.propose_rule_change("be-brief", "Short answers.", "r")
    assert rules.propose_rule_change("be-brief", "Very short answers.", "r").startswith("Proposal #1")
    assert "already waiting as proposal #1" in rules.propose_rule_change("be-brief", "Very short answers.", "r")
    monkeypatch.setattr(rules, "_max_pending", lambda: 2)
    assert rules.propose_rule("Two", "x", reason="r").startswith("Proposal #2")
    assert "at most 2" in rules.propose_rule("Three", "x", reason="r")
    as_agent("Coder")  # the limit is per agent
    assert rules.propose_rule("Three", "x", reason="r").startswith("Proposal #3")


def test_approving_a_create_when_the_rule_appeared_meanwhile(home, as_agent):
    from myclaw import rules

    as_agent("Researcher")
    rules.propose_rule("Be brief", "Short.", reason="r")
    rules.save_rule("Be brief", "The user's own text.")
    with pytest.raises(ValueError, match="exists now"):
        rules.approve(1)
    assert rules.get_rule("be-brief")["text"] == "The user's own text."
    assert rules.get_proposal(1)["status"] == "pending"


def test_no_direct_write_path_from_agent_tools(home):
    """Agents get exactly the five rule tools: read, list, propose — never write/approve."""
    from myclaw.tools import TOOLS, validate_path

    rule_tools = {name: info["func"] for name, info in TOOLS.items()
                  if getattr(info["func"], "_module", "") == "myclaw.rules"}
    assert set(rule_tools) == {"list_rules", "read_rule", "propose_rule", "propose_rule_change",
                               "propose_rule_deletion"}
    assert {f._name for f in rule_tools.values()} == {"agent_list_rules", "agent_read_rule", "propose_rule",
                                                      "propose_rule_change", "propose_rule_deletion"}
    # The file tools stay in the workspace: the rules folder is out of reach.
    for p in ("../rules/x.md", str(home / ".myclaw" / "rules" / "x.md"), "../rules/.proposals/1.json"):
        with pytest.raises(ValueError):
            validate_path(p)


def test_tools_through_the_registry(home, as_agent):
    from myclaw import rules
    from myclaw.tools import TOOLS

    as_agent("Researcher")
    out = TOOLS["propose_rule"]["func"](title="Cite sources", text="Cite them.", reason="r")
    assert out.startswith("Proposal #1") and "not active yet" in out
    assert _md_files(home) == []
    rules.approve(1)
    assert "cite-sources: Cite sources" in TOOLS["list_rules"]["func"]()
    assert "Cite them." in TOOLS["read_rule"]["func"](slug="cite-sources")
    assert "no rule" in TOOLS["read_rule"]["func"](slug="nope")


def test_new_proposal_is_announced(home, as_agent, monkeypatch):
    from myclaw import events, rules

    seen = []
    monkeypatch.setattr(events, "publish", lambda ch, ev: seen.append((ch, ev)))
    as_agent("Researcher")
    rules.propose_rule("Cite sources", "Cite them.", reason="r")
    assert seen and seen[0][0] == events.ALL and seen[0][1]["type"] == "rule_proposal_created"
    assert seen[0][1]["proposal_id"] == 1 and seen[0][1]["by"] == "Researcher"


def test_telegram_notice_uses_the_running_bot(home, monkeypatch):
    from myclaw import rules
    from myclaw.channels import telegram as tg

    sent = []

    async def fake_send(text, to=""):
        sent.append(text)
        return "Sent"

    async def main():
        from myclaw.events import CURRENT_AGENT

        bot = type("Bot", (), {})()
        bot.loop = asyncio.get_running_loop()
        monkeypatch.setattr(tg, "ACTIVE", [bot])
        monkeypatch.setattr(tg, "send_telegram_message", fake_send)
        CURRENT_AGENT.set("Researcher")  # inside this event loop's context
        # Agent tools run in a worker thread (asyncio.to_thread), like in the tool executor.
        await asyncio.to_thread(rules.propose_rule, "Cite sources", "Cite them.", "all", "better answers")
        for _ in range(50):
            if sent:
                break
            await asyncio.sleep(0.01)

    asyncio.run(main())
    assert sent and "Cite sources" in sent[0] and "#1" in sent[0] and "zensynora rules approve 1" in sent[0]


# ── the system prompt ────────────────────────────────────────────────────────
def test_prompt_section_scope_order_and_label(home, as_agent):
    from myclaw import rules

    rules.save_rule("Low one", "LOW TEXT", priority="low")
    rules.save_rule("High one", "HIGH TEXT\nsecond line", priority="high")
    rules.save_rule("Normal one", "NORMAL TEXT")
    rules.save_rule("Only coder", "CODER TEXT", scope="Coder", priority="high")
    rules.save_rule("Switched off", "OFF TEXT", enabled=False)
    as_agent("Researcher")
    rules.propose_rule("Pending", "PENDING TEXT", reason="r")

    s = rules.prompt_section("Researcher")
    assert s.startswith("## Rules you must follow") and "user's own instructions" in s
    assert s.index("HIGH TEXT") < s.index("NORMAL TEXT") < s.index("LOW TEXT")
    assert "> HIGH TEXT\n> second line" in s  # quoted
    assert "CODER TEXT" not in s and "OFF TEXT" not in s and "PENDING TEXT" not in s
    c = rules.prompt_section("coder")
    assert "CODER TEXT" in c and c.index("CODER TEXT") < c.index("NORMAL TEXT")


def test_prompt_section_cap_keeps_priority_order(home):
    from myclaw import rules

    rules.save_rule("A first", "A" * 900, priority="high")
    rules.save_rule("B second", "B" * 900)
    rules.save_rule("C third", "short", priority="low")
    s = rules.prompt_section("default", max_chars=1600)
    assert "A" * 900 in s and "B" * 900 not in s and "short" not in s  # never skips to a lower one
    assert "2 more rule(s) not shown" in s and len(s) < 1600 + 300


def test_prompt_section_is_cached_and_follows_file_edits(home, monkeypatch):
    from myclaw import rules

    rules.save_rule("Be brief", "Short answers.")
    assert "Short answers." in rules.prompt_section("default")
    calls = []
    real = rules.parse
    monkeypatch.setattr(rules, "parse", lambda *a, **k: calls.append(1) or real(*a, **k))
    for _ in range(5):
        rules.prompt_section("default")
    assert calls == []  # nothing re-read while the files are unchanged
    path = home / ".myclaw" / "rules" / "be-brief.md"
    path.write_text(path.read_text().replace("Short answers.", "Edited by hand, longer."))
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))
    assert "Edited by hand" in rules.prompt_section("default") and calls


def test_rules_switch_off_in_config(home, monkeypatch):
    from myclaw import rules

    rules.save_rule("Be brief", "Short answers.")
    monkeypatch.setattr(rules, "_enabled_in_config", lambda: False)
    assert rules.prompt_section("default") == ""


def test_every_agent_prompt_gets_the_rules_without_a_rebuild(home):
    from myclaw import rules
    from myclaw.agent import SYSTEM_PROMPT, Agent
    from myclaw.config import load_config

    assert "propose_rule_change" in SYSTEM_PROMPT and "Rules you must follow" in SYSTEM_PROMPT
    a = Agent(load_config(), name="default")
    first = asyncio.run(a._load_system_prompt())
    assert "## Rules you must follow" not in first
    rules.save_rule("Be brief", "Short answers.")
    second = asyncio.run(a._load_system_prompt())  # cached prompt, fresh rules
    assert second.startswith(first) and "> Short answers." in second
    assert "> Short answers." in a.system_prompt
    rules.update_rule("be-brief", enabled=False)
    assert "Short answers." not in asyncio.run(a._load_system_prompt())


# ── API ──────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(home):
    from myclaw.web import rules_api

    app = FastAPI()
    app.include_router(rules_api.router)
    with TestClient(app) as c:
        yield c


def test_api_crud(client, home):
    r = client.post("/api/rules", json={"title": "Be brief", "text": "Short.", "priority": "high",
                                        "scope": ["Researcher"]})
    assert r.status_code == 200 and r.json()["slug"] == "be-brief"
    assert client.post("/api/rules", json={"title": "Be brief", "text": "x"}).status_code == 400
    body = client.get("/api/rules").json()
    assert [x["slug"] for x in body["rules"]] == ["be-brief"] and body["pending"] == 0
    assert "default" in body["agents"] and body["priorities"] == ["high", "normal", "low"]
    assert client.get("/api/rules/be-brief").json()["scope"] == ["Researcher"]
    r = client.put("/api/rules/be-brief", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False and r.json()["text"] == "Short."
    assert client.put("/api/rules/be-brief", json={"text": "token = ghp_abcdefghijklmnopqrstuvwxyz0123456789"}
                      ).status_code == 400
    assert client.get("/api/rules/nope").status_code == 404
    assert client.get("/api/rules/..%2Fconfig").status_code in (400, 404)
    assert client.delete("/api/rules/be-brief").json()["ok"] is True
    assert client.get("/api/rules").json()["rules"] == [] and _trash(home)


def test_api_proposals_approve_reject(client, home, as_agent):
    from myclaw import rules

    rules.save_rule("Be brief", "Short.")
    as_agent("Researcher")
    rules.propose_rule_change("be-brief", "Very short.", "clearer")
    rules.propose_rule("Cite sources", "Cite them.", reason="quality")
    assert client.get("/api/rules").json()["pending"] == 2
    props = client.get("/api/rules/proposals?status=pending").json()["proposals"]
    assert [p["id"] for p in props] == [2, 1]
    assert "+Very short." in props[1]["diff"] and props[1]["current_exists"] is True
    assert props[0]["current_exists"] is False and "/dev/null" in props[0]["diff"]
    assert client.get("/api/rules/proposals?status=bogus").status_code == 400
    r = client.post("/api/rules/proposals/1/approve", json={})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    assert rules.get_rule("be-brief")["text"] == "Very short."
    assert client.post("/api/rules/proposals/1/approve").status_code == 409
    r = client.post("/api/rules/proposals/2/reject", json={"note": "not now"})
    assert r.json()["status"] == "rejected" and r.json()["note"] == "not now"
    assert client.post("/api/rules/proposals/99/reject").status_code == 404
    assert client.post("/api/rules/proposals/abc/approve").status_code in (404, 409)
    hist = client.get("/api/rules/proposals").json()["proposals"]
    assert {p["status"] for p in hist} == {"approved", "rejected"} and "diff" not in hist[0]


def test_api_is_guarded(home):
    from myclaw.web import rules_api

    app = FastAPI()
    app.include_router(rules_api.router)
    remote = TestClient(app, client=("192.168.1.50", 5000))
    assert remote.get("/api/rules").status_code == 403
    assert remote.post("/api/rules", json={"title": "x", "text": "y"}).status_code == 403
    assert remote.post("/api/rules/proposals/1/approve").status_code == 403
    assert remote.delete("/api/rules/x").status_code == 403
    assert not (home / ".myclaw" / "rules").exists()


def test_api_router_is_in_the_app(home):
    from myclaw.web.api import app

    c = TestClient(app)  # no lifespan: only the route is needed
    assert c.get("/api/rules").json()["rules"] == []
    assert c.get("/api/rules/proposals").json() == {"proposals": []}


# ── CLI ──────────────────────────────────────────────────────────────────────
def test_cli(home, as_agent):
    from click.testing import CliRunner

    from myclaw import rules
    from myclaw.cli import cli

    run = CliRunner()
    assert "No rules yet" in run.invoke(cli, ["rules", "list"]).output
    rules.save_rule("Be brief", "Short.", priority="high")
    as_agent("Researcher")
    rules.propose_rule_change("be-brief", "Very short.", "clearer")
    rules.propose_rule("Cite sources", "Cite them.", reason="quality")
    out = run.invoke(cli, ["rules", "list"]).output
    assert "be-brief: Be brief" in out and "high" in out and "2 proposal(s) waiting" in out
    assert "Short." in run.invoke(cli, ["rules", "show", "be-brief"]).output
    assert run.invoke(cli, ["rules", "show", "nope"]).exit_code != 0
    out = run.invoke(cli, ["rules", "proposals"]).output
    assert "#1 [pending] modify be-brief" in out and "+Very short." in out and "why: clearer" in out
    res = run.invoke(cli, ["rules", "approve", "1"])
    assert res.exit_code == 0 and "Approved #1" in res.output
    assert rules.get_rule("be-brief")["text"] == "Very short."
    assert run.invoke(cli, ["rules", "approve", "1"]).exit_code != 0
    res = run.invoke(cli, ["rules", "reject", "2", "--note", "later"])
    assert res.exit_code == 0 and rules.get_proposal(2)["status"] == "rejected"
    assert run.invoke(cli, ["rules", "reject", "42"]).exit_code != 0
    out = run.invoke(cli, ["rules", "proposals", "--status", "all"]).output
    assert "approved by user (cli)" in out and "rejected by user (cli)" in out and ": later" in out


# ── doctor ───────────────────────────────────────────────────────────────────
def _doctor():
    from myclaw.doctor import run_checks

    return {r.id: r for r in run_checks(offline=True, only=["rules"]).results}["rules"]


def test_doctor_check(home, as_agent):
    from myclaw import rules

    r = _doctor()
    assert r.status == "ok" and r.detail == "0 active, 0 proposal(s) waiting"
    assert not (home / ".myclaw" / "rules").exists()  # the check creates nothing
    rules.save_rule("Be brief", "Short.")
    rules.save_rule("Off", "x", enabled=False)
    as_agent("Researcher")
    rules.propose_rule("Cite sources", "Cite them.", reason="r")
    r = _doctor()
    assert r.status == "ok" and r.detail == "1 active (1 switched off), 1 proposal(s) waiting"
    path = home / ".myclaw" / "rules" / ".proposals" / "1.json"
    p = json.loads(path.read_text())
    p["created"] = "2020-01-01T00:00:00"
    path.write_text(json.dumps(p))
    r = _doctor()
    assert r.status == "warn" and "#1" in r.detail and "Rules" in r.hint
