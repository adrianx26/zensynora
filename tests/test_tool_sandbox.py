"""2026-10-28 (rules re-review): agent-made tools run in a separate, locked-down process.

Real child processes in a temporary HOME: normal tools keep working (text, JSON,
async, logging, tempfile, printing), and code that slipped past the source check
still cannot write outside the workspace, read ~/.myclaw, start programs, use
ctypes, signal ZenSynora or connect to this computer / the local network.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

import myclaw.tools  # noqa: F401  (registers the tools package)

core = sys.modules["myclaw.tools.core"]


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    import myclaw.config as cfg_mod
    from myclaw.tools import toolbox

    monkeypatch.setenv("HOME", str(tmp_path))
    data = tmp_path / ".myclaw"
    work = data / "workspace"
    work.mkdir(parents=True)
    (data / "rules").mkdir()
    (data / "config.json").write_text('{"providers": {}}')
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", data / "config.json")
    cfg_mod._cached_config = None
    monkeypatch.setattr(core, "WORKSPACE", work)
    tb = data / "TOOLBOX"
    tb.mkdir()
    monkeypatch.setattr(toolbox, "TOOLBOX_DIR", tb)
    monkeypatch.setattr(toolbox, "TOOLBOX_REG", tb / "toolbox_registry.json")
    monkeypatch.setattr(toolbox, "TOOLBOX_DOCS", tb / "README.md")
    monkeypatch.setattr(toolbox, "SKIPPED_TOOLS", {})
    yield tmp_path
    cfg_mod._cached_config = None


def run(code, name="t", **args):
    from myclaw.tools import tool_sandbox

    return tool_sandbox.run(name, code, args)


@pytest.mark.parametrize("code, expected", [
    ("def t(s: str = 'ab'):\n    return s.upper()\n", "AB"),
    ("def t():\n    return {'a': [1, 2]}\n", {"a": [1, 2]}),
    ("import asyncio\nasync def t():\n    await asyncio.sleep(0)\n    return 'async ok'\n", "async ok"),
    ("import logging\nlogger = logging.getLogger(__name__)\ndef t():\n    logger.error('x')\n    return 'logged'\n",
     "logged"),
    ("def t():\n    print('printed')\n", "printed\n"),
    ("def t():\n    return object()\n", None),  # not JSON → text
    ("import json, re, datetime, math, csv, io\ndef t():\n    b = io.StringIO()\n    csv.writer(b).writerow([1, 2])\n"
     "    return b.getvalue().strip()\n", "1,2"),
])
def test_normal_tools_work(sandbox, code, expected):
    out = run(code)
    if expected is None:
        assert isinstance(out, str) and out.startswith("<object object")
    else:
        assert out == expected


def test_tempfile_and_home_are_inside_the_workspace(sandbox):
    code = ("import tempfile, os\ndef t():\n    with tempfile.NamedTemporaryFile('w', delete=False) as f:\n"
            "        f.write('x')\n    return [f.name, os.path.expanduser('~')]\n")
    name, home = run(code)
    work = sandbox / ".myclaw" / "workspace"
    assert Path(name).is_relative_to(work) and Path(home) == work / ".tool-home"


def test_arguments_and_signature(sandbox):
    from myclaw.tools.tool_sandbox import SandboxedTool

    code = ("def add(a: int, b: int = 2, label: str = 'sum', flag: bool = False):\n    \"\"\"Add.\"\"\"\n"
            "    return f'{label}={a + b} {flag}'\n")
    tool = SandboxedTool("add", code)
    import inspect

    sig = inspect.signature(tool)
    assert [p.annotation for p in sig.parameters.values()] == [int, int, str, bool]
    assert tool.__doc__ == "Add."
    assert tool(1) == "sum=3 False" and tool(a=1, b=5, label="s") == "s=6 False"
    assert tool(1, 2, 3, 4, 5).startswith("Error:")  # too many arguments: an actionable message


ESCAPES = {
    "write rules": "def t():\n    open({rules!r} + '/x.md', 'w').write('x')\n",
    "read config": "def t():\n    return open({config!r}).read()\n",
    "list data": "def t():\n    import os\n    return os.listdir({data!r})\n",
    "os.system": "def t():\n    import os\n    return os.system('id')\n",
    "subprocess": "def t():\n    import subprocess\n    return subprocess.run(['id']).returncode\n",
    "asyncio subprocess": "import asyncio\nasync def t():\n    p = await asyncio.create_subprocess_shell('id')\n",
    "ctypes": "def t():\n    import ctypes\n    return ctypes.CDLL(None).getpid()\n",
    "write /tmp": "def t():\n    open('/tmp/zensynora-sandbox-escape', 'w').write('x')\n",
    "rename into data": "def t():\n    import os\n    open('a', 'w').write('x')\n    os.rename('a', {rules!r} + '/a.md')\n",
    "socket localhost": "def t():\n    import socket\n    socket.create_connection(('127.0.0.1', 8000), 2)\n",
    "socket private": "def t():\n    import socket\n    socket.create_connection(('192.168.1.1', 80), 2)\n",
    "socket ipv6 loopback": "def t():\n    import socket\n    socket.create_connection(('::1', 80), 2)\n",
    "unix socket": "def t():\n    import socket\n    s = socket.socket(socket.AF_UNIX)\n    s.connect('/run/x.sock')\n",
    "kill parent": "def t():\n    import os\n    os.kill({pid}, 0)\n    return 'signalled'\n",
    "requests to the web ui": "import requests\ndef t():\n    return requests.post('http://127.0.0.1:8000/api/settings', "
                              "headers={{'X-ZenSynora-UI': '1'}}, timeout=3).status_code\n",
}


@pytest.mark.parametrize("what", list(ESCAPES))
def test_escapes_are_refused_even_past_the_source_check(sandbox, what):
    data = sandbox / ".myclaw"
    code = ESCAPES[what].format(rules=str(data / "rules"), config=str(data / "config.json"), data=str(data),
                                pid=os.getpid())
    out = run(code)
    assert isinstance(out, str) and out.startswith("Error:"), out
    assert list((data / "rules").iterdir()) == [] and not Path("/tmp/zensynora-sandbox-escape").exists()


def test_landlock_alone_stops_writes_outside_the_workspace(sandbox):
    """Without the audit hook, the kernel still refuses (when Landlock is available)."""
    from myclaw.tools import tool_sandbox

    st = tool_sandbox.status()
    if not st["landlock"].startswith("on"):
        pytest.skip(f"Landlock not available here: {st['landlock']}")
    data = sandbox / ".myclaw"
    cmd, payload, env, timeout = tool_sandbox._request(
        "t", f"def t():\n    open({str(data / 'rules' / 'x.md')!r}, 'w').write('x')\n", {}, "", "call")
    req = json.loads(payload)
    req["hook"] = False
    import subprocess

    r = subprocess.run(cmd, input=json.dumps(req), capture_output=True, text=True, timeout=60, env=env,
                       cwd=str(data / "workspace"))
    assert "PermissionError" in r.stdout and not (data / "rules" / "x.md").exists()
    req["code"] = f"def t():\n    return open({str(data / 'config.json')!r}).read()\n"
    r = subprocess.run(cmd, input=json.dumps(req), capture_output=True, text=True, timeout=60, env=env,
                       cwd=str(data / "workspace"))
    assert "PermissionError" in r.stdout and "providers" not in r.stdout


def test_timeout(sandbox, monkeypatch):
    from myclaw.tools import tool_sandbox

    monkeypatch.setattr(tool_sandbox, "_settings", lambda: (1, [80, 443]))
    out = run("import time\ndef t():\n    time.sleep(30)\n")
    assert out.startswith("Error:") and "did not finish within 1 s" in out


# ── register_tool / load_custom_tools use the sandbox ────────────────────────
WEATHER = '''import requests, json
import logging
logger = logging.getLogger(__name__)


def weather(city: str = "Paris"):
    """Current weather for a city (wttr.in)."""
    try:
        r = requests.get(f"https://wttr.in/{city}?format=j1", timeout=10)
        return json.dumps(r.json()["current_condition"][0])
    except Exception as e:
        logger.error(f"weather failed: {e}")
        return f"Error: {e}"
'''

TEXT = '''import re
import logging
logger = logging.getLogger(__name__)


def slugify_text(text: str, sep: str = "-"):
    """Make a slug."""
    try:
        return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)
    except Exception as e:
        logger.error(f"slug failed: {e}")
        return f"Error: {e}"


if __name__ == "__main__":
    print(slugify_text("Hello World"))
'''


def test_register_tool_runs_sandboxed(sandbox):
    from myclaw.tools import TOOLS, toolbox
    from myclaw.tools.tool_sandbox import SandboxedTool

    out = toolbox.register_tool("slugify_text", TEXT, "Make slugs")
    assert "Error" not in out, out
    func = TOOLS["slugify_text"]["func"]
    assert isinstance(func, SandboxedTool) and func.sandboxed
    assert func(text="Hello, World!") == "hello-world"
    assert "Error" not in toolbox.register_tool("weather", WEATHER, "Weather")
    assert isinstance(TOOLS["weather"]["func"], SandboxedTool)
    schema = next(s["function"] for s in toolbox.TOOL_SCHEMAS if s["function"]["name"] == "slugify_text")
    assert schema["parameters"]["required"] == ["text"]
    # changing the file afterwards does not change what runs (the checked code)
    (toolbox.TOOLBOX_DIR / "slugify_text.py").write_text("def slugify_text(text):\n    return 'changed'\n")
    assert TOOLS["slugify_text"]["func"](text="A B") == "a-b"
    TOOLS.pop("slugify_text", None)
    TOOLS.pop("weather", None)


def test_register_tool_reports_import_errors_from_the_sandbox(sandbox):
    from myclaw.tools import TOOLS, toolbox

    code = TEXT.replace("import re\n", "import re\nimport bs4.nonexistent_module_xyz\n")
    out = toolbox.register_tool("slugify_text", code, "x")
    assert out.startswith("Error loading tool:") and "slugify_text" not in TOOLS


def test_skipped_tools_are_listed_by_api_and_doctor(sandbox, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.tools import TOOLS, toolbox
    from myclaw.web import settings_api

    tb = toolbox.TOOLBOX_DIR
    (tb / "old_hack.py").write_text('def old_hack():\n    """x"""\n    import os\n    return os.getcwd()\n')
    (tb / "fine.py").write_text(TEXT.replace("slugify_text", "fine"))
    toolbox.TOOLBOX_REG.write_text(json.dumps({
        "old_hack": {"path": str(tb / "old_hack.py"), "author": "agent"},
        "fine": {"path": str(tb / "fine.py"), "author": "agent"}}))
    monkeypatch.setattr(toolbox, "_update_toolbox_readme", lambda: None)
    toolbox.load_custom_tools()
    assert "old_hack" not in TOOLS and "fine" in TOOLS
    assert "Importing 'os'" in toolbox.SKIPPED_TOOLS["old_hack"]
    app = FastAPI()
    app.include_router(settings_api.router)
    body = TestClient(app).get("/api/tools").json()
    assert body["skipped"] == [{"name": "old_hack", "reason": toolbox.SKIPPED_TOOLS["old_hack"]}]
    assert next(t for t in body["tools"] if t["name"] == "fine")["sandboxed"] is True
    from myclaw.doctor import run_checks

    r = {x.id: x for x in run_checks(offline=True, only=["tool_sandbox"]).results}["tool_sandbox"]
    assert r.status == "warn" and "old_hack" in r.detail and "register_tool" in r.hint
    TOOLS.pop("fine", None)


def test_doctor_tool_sandbox_ok(sandbox):
    from myclaw.doctor import run_checks

    r = {x.id: x for x in run_checks(offline=True, only=["tool_sandbox"]).results}["tool_sandbox"]
    assert r.status == "ok"
