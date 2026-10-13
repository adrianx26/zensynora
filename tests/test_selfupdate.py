"""Approval-gated self-update (v0.7.1): read, propose, approve, apply, learn."""

import json
import subprocess
from datetime import UTC
from pathlib import Path

import pytest

from myclaw import selfupdate


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A fake managed install: base/releases/1.0-x with code, and a fake HOME."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    base = tmp_path / "base"
    root = base / "releases" / "1.0-x"
    (root / "myclaw").mkdir(parents=True)
    (root / "venv" / "lib").mkdir(parents=True)
    (root / "myclaw" / "mod.py").write_text("X = 1\n")
    (root / "README.md").write_text("# Old\n")
    (root / ".env").write_text("SECRET=1\n")
    (root / "venv" / "lib" / "x.py").write_text("y = 2\n")
    (root / "update.sh").write_text("#!/bin/bash\n")
    (root / "pyproject.toml").write_text("[project]\nname='x'\n")
    monkeypatch.setattr(selfupdate, "install_root", lambda: root)
    monkeypatch.setattr(selfupdate, "install_base", lambda: base)
    monkeypatch.setattr(selfupdate, "_cfg_flag", lambda name, default=False: {"enabled": True}.get(name, default))
    return root


def test_read_and_list_are_limited_to_the_program_folder(install):
    assert "X = 1" in selfupdate.read_source_file("myclaw/mod.py")
    for bad in ("../../etc/passwd", ".env", "venv/lib/x.py", "/etc/passwd"):
        assert selfupdate.read_source_file(bad).startswith("Error")
    listing = selfupdate.list_source_files("**/*")
    assert "myclaw/mod.py" in listing and ".env" not in listing and "venv/" not in listing
    assert selfupdate.list_source_files("../*").startswith("Error")


def test_proposal_validation(install):
    with pytest.raises(ValueError, match="syntax error"):
        selfupdate.create_proposal("t", "r", {"myclaw/mod.py": "def (:\n"})
    with pytest.raises(ValueError, match="identical"):
        selfupdate.create_proposal("t", "r", {"myclaw/mod.py": "X = 1\n"})
    with pytest.raises(ValueError, match="not available"):
        selfupdate.create_proposal("t", "r", {".env": "SECRET=2\n"})
    with pytest.raises(ValueError, match="outside"):
        selfupdate.create_proposal("t", "r", {"../evil.py": "x=1\n"})
    p = selfupdate.create_proposal("Fix X", "because", {"myclaw/mod.py": "X = 2\n"})
    assert p["status"] == "pending" and p["risk"] == "high"
    assert "-X = 1" in p["diff"] and "+X = 2" in p["diff"]
    docs = selfupdate.create_proposal("Docs", "typo", {"README.md": "# New\n"})
    assert docs["risk"] == "low"
    # The running code is untouched by a proposal.
    assert (install / "myclaw" / "mod.py").read_text() == "X = 1\n"


def test_agent_tool_wrapper_reports_problems_instead_of_raising(install):
    msg = selfupdate.propose_code_change("t", "r", "myclaw/mod.py", "def (:")
    assert msg.startswith("Proposal rejected")
    ok = selfupdate.propose_code_change("t", "r", files_json=json.dumps({"myclaw/mod.py": "X = 3\n"}))
    assert "saved" in ok and "approval" in ok.lower()


def test_apply_goes_through_the_updater_and_records_the_result(install):
    p = selfupdate.create_proposal("Fix X", "because", {"myclaw/mod.py": "X = 2\n"})
    with pytest.raises(ValueError, match="approved"):
        selfupdate.apply_proposal(p["id"])
    selfupdate.approve(p["id"])
    seen = {}

    def fake_updater(cmd, **kw):
        staging = Path(cmd[cmd.index("--from-dir") + 1])
        seen["staged"] = (staging / "myclaw" / "mod.py").read_text()
        seen["venv_copied"] = (staging / "venv").exists()
        seen["cmd"] = cmd
        kw["stdout"].write("✔ Updated\n")
        return subprocess.CompletedProcess(cmd, 0)

    done = selfupdate.apply_proposal(p["id"], runner=fake_updater)
    assert done["status"] == "applied" and done["result"]["ok"]
    assert seen["staged"] == "X = 2\n" and not seen["venv_copied"]
    assert "--force" in seen["cmd"] and "--skip-webui" in seen["cmd"]
    # The running release itself was never written to, and staging is gone.
    assert (install / "myclaw" / "mod.py").read_text() == "X = 1\n"
    assert not any((install.parent.parent / "staging").glob("*"))


def test_failed_update_and_outdated_proposal(install):
    p = selfupdate.create_proposal("Fix X", "r", {"myclaw/mod.py": "X = 2\n"})
    selfupdate.approve(p["id"])
    failed = selfupdate.apply_proposal(p["id"], runner=lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    assert failed["status"] == "failed" and "rolled back" in failed["result"]["note"]

    q = selfupdate.create_proposal("Other", "r", {"myclaw/mod.py": "X = 5\n"})
    selfupdate.approve(q["id"])
    (install / "myclaw" / "mod.py").write_text("X = 9\n")  # code changed meanwhile
    out = selfupdate.apply_proposal(q["id"], runner=lambda *a, **k: pytest.fail("must not run"))
    assert out["status"] == "failed"
    assert "changed since" in out["history"][-1]["note"]


def test_reject_and_delete(install):
    p = selfupdate.create_proposal("t", "r", {"README.md": "# x\n"})
    selfupdate.reject(p["id"], note="no")
    assert selfupdate.get_proposal(p["id"])["status"] == "rejected"
    with pytest.raises(ValueError):
        selfupdate.approve(p["id"])
    assert selfupdate.delete_proposal(p["id"]) and selfupdate.get_proposal(p["id"]) is None


def test_recent_problems_reads_the_log(install):
    from myclaw.doctor.paths import log_file

    lf = log_file()
    lf.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime

    now = datetime.now(UTC).isoformat()
    lf.write_text("\n".join(json.dumps({"timestamp": now, "level": "ERROR", "message": "boom"}) for _ in range(3)))
    probs = selfupdate.recent_problems()
    assert probs["errors"][0]["message"] == "boom" and probs["errors"][0]["count"] == 3


def test_recent_problems_includes_failed_and_low_score_runs(install):
    from myclaw.jobs import store

    job = store.create_job({"name": "Nightly", "prompt": "do it"})
    bad = store.start_run(job["id"])
    store.finish_run(bad, "error", "", "provider down")
    low = store.start_run(job["id"])
    store.finish_run(low, "ok", "meh")
    store.set_evaluation(low, 3, {"score": 3, "verdict": "fail", "reasons": "missed half the items"})
    good = store.start_run(job["id"])
    store.finish_run(good, "ok", "great")
    store.set_evaluation(good, 9, {"score": 9, "verdict": "pass", "reasons": "all good"})
    probs = selfupdate.recent_problems()["job_problems"]
    assert {p["status"] for p in probs} == {"error", "ok"} and len(probs) == 2
    assert any("missed half" in p["evaluation"] for p in probs)
