"""Problems from the diagnostics report of 2026-10-23.

* shell: '|' and '&&' between allowed commands were always refused
* read_file: folders failed ("Is a directory"), wrong paths were logged as ERRORs
* integrity: after an update with the same version, "44 files differ"
* task timer: fixed 300 s limit (now timeouts.task_seconds; jobs use their own)
"""
import asyncio
import json
import sys

import pytest

import myclaw.tools  # noqa: F401  (registers the tools package)

shell_mod = sys.modules["myclaw.tools.shell"]


@pytest.fixture
def ws(tmp_path, monkeypatch):
    from myclaw.tools import core

    (tmp_path / "a.md").write_text("alpha\n")
    (tmp_path / "b.py").write_text("print(1)\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "c.md").write_text("gamma\n")
    monkeypatch.setattr(core, "WORKSPACE", tmp_path)
    monkeypatch.setattr(shell_mod, "WORKSPACE", tmp_path)
    monkeypatch.setattr(core._rate_limiter, "check", lambda *a, **k: True)
    return tmp_path


# ── shell ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("cmd, expected", [
    ("ls -la | grep py", [("", [["ls", "-la"], ["grep", "py"]])]),
    ("pwd && ls", [("", [["pwd"]]), ("&&", [["ls"]])]),
    ('find . -name "*.md" -o -name "README*" | head -20',
     [("", [["find", ".", "-name", "*.md", "-o", "-name", "README*"], ["head", "-20"]])]),
    ('grep "a|b" x || echo none', [("", [["grep", "a|b", "x"]]), ("||", [["echo", "none"]])]),
    ('echo "a;b"; ls', [("", [["echo", "a;b"]]), (";", [["ls"]])]),
])
def test_chains_of_allowed_commands_parse(cmd, expected):
    assert shell_mod.parse(cmd) == expected


@pytest.mark.parametrize("cmd, why", [
    ("ls | rm -rf x", "blocked"),
    ("ls > out.txt", "'>' is not allowed"),
    ("ls & ls", "'&' is not allowed"),
    ("ls $(whoami)", "dangerous"),
    ("ls `id`", "dangerous"),
    ("(ls)", "'(' is not allowed"),
    ("find . -delete", "find -delete"),
    ("find . -exec cat x", "find -exec"),
    ("ls &&", "Missing command"),
    ("touch x | ls", "not allowed"),
    ("ls\nrm x", "dangerous"),
])
def test_unsafe_chains_are_refused(cmd, why):
    with pytest.raises(shell_mod.ShellError) as e:
        shell_mod.parse(cmd)
    assert why in str(e.value)


def test_chains_run_without_a_shell(ws):
    from myclaw.tools import shell

    assert shell("ls | grep md").strip() == "a.md"
    assert shell("cat nope.txt || echo fallback").strip().endswith("fallback")
    assert "never" not in shell("cat nope.txt && echo never")
    out = shell("pwd ; ls docs | wc -l")
    assert str(ws) in out and out.strip().endswith("1")


def test_async_shell_runs_chains(ws, monkeypatch):
    from myclaw.tools import core

    async def ok(*a, **k):
        return True
    monkeypatch.setattr(core._rate_limiter, "acheck", ok)
    assert asyncio.run(shell_mod.shell_async("ls | grep py")).strip() == "b.py"


def test_allowlist_is_read_at_call_time(monkeypatch):
    from myclaw.tools import core

    monkeypatch.setattr(core, "ALLOWED_COMMANDS", frozenset({"ls", "ps", "grep"}))
    assert shell_mod.parse("ps aux | grep zensynora")[0][1][0] == ["ps", "aux"]


# ── read_file ────────────────────────────────────────────────────────────────
def test_read_file_lists_a_folder(ws):
    from myclaw.tools import read_file

    out = read_file("docs")
    assert out.startswith("'docs' is a folder (1 entries)") and "c.md" in out
    assert "docs/" in read_file(".")


def test_read_file_missing_shows_what_is_there_and_does_not_log_error(ws, caplog):
    from myclaw.tools import read_file

    with caplog.at_level("INFO"):
        out = read_file("config.json")
    assert out.startswith("Error: No file 'config.json'") and "a.md" in out and "docs/" in out
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_read_file_outside_workspace_explains(ws):
    from myclaw.tools import read_file

    out = read_file("/etc/passwd")
    assert "only reads files inside the workspace" in out and "get_recent_problems" in out


def test_read_file_binary_and_large(ws, monkeypatch):
    from myclaw.tools import read_file

    files_mod = sys.modules["myclaw.tools.files"]
    (ws / "x.zip").write_bytes(b"PK\x03\x04\x00\x00" * 100)
    assert "binary file" in read_file("x.zip")
    monkeypatch.setattr(files_mod, "MAX_READ_BYTES", 10)
    (ws / "big.txt").write_text("0123456789abcdef")
    out = read_file("big.txt")
    assert out.startswith("0123456789") and "only the first 10 of 16 bytes" in out


# ── integrity ────────────────────────────────────────────────────────────────
def test_baseline_from_another_release_folder_is_stale(tmp_path, monkeypatch):
    from myclaw import __version__
    from myclaw.doctor import integrity

    monkeypatch.setattr(integrity, "medic_dir", lambda: tmp_path)
    monkeypatch.setattr(integrity, "install_root", lambda: tmp_path / "releases" / "new")
    (tmp_path / "integrity.json").write_text(json.dumps({
        "version": __version__, "root": str(tmp_path / "releases" / "old"),
        "files": {"myclaw/x.py": "0" * 64}}))
    res = integrity.verify()
    assert res["status"] == "stale-baseline" and "now runs from" in res["note"]


# ── task timer ───────────────────────────────────────────────────────────────
def test_task_limit_comes_from_settings_and_jobs(monkeypatch):
    from myclaw.config import AppConfig
    from myclaw.task_timer import TASK_MAX_SECONDS, configured_max_timeout

    assert AppConfig().timeouts.task_seconds == 600
    cfg = AppConfig()
    cfg.timeouts.task_seconds = 900
    monkeypatch.setattr("myclaw.config.load_config", lambda *a, **k: cfg)
    assert configured_max_timeout() == 900
    tok = TASK_MAX_SECONDS.set(1830)
    try:
        assert configured_max_timeout() == 1830
    finally:
        TASK_MAX_SECONDS.reset(tok)
    with pytest.raises(Exception):
        AppConfig(**{"timeouts": {"task_seconds": 5}})


# ── knowledge auto-save ──────────────────────────────────────────────────────
def test_transient_tool_output_is_not_saved_as_knowledge():
    from myclaw.agent import Agent

    long = "x" * 200
    assert not Agent._should_save_tool_result("shell", long)        # listings, ps output …
    assert not Agent._should_save_tool_result("write_file", long)   # the agent's own reports
    assert not Agent._should_save_tool_result("browse", "Error: " + long)
    assert Agent._should_save_tool_result("browse", long)
    assert Agent._should_save_tool_result("read_file", long)
