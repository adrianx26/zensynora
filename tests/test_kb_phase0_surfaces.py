"""KB phase 0 (2026-10-23): MCP server types, CLI --base, Web UI API, knowledge_dir."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Dict, List, Optional

import pytest


# ── MCP server: typed schema + argument coercion ─────────────────────────────


def _sample_tool(query: str, limit: int = 5, ratio: float = 0.5, exact: bool = False,
                 tags: Optional[List[str]] = None, opts: Dict[str, int] | None = None,
                 user_id: str = "default", **kwargs) -> str:
    return json.dumps({"query": query, "limit": limit, "ratio": ratio, "exact": exact,
                       "tags": tags, "opts": opts, "user_id": user_id,
                       "sliced": "abcdefgh"[:limit]})


def test_mcp_schema_uses_annotations():
    from myclaw.mcp.server import build_input_schema

    schema = build_input_schema(_sample_tool)  # string annotations (__future__)
    props = schema["properties"]
    assert {k: v["type"] for k, v in props.items()} == {
        "query": "string", "limit": "integer", "ratio": "number", "exact": "boolean",
        "tags": "array", "opts": "object", "user_id": "string"}
    assert props["limit"]["default"] == 5 and props["exact"]["default"] is False
    assert "default" not in props["tags"]  # None defaults are not advertised
    assert schema["required"] == ["query"]
    assert "kwargs" not in props


def test_mcp_user_id_hidden_only_for_knowledge_tools():
    """Regression: non-knowledge tools (schedule, swarm_*, user profile) keep user_id."""
    import myclaw.tools as m_tools
    from myclaw.mcp.server import coerce_arguments, list_tool_definitions

    tools = {t.name: t.inputSchema for t in list_tool_definitions()}
    for name in ("schedule", "nlp_schedule", "swarm_list", "swarm_stats", "swarm_assign",
                 "get_user_profile", "update_user_profile", "generate_session_insights"):
        assert tools[name]["properties"]["user_id"] == {"type": "string", "default": "default"}, name
        func = m_tools.TOOLS[name]["func"]
        assert coerce_arguments(func, {"user_id": "alice"}) == {"user_id": "alice"}, name
    for name in ("write_to_knowledge", "search_knowledge", "read_knowledge", "list_knowledge",
                 "get_knowledge_context", "get_related_knowledge", "sync_knowledge_base",
                 "list_knowledge_tags"):
        assert "user_id" not in tools[name]["properties"], name
        func = m_tools.TOOLS[name]["func"]
        assert "user_id" not in coerce_arguments(func, {"user_id": "alice"}), name


def test_mcp_lists_real_knowledge_tools_with_types():
    from myclaw.mcp.server import list_tool_definitions

    tools = {t.name: t.inputSchema for t in list_tool_definitions()}
    s = tools["search_knowledge"]
    assert s["properties"]["limit"] == {"type": "integer", "default": 5}
    assert "user_id" not in s["properties"] and s["required"] == ["query"]
    assert "user_id" not in tools["list_knowledge"]["properties"]


def test_mcp_coerces_string_arguments(monkeypatch):
    import myclaw.tools as m_tools
    from myclaw.mcp.server import call_registered_tool, coerce_arguments

    args = coerce_arguments(_sample_tool, {
        "query": 42, "limit": "3", "ratio": "2.5", "exact": "true", "tags": "a, b",
        "opts": '{"x": 1}', "user_id": "someone-else", "extra": "kept"})
    assert args == {"query": "42", "limit": 3, "ratio": 2.5, "exact": True, "tags": ["a", "b"],
                    "opts": {"x": 1}, "user_id": "someone-else", "extra": "kept"}
    assert coerce_arguments(_sample_tool, {"exact": "0", "tags": '["x"]', "limit": 4.0}) == {
        "exact": False, "tags": ["x"], "limit": 4}

    monkeypatch.setitem(m_tools.TOOLS, "_phase0_sample", {"func": _sample_tool, "desc": "t"})
    out = json.loads(asyncio.run(call_registered_tool("_phase0_sample",
                                                      {"query": "q", "limit": "2"}))[0].text)
    assert out["limit"] == 2 and out["sliced"] == "ab" and out["user_id"] == "default"
    out = json.loads(asyncio.run(call_registered_tool(
        "_phase0_sample", {"query": "q", "user_id": "alice"}))[0].text)
    assert out["user_id"] == "alice"  # a non-knowledge tool receives user_id

    bad = asyncio.run(call_registered_tool("_phase0_sample", {"query": "q", "limit": "lots"}))[0].text
    assert "limit" in bad and "whole number" in bad
    missing = asyncio.run(call_registered_tool("_phase0_sample", {"limit": "2"}))[0].text
    assert "query" in missing and "Invalid arguments" in missing


def test_mcp_server_logs_go_to_stderr():
    import logging
    import sys

    from myclaw.mcp.server import _logs_to_stderr

    h = logging.StreamHandler(sys.stdout)
    lg = logging.getLogger("phase0.mcp.test")
    lg.addHandler(h)
    try:
        _logs_to_stderr()
        assert h.stream is sys.stderr
    finally:
        lg.removeHandler(h)


def test_mcp_search_knowledge_with_string_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from myclaw.knowledge.storage import write_note
    from myclaw.mcp.server import call_registered_tool

    write_note("Zebra Facts", title="Zebra Facts", content="Zebras have stripes.")
    text = asyncio.run(call_registered_tool("search_knowledge", {"query": "zebras", "limit": "5"}))[0].text
    assert "Execution Error" not in text and "Invalid" not in text
    assert "Zebra" in text


# ── CLI: --base, read shows the text, sync --force, boolean search ───────────


@pytest.fixture
def cli_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from click.testing import CliRunner

    from myclaw.cli import cli

    return CliRunner(), cli


def test_cli_base_option_and_read_shows_text(cli_home):
    runner, cli = cli_home
    from myclaw.knowledge.parser import Observation
    from myclaw.knowledge.storage import write_note

    p = write_note("Alpha Plan", title="Alpha Plan", content="The alpha plan text lives here.",
                   observations=[Observation(category="fact", content="Alpha starts Monday")],
                   tags=["plan"], user_id="team")
    write_note("Beta Plan", title="Beta Plan", content="Beta is something else.", user_id="team")

    r = runner.invoke(cli, ["knowledge", "read", p, "--base", "team"])
    assert r.exit_code == 0, r.output
    assert "The alpha plan text lives here." in r.output
    assert "[fact] Alpha starts Monday" in r.output and "#plan" in r.output

    assert "Note not found" in runner.invoke(cli, ["knowledge", "read", p]).output

    r = runner.invoke(cli, ["knowledge", "list", "-b", "team"])
    assert "Alpha Plan" in r.output and "Beta Plan" in r.output
    assert "Knowledge base is empty." in runner.invoke(cli, ["knowledge", "list"]).output

    r = runner.invoke(cli, ["knowledge", "sync", "--force", "-b", "team"])
    assert r.exit_code == 0 and "Sync complete" in r.output, r.output

    r = runner.invoke(cli, ["knowledge", "search", "alpha OR beta", "-b", "team"])
    assert r.exit_code == 0, r.output
    assert "alpha-plan" in r.output and "beta-plan" in r.output

    r = runner.invoke(cli, ["knowledge", "tags", "-b", "team"])
    assert "#plan" in r.output

    r = runner.invoke(cli, ["knowledge", "write", "-b", "team"],
                      input="Gamma\nGamma body\n\nx, y\n")
    assert r.exit_code == 0 and "Created" in r.output, r.output
    r = runner.invoke(cli, ["knowledge", "read", "gamma", "-b", "team"])
    assert "Gamma body" in r.output


def test_cli_rejects_path_as_base(cli_home):
    runner, cli = cli_home
    r = runner.invoke(cli, ["knowledge", "list", "--base", "../etc"])
    assert r.exit_code != 0 and "not a knowledge base name" in r.output


# ── Web UI API ───────────────────────────────────────────────────────────────

K8S = """---
title: "Kubernetes"
permalink: kubernetes
aliases: ["k8s"]
type: tool
---

# Kubernetes

Container orchestration.
"""

DEPLOY = """---
title: "Deploy"
permalink: deploy
---

# Deploy

How we deploy with helm.

- runs_on [[K8s]]
"""


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import knowledge_api

    kdir = tmp_path / ".myclaw" / "knowledge" / "default"
    kdir.mkdir(parents=True)
    (kdir / "kubernetes.md").write_text(K8S)
    (kdir / "deploy.md").write_text(DEPLOY)
    app = FastAPI()
    app.include_router(knowledge_api.router)
    return TestClient(app), kdir


def test_resolver_order_and_aliases():
    from myclaw.web.knowledge_api import _Resolver

    def n(permalink, name, title, aliases=()):
        return SimpleNamespace(permalink=permalink, name=name, title=title,
                               aliases=list(aliases), file_path=None)

    r = _Resolver([n("a-note", "Shared", "Alpha"), n("shared", "Other", "Beta", ["alpha", "Gizmo"])])
    assert r("shared") == ("shared", True)      # permalink beats another note's name
    assert r("ALPHA") == ("a-note", True)       # title beats another note's alias
    assert r("gizmo") == ("shared", True)       # alias, case-insensitive
    assert r("Giz") == ("missing:giz", False)   # no fuzzy matching
    # Notes without the aliases attribute (old parser) still work.
    assert _Resolver([SimpleNamespace(permalink="x", name="X", title="X", file_path=None)])("x")[1]


def test_alias_link_resolves_in_graph_and_note_has_aliases(web):
    c, _ = web
    g = c.get("/api/knowledge/graph").json()
    assert {"source": "deploy", "target": "kubernetes", "type": "runs_on"} in g["edges"]
    assert not any(n["missing"] for n in g["nodes"])

    d = c.get("/api/knowledge/note/kubernetes").json()
    assert d["aliases"] == ["k8s"]
    assert isinstance(d["metadata"], dict)
    assert d["backlinks"] == [{"type": "runs_on", "source": "deploy", "label": "Deploy"}]
    # Existing fields are still there (the React UI uses them).
    assert {"permalink", "title", "tags", "content", "body", "observations", "links",
            "backlinks", "file", "size", "modified"} <= set(d)
    assert c.get("/api/knowledge/note/deploy").json()["aliases"] == []


def test_search_supports_or(web):
    c, _ = web
    assert c.post("/api/knowledge/sync").json()["ok"] is True
    res = c.get("/api/knowledge/search", params={"q": "orchestration OR helm"}).json()["results"]
    assert set(res) == {"kubernetes", "deploy"}
    res = c.get("/api/knowledge/search", params={"q": "orchestration AND helm"}).json()["results"]
    assert res == []


def test_trash_is_not_listed(web):
    c, kdir = web
    (kdir.parent / ".trash").mkdir()  # a stray hidden folder at the root is no base either
    r = c.delete("/api/knowledge/note/deploy")
    assert r.status_code == 200
    assert (kdir / ".trash").is_dir() and len(list((kdir / ".trash").glob("deploy-*.md"))) == 1
    bases = c.get("/api/knowledge/bases").json()["bases"]
    assert bases[0] == {"name": "default", "notes": 1}
    assert not any(b["name"].startswith(".") for b in bases)
    ids = {n["id"] for n in c.get("/api/knowledge/graph").json()["nodes"]}
    assert ids == {"kubernetes"}
    assert c.get("/api/knowledge/note/deploy").status_code == 404


def test_web_root_follows_knowledge_root(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from myclaw.knowledge.storage import set_knowledge_root
    from myclaw.web.knowledge_api import _root

    try:
        set_knowledge_root(tmp_path / "elsewhere")
        assert _root() == tmp_path / "elsewhere"
    finally:
        set_knowledge_root(None)
    assert _root() == tmp_path / ".myclaw" / "knowledge"


# ── knowledge.knowledge_dir from the config ──────────────────────────────────


@pytest.fixture
def cfg_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    import myclaw.config as cfg_mod
    from myclaw.knowledge.storage import set_knowledge_root

    monkeypatch.setattr(cfg_mod, "CONFIG_DIR", tmp_path / ".myclaw")
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", tmp_path / ".myclaw" / "config.json")
    monkeypatch.setattr(cfg_mod, "WORKSPACE", tmp_path / ".myclaw" / "workspace")
    monkeypatch.setattr(cfg_mod, "_cached_config", None)
    monkeypatch.setattr(cfg_mod, "_start_config_watcher", lambda: None)

    def write(knowledge_dir):
        (tmp_path / ".myclaw").mkdir(exist_ok=True)
        cfg_mod.CONFIG_FILE.write_text(json.dumps({"knowledge": {"knowledge_dir": knowledge_dir}}))
        return cfg_mod.load_config(force_reload=True)

    try:
        yield SimpleNamespace(tmp=tmp_path, write=write, cfg=cfg_mod)
    finally:
        set_knowledge_root(None)


def test_config_knowledge_dir_is_used(cfg_env):
    from myclaw.knowledge.storage import knowledge_root, read_note, write_note

    custom = cfg_env.tmp / "my-notes"
    cfg_env.write(str(custom))
    assert knowledge_root() == custom
    p = write_note("Custom Place", title="Custom Place", content="Stored elsewhere.")
    assert (custom / "default" / f"{p}.md").is_file()
    assert not (cfg_env.tmp / ".myclaw" / "knowledge" / "default" / f"{p}.md").exists()
    assert read_note(p) is not None

    cfg_env.write("~/.myclaw/knowledge")  # the default resets to HOME-based
    import myclaw.knowledge.storage as storage

    assert storage._KNOWLEDGE_ROOT is None
    assert knowledge_root() == cfg_env.tmp / ".myclaw" / "knowledge"


def test_config_knowledge_dir_never_breaks_loading(cfg_env, monkeypatch):
    import myclaw.knowledge.storage as storage

    def boom(_):
        raise RuntimeError("nope")

    monkeypatch.setattr(storage, "set_knowledge_root", boom)
    cfg = cfg_env.write(str(cfg_env.tmp / "x"))
    assert cfg.knowledge.knowledge_dir.endswith("x")


def test_mcp_server_console_logs_go_to_stderr(monkeypatch):
    """2026-10-23 (KB phase 0): stdout is the MCP protocol channel."""
    import logging
    import sys

    from myclaw.logging_config import configure_logging

    monkeypatch.setattr(sys, "argv", ["zensynora", "mcp-server"])
    try:
        configure_logging(level=logging.INFO, log_file=None)
        streams = [h.stream for h in logging.getLogger("myclaw").handlers
                   if isinstance(h, logging.StreamHandler)]
        assert sys.stderr in streams and sys.stdout not in streams
    finally:
        monkeypatch.setattr(sys, "argv", ["pytest"])
        configure_logging(level=logging.INFO, log_file=None)
