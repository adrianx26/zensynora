"""Doctor checks and medic tools (0.7.0 rewrite of the placeholder medic)."""
import json
import os
from pathlib import Path

import pytest

import myclaw.agents.medic_agent as medic
from myclaw.doctor import registry
from myclaw.doctor.registry import FAIL, OK, SKIP, CheckResult, DoctorContext


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw").mkdir()
    return tmp_path


# ── every advertised medic tool must exist (12 of 15 were missing before) ────
def test_all_registered_medic_tools_exist():
    from myclaw.tools import _LAZY_AGENT_TOOLS

    for name, (module, func, _desc) in _LAZY_AGENT_TOOLS.items():
        if module == "myclaw.agents.medic_agent":
            assert callable(getattr(medic, func, None)), f"medic tool {func} missing"


def test_medic_tool_schemas_have_real_parameters():
    from myclaw.tools import TOOL_SCHEMAS, ensure_tool_schemas

    ensure_tool_schemas()
    schemas = {s["function"]["name"]: s["function"] for s in TOOL_SCHEMAS}
    props = schemas["recover_file"]["parameters"]["properties"]
    assert "file_path" in props and "args" not in props


# ── registry behaviour ───────────────────────────────────────────────────────
def test_crashing_check_becomes_failure_not_exception(monkeypatch):
    def boom(ctx):
        raise RuntimeError("kaputt")

    fake = registry._Check("boom", "Boom", "test", boom)
    monkeypatch.setattr(registry, "registered_checks", lambda: [fake])
    rep = registry.run_checks()
    assert rep.results[0].status == FAIL and "kaputt" in rep.results[0].detail
    assert rep.overall == FAIL


def test_network_checks_skipped_offline(monkeypatch):
    called = []
    fake = registry._Check("net", "Net", "llm", lambda ctx: called.append(1) or (OK, "x"), network=True)
    monkeypatch.setattr(registry, "registered_checks", lambda: [fake])
    rep = registry.run_checks(offline=True)
    assert rep.results[0].status == SKIP and not called


def test_full_offline_report_runs(home):
    rep = registry.run_checks(offline=True)
    cats = {r.category for r in rep.results}
    assert {"environment", "configuration", "data", "security", "install"} <= cats
    assert "Overall:" in rep.to_text()
    json.dumps(rep.to_dict())  # serialisable for the Web UI


def test_permission_check_and_fix(home):
    cfg = home / ".myclaw" / "config.json"
    cfg.write_text("{}")
    os.chmod(cfg, 0o644)
    rep = registry.run_checks(only=["permissions"])
    assert rep.results[0].status == "warn" and rep.results[0].fixable
    registry.apply_fixes(rep)
    assert oct(cfg.stat().st_mode & 0o777) == "0o600"


# ── medic tools ──────────────────────────────────────────────────────────────
def test_validate_and_detect(tmp_path):
    assert medic.validate_modification("a.py", "x = 1\n") == "VALID"
    assert medic.validate_modification("a.py", "def f(:\n").startswith("INVALID")
    assert "os.system" in medic.validate_modification("a.py", "import os\nos.system('x')\n")
    bad = tmp_path / "c.json"
    bad.write_text("{nope")
    assert medic.detect_errors_in_file(str(bad)).startswith("PARSE ERROR")
    good = tmp_path / "ok.py"
    good.write_text("print(1)\n")
    assert medic.detect_errors_in_file(str(good)).startswith("OK")


def test_loop_guard_blocks_runaway(monkeypatch):
    class Cfg:
        class medic:
            max_loop_iterations = 3

    monkeypatch.setattr(medic, "_config", Cfg)
    medic._reset_loop_counter()
    for _ in range(3):
        assert medic.prevent_infinite_loop().startswith("OK")
    assert "limit reached" in medic.prevent_infinite_loop().lower()
    medic._reset_loop_counter()

    from myclaw.agent_internals.medic_proxy import medic_loop_check
    assert medic_loop_check() is False


def test_task_analytics(home):
    medic.record_task_execution("a", True, 1.0)
    medic.record_task_execution("b", False, 3.0, "timeout")
    out = medic.get_task_analytics()
    assert "2 tasks" in out and "50%" in out and "b: 1 runs, 1 failed" in out


def test_integrity_scan_verify_recover(home, tmp_path, monkeypatch):
    from myclaw.doctor import integrity

    root = tmp_path / "install"
    (root / "myclaw").mkdir(parents=True)
    f = root / "myclaw" / "mod.py"
    f.write_text("GOOD = 1\n")
    monkeypatch.setattr(integrity, "install_root", lambda: root)

    assert "Recorded 1" in medic.scan_files()
    assert medic.verify_file_integrity().startswith("OK")
    medic.create_backup("myclaw/mod.py")

    f.write_text("BAD = 1\n")
    assert "modified: myclaw/mod.py" in medic.verify_file_integrity()
    assert "local backup" in medic.recover_file("myclaw/mod.py")
    assert f.read_text() == "GOOD = 1\n"

    f.unlink()  # newest backup is now the damaged copy; recovery must still pick the good one
    medic.recover_file("myclaw/mod.py")
    assert f.read_text() == "GOOD = 1\n"

    assert medic.recover_file("../../etc/passwd").startswith("ERROR")
    assert "disabled" in medic.enable_hash_check(False)
    assert "disabled" in medic.verify_file_integrity()
