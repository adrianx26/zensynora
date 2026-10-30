"""2026-10-31: create / edit / delete / migrate your tools and skills; export and import as a file.

Real files in a temporary HOME; tools run in the real tool sandbox.
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import myclaw.tools  # noqa: F401  (registers the tools package)

core = sys.modules["myclaw.tools.core"]

HELLO = 'def hello_user(who: str = "world"):\n    """Say hello to someone."""\n    return f"Hello {who}!"\n'
STATIC = '''import logging
logger = logging.getLogger(__name__)

def coordination_notes():
    """Coordination workflow for two agents."""
    try:
        return {"description": "Design with one agent, review with another",
                "steps": ["draft the spec", "review with fresh context"]}
    except Exception as e:
        logger.error(f"failed: {e}")
        return {}
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    import myclaw.config as cfg_mod
    from myclaw.tools import toolbox

    monkeypatch.setenv("HOME", str(tmp_path))
    data = tmp_path / ".myclaw"
    (data / "workspace").mkdir(parents=True)
    (data / "config.json").write_text('{"providers": {}}')
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", data / "config.json")
    cfg_mod._cached_config = None
    monkeypatch.setattr(core, "WORKSPACE", data / "workspace")
    tb = data / "TOOLBOX"
    tb.mkdir()
    monkeypatch.setattr(toolbox, "TOOLBOX_DIR", tb)
    monkeypatch.setattr(toolbox, "TOOLBOX_REG", tb / "toolbox_registry.json")
    monkeypatch.setattr(toolbox, "TOOLBOX_DOCS", tb / "README.md")
    monkeypatch.setattr(toolbox, "SKIPPED_TOOLS", {})
    tools_before = dict(core.TOOLS)
    schemas_before = list(core.TOOL_SCHEMAS)
    yield tmp_path
    core.TOOLS.clear()
    core.TOOLS.update(tools_before)
    core.TOOL_SCHEMAS[:] = schemas_before
    cfg_mod._cached_config = None


def _tm():
    from myclaw.tools import toolbox_manage

    return toolbox_manage


# ── tools: create / edit / delete ───────────────────────────────────────────
def test_create_edit_delete_tool_keeps_previous_versions(home):
    tm = _tm()
    t = tm.save_tool("hello_user", HELLO, "Says hello", create=True)
    assert t["version"] == "1.0.0" and t["author"] == "user" and t["loaded"] and t["problem"] is None
    assert core.TOOLS["hello_user"]["func"](who="Ana") == "Hello Ana!"
    assert any(s["function"]["name"] == "hello_user" for s in core.TOOL_SCHEMAS)

    t = tm.save_tool("hello_user", HELLO.replace("Hello", "Hi"))
    assert t["version"] == "1.0.1" and len(t["kept"]) == 1
    assert core.TOOLS["hello_user"]["func"](who="Ana") == "Hi Ana!"
    trash = home / ".myclaw" / "TOOLBOX" / ".trash"
    assert "Hello {who}" in (trash / t["kept"][0]).read_text()

    out = tm.delete_tool("hello_user")
    assert "hello_user" not in core.TOOLS and "hello_user" not in tm.read_registry()
    assert not (home / ".myclaw" / "TOOLBOX" / "hello_user.py").exists()
    assert {p.name for p in trash.iterdir()} >= set(out["trash"])
    assert any("registry" in n for n in out["trash"])  # the entry is kept too


@pytest.mark.parametrize("name,code,msg", [
    ("bad name", HELLO, "Python name"),
    ("hello_user", "def hello_user(:\n", "Syntax error"),
    ("other_name", HELLO, "must define a function named 'other_name'"),
    ("shell", "def shell():\n    return 1\n", "built-in"),
    ("hello_user", "", "Write the tool"),
])
def test_save_tool_refuses_with_a_reason(home, name, code, msg):
    with pytest.raises(_tm().ToolboxError, match=msg):
        _tm().save_tool(name, code, create=True)


def test_create_twice_and_edit_unknown_are_refused(home):
    tm = _tm()
    tm.save_tool("hello_user", HELLO, create=True)
    with pytest.raises(tm.ToolboxError, match="already exists"):
        tm.save_tool("hello_user", HELLO, create=True)
    with pytest.raises(tm.ToolboxError, match="No tool"):
        tm.save_tool("nope_tool", HELLO.replace("hello_user", "nope_tool"))
    with pytest.raises(tm.ToolboxError, match="built-in"):
        tm.delete_tool("shell")


def test_delete_never_moves_files_outside_the_toolbox(home):
    tm = _tm()
    outside = home / "important.py"
    outside.write_text(HELLO)
    tm.save_tool("hello_user", HELLO, create=True)
    reg = tm.read_registry()
    reg["hello_user"]["path"] = str(outside)  # e.g. an agent edited the registry
    tm._write_registry(reg)
    tm.delete_tool("hello_user")
    assert outside.read_text() == HELLO


def test_enable_disable(home):
    tm = _tm()
    tm.save_tool("hello_user", HELLO, create=True)
    assert tm.set_enabled("hello_user", False)["loaded"] is False
    assert "hello_user" not in core.TOOLS
    assert tm.set_enabled("hello_user", True)["loaded"] is True


def test_user_edit_of_an_agent_tool_still_runs_sandboxed(home):
    tm = _tm()
    tm.save_tool("hello_user", HELLO, create=True)
    func = core.TOOLS["hello_user"]["func"]
    assert getattr(func, "sandboxed", False)


# ── migrate ─────────────────────────────────────────────────────────────────
def test_instructions_only_tool_becomes_a_skill(home):
    tm = _tm()
    from myclaw import agent_skills

    tm.save_tool("coordination_notes", STATIC, create=True, author="agent")
    row = next(t for t in tm.list_tools()["tools"] if t["name"] == "coordination_notes")
    assert row["instructions_only"] is True
    assert tm.save_tool("hello_user", HELLO, create=True)["instructions_only"] is False  # takes an argument

    out = tm.tool_to_skill("coordination_notes")
    sk = agent_skills.get_skill("coordination-notes")
    assert sk and sk["source"] == "yours"
    assert "review with fresh context" in sk["body"] and "\n" not in sk["description"]
    assert out["tool_disabled"] and "coordination_notes" not in core.TOOLS
    assert (home / ".myclaw" / "TOOLBOX" / "coordination_notes.py").exists()  # kept, only disabled
    with pytest.raises(tm.ToolboxError, match="already have a skill"):
        tm.tool_to_skill("coordination_notes")


def test_static_detection_never_runs_code(home):
    tm = _tm()
    marker = home / "ran.txt"
    code = (f'def boom():\n    """x"""\n    open({str(marker)!r}, "w").write("ran")\n    return {{"a": 1}}\n')
    assert tm._static_value(code, "boom") == (False, None)
    assert not marker.exists()


def test_repair_fixes_registry_and_old_formats(home):
    tm = _tm()
    tb = home / ".myclaw" / "TOOLBOX"
    tm.save_tool("hello_user", HELLO, create=True)
    reg = tm.read_registry()
    reg["hello_user"]["path"] = "/home/someone-else/.myclaw/TOOLBOX/hello_user.py"   # another computer
    reg["gone_tool"] = {"path": str(tb / "gone_tool.py"), "enabled": False}            # file deleted
    tm._write_registry(reg)
    raw = json.loads((tb / "toolbox_registry.json").read_text())
    raw["old_style"] = str(tb / "old_style.py")                                         # old format
    (tb / "toolbox_registry.json").write_text(json.dumps(raw))
    (tb / "old_style.py").write_text(HELLO.replace("hello_user", "old_style"))
    (tb / "stray_tool.py").write_text(HELLO.replace("hello_user", "stray_tool"))       # not registered
    (tb / "lost_README.md").write_text("# lost")                                       # README only
    sd = home / ".myclaw" / "skills"
    sd.mkdir()
    (sd / "flat-skill.md").write_text("---\nname: flat-skill\ndescription: d\n---\nDo it.\n")

    plan = tm.repair(dry_run=True)["actions"]
    kinds = sorted(a["kind"] for a in plan)
    assert kinds == ["missing_file", "old_format", "old_skill_file", "orphan_doc", "path", "unregistered"]
    assert tm.read_registry()["hello_user"]["path"].startswith("/home/someone-else")  # dry run changed nothing

    tm.repair()
    reg = tm.read_registry()
    assert reg["hello_user"]["path"] == str(tb / "hello_user.py")
    assert "gone_tool" not in reg and reg["old_style"]["author"] == "agent"
    assert reg["stray_tool"]["enabled"] is False
    assert not (tb / "lost_README.md").exists() and any((tb / ".trash").glob("lost_README*"))
    assert (sd / "flat-skill" / "SKILL.md").exists() and not (sd / "flat-skill.md").exists()
    assert tm.repair(dry_run=True)["actions"] == []  # idempotent


# ── skills: nothing is erased ───────────────────────────────────────────────
def test_skill_overwrite_and_delete_go_to_trash(home):
    from myclaw import agent_skills

    agent_skills.save_skill("my-skill", "d", "First version.")
    agent_skills.save_skill("my-skill", "d", "Second version.")
    trash = agent_skills.trash_dir()
    assert any("First version." in p.read_text() for p in trash.glob("*.md"))
    assert agent_skills.delete_skill("my-skill")
    assert agent_skills.get_skill("my-skill") is None
    kept = [p for p in trash.iterdir() if p.is_dir()]
    assert kept and "Second version." in (kept[0] / "SKILL.md").read_text()
    assert all(s["name"] != "my-skill" for s in agent_skills.list_skills())  # trash is not listed


# ── export / import ─────────────────────────────────────────────────────────
def test_bundle_round_trip(home, tmp_path, monkeypatch):
    tm = _tm()
    from myclaw import agent_skills

    tm.save_tool("hello_user", HELLO, "Says hello", documentation="# hello_user\n\nGreets.\n", create=True,
                 tags=["demo"])
    agent_skills.save_skill("weather-report", "Report the weather", "1. Look it up.", always=True)
    bundle = tm.export_bundle()
    text = json.dumps(bundle)

    # a second computer: an empty home
    other = tmp_path / "other"
    (other / ".myclaw" / "TOOLBOX").mkdir(parents=True)
    from myclaw.tools import toolbox

    monkeypatch.setenv("HOME", str(other))
    monkeypatch.setattr(toolbox, "TOOLBOX_DIR", other / ".myclaw" / "TOOLBOX")
    monkeypatch.setattr(toolbox, "TOOLBOX_REG", other / ".myclaw" / "TOOLBOX" / "toolbox_registry.json")
    monkeypatch.setattr(toolbox, "TOOLBOX_DOCS", other / ".myclaw" / "TOOLBOX" / "README.md")
    core.TOOLS.pop("hello_user", None)

    out = tm.import_file(text, "export.json")
    assert {(i["kind"], i["name"]) for i in out["imported"]} == {("tool", "hello_user"), ("skill", "weather-report")}
    t = tm.get_tool("hello_user")
    assert t["code"] == HELLO and t["documentation"].startswith("# hello_user") and t["tags"] == ["demo"]
    assert t["author"] == "imported" and t["enabled"] and core.TOOLS["hello_user"]["func"]() == "Hello world!"
    sk = agent_skills.get_skill("weather-report")
    assert sk["body"] == "1. Look it up." and sk["always"] is True and sk["description"] == "Report the weather"

    again = tm.import_file(text, "export.json")
    assert not again["imported"] and len(again["skipped"]) == 2
    assert "already exists" in again["skipped"][0]["reason"]
    replaced = tm.import_file(text, "export.json", overwrite=True)
    assert all(i["replaced"] for i in replaced["imported"])


def test_imported_code_that_fails_the_check_comes_in_disabled(home):
    tm = _tm()
    code = 'import subprocess\n\ndef run_it():\n    """x"""\n    return subprocess.run(["id"])\n'
    out = tm.import_file(code, "run_it.py")
    item = out["imported"][0]
    assert item["enabled"] is False and "subprocess" in item["problem"]
    assert "run_it" not in core.TOOLS
    trusted = tm.import_file(code, "run_it.py", overwrite=True, trust=True)["imported"][0]
    assert trusted["problem"] in (None, "")  # marked yours: not re-checked, still sandboxed
    assert tm.get_tool("run_it")["author"] == "user"


def test_import_single_files_and_bad_files(home):
    tm = _tm()
    from myclaw import agent_skills

    out = tm.import_file(HELLO, "hello_user.py")
    assert out["imported"][0]["name"] == "hello_user"
    out = tm.import_file("---\nname: note-taking\ndescription: Take notes\n---\nWrite it down.\n", "SKILL.md")
    assert out["imported"][0] == {"kind": "skill", "name": "note-taking", "replaced": False}
    assert agent_skills.get_skill("note-taking")["body"] == "Write it down."
    for text, fname, msg in [
        ('{"format": "other"}', "x.json", "not a ZenSynora export"),
        ('{"format": "zensynora-bundle", "version": 99}', "x.json", "newer ZenSynora"),
        ("x = 1\n", "x.py", "no function"),
        ('{"format": "zensynora-bundle", "version": 1, "tools": [], "skills": []}', "x.json", "no tools or skills"),
        ("{not json", "x.json", "not valid JSON"),
    ]:
        with pytest.raises(tm.ToolboxError, match=msg):
            tm.import_file(text, fname)


def test_bundle_with_one_bad_item_imports_the_rest(home):
    tm = _tm()
    bundle = {"format": "zensynora-bundle", "version": 1,
              "tools": [{"name": "hello_user", "code": HELLO}, {"name": "broken", "code": "def broken(:"}],
              "skills": [{"name": "", "body": "x"}]}
    out = tm.import_file(json.dumps(bundle), "b.json")
    assert [i["name"] for i in out["imported"]] == ["hello_user"]
    assert {s["name"] for s in out["skipped"]} == {"broken", "?"}


# ── API ─────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(home):
    from myclaw.web import toolbox_api

    app = FastAPI()
    app.include_router(toolbox_api.router)
    with TestClient(app) as c:
        yield c


def test_api_crud_export_import(client, home):
    r = client.post("/api/toolbox", json={"name": "hello_user", "code": HELLO, "create": True})
    assert r.status_code == 200, r.text
    assert client.get("/api/toolbox").json()["tools"][0]["name"] == "hello_user"
    assert "Hello {who}" in client.get("/api/toolbox/hello_user").json()["code"]
    assert client.post("/api/toolbox", json={"name": "hello_user", "code": HELLO, "create": True}).status_code == 400
    assert client.get("/api/toolbox/nope").status_code == 404
    assert client.put("/api/toolbox/hello_user/enabled", json={"enabled": False}).json()["enabled"] is False

    r = client.get("/api/transfer/export", params={"tools": "hello_user", "skills": ""})
    assert r.status_code == 200 and "zensynora-tool-hello_user.json" in r.headers["content-disposition"]
    bundle = r.json()
    assert [t["name"] for t in bundle["tools"]] == ["hello_user"] and bundle["skills"] == []

    assert client.delete("/api/toolbox/hello_user").status_code == 200
    r = client.post("/api/transfer/import", json={"text": json.dumps(bundle), "filename": "b.json"})
    assert r.json()["imported"][0]["name"] == "hello_user"

    from myclaw import agent_skills

    agent_skills.save_skill("my-skill", "Do things", "Steps.")
    r = client.get("/api/skills/my-skill/export")
    assert r.status_code == 200 and r.text.startswith("---\nname: my-skill")
    assert "my-skill.SKILL.md" in r.headers["content-disposition"]


def test_api_changes_need_the_web_ui(home):
    from myclaw.web import toolbox_api

    app = FastAPI()
    app.include_router(toolbox_api.router)
    plain = TestClient(app, headers={})  # like curl from an agent's shell
    for method, url, body in [
        ("post", "/api/toolbox", {"name": "hello_user", "code": HELLO, "create": True}),
        ("post", "/api/transfer/import", {"text": HELLO, "filename": "hello_user.py"}),
        ("post", "/api/toolbox/repair", {}),
        ("delete", "/api/toolbox/hello_user", None),
    ]:
        r = getattr(plain, method)(url, json=body) if body is not None else getattr(plain, method)(url)
        assert r.status_code == 403, (url, r.status_code)
    assert not (home / ".myclaw" / "TOOLBOX" / "hello_user.py").exists()
