"""v0.7.4: prompt writer / job judge fall back through the rotation list;
Web UI update check, update and restart."""

import asyncio
import json
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def home(tmp_path, monkeypatch):
    import myclaw.config as cfg_mod
    from myclaw.web import settings_api

    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw").mkdir()
    cfg_file = tmp_path / ".myclaw" / "config.json"
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    monkeypatch.setattr(settings_api, "CONFIG_FILE", cfg_file)
    cfg_mod._cached_config = None
    yield tmp_path
    cfg_mod._cached_config = None


def test_prompt_generation_falls_back_when_the_main_model_is_refused(home, monkeypatch):
    """Real case: default inkling:free → OpenRouter 403; rotation had working models."""
    import myclaw.provider as prov
    from myclaw import llm_errors
    from myclaw.web import models_api, settings_api

    monkeypatch.setattr(llm_errors, "MODEL_COOLDOWN", {})
    tried = []

    async def fake_complete(provider, messages, model):
        tried.append(model)
        if model == "inkling:free":
            raise RuntimeError("Error code: 403 - inkling:free is only available on agentic harnesses")
        return "You are Researcher, an agent that checks sources."

    monkeypatch.setattr(prov, "complete_text", fake_complete)
    monkeypatch.setattr(prov, "get_provider", lambda cfg, name=None: object())
    app = FastAPI()
    app.include_router(settings_api.router)
    app.include_router(models_api.router)
    c = TestClient(app)
    c.put("/api/settings", json={"changes": {
        "agents.defaults.provider": "openrouter", "agents.defaults.model": "inkling:free",
        "agents.rotation.entries": [{"provider": "openrouter", "model": "north-mini"}]}})
    r = c.post("/api/agents/generate-prompt", json={"name": "Researcher", "description": "checks sources"})
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "north-mini" and tried == ["inkling:free", "north-mini"]
    # The refused model is skipped next time.
    c.post("/api/agents/generate-prompt", json={"name": "Researcher"})
    assert tried[-1] == "north-mini" and tried.count("inkling:free") == 1


def test_prompt_generation_reports_every_reason_when_all_fail(home, monkeypatch):
    import myclaw.provider as prov
    from myclaw import llm_errors
    from myclaw.web import models_api

    monkeypatch.setattr(llm_errors, "MODEL_COOLDOWN", {})

    async def fail(provider, messages, model):
        raise RuntimeError("Error code: 429 - temporarily rate-limited upstream")

    monkeypatch.setattr(prov, "complete_text", fail)
    monkeypatch.setattr(prov, "get_provider", lambda cfg, name=None: object())
    app = FastAPI()
    app.include_router(models_api.router)
    r = TestClient(app).post("/api/agents/generate-prompt", json={"name": "X"})
    assert r.status_code == 502 and "rate-limited" in r.json()["detail"]


# ── Web UI update check / update / rollback / restart ───────────────────────

FAKE_UPDATE_SH = """#!/usr/bin/env bash
# stand-in for update.sh: prints the same step lines as the real updater
echo "args: $*"
for i in 1 2 3; do echo "[$i/14] step $i"; sleep 0.2; done
if [ "$1" = "rollback" ]; then echo "✔ Rolled back to 0.7.2"; else echo "✔ Updated 0.7.2 → 0.7.4"; fi
"""


@pytest.fixture
def upd(home, monkeypatch):
    from myclaw.web import settings_api, update_api

    base = home / "install"
    root = base / "releases" / "0.7.2-1"
    (root / "venv" / "bin").mkdir(parents=True)
    (root / "update.sh").write_text(FAKE_UPDATE_SH)
    monkeypatch.setattr(update_api, "install_base", lambda: base)
    monkeypatch.setattr(update_api, "install_root", lambda: root)
    monkeypatch.setattr(update_api, "active_release", lambda: root)
    monkeypatch.setattr(update_api, "release_version", lambda p: "0.7.2")
    monkeypatch.setattr(update_api, "stale_release", lambda: None)
    monkeypatch.setattr(update_api, "_proc", None)
    app = FastAPI()
    app.include_router(update_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    with TestClient(app) as c:
        yield c, base, update_api


def _wait_done(c, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        s = c.get("/api/update/status").json()
        if not s["update_running"]:
            return s
        time.sleep(0.2)
    raise AssertionError("update did not finish")


def test_update_runs_in_the_background_and_reports_progress(upd):
    c, base, _ = upd
    s = c.get("/api/update/status").json()
    assert s["managed"] and not s["update_running"] and s["installed_version"] == "0.7.2"

    r = c.post("/api/update/start", json={"ref": "feature/x", "force": True})
    assert r.status_code == 200, r.text
    assert c.post("/api/update/start", json={}).status_code == 409   # one at a time
    running = c.get("/api/update/status").json()
    assert running["update_running"] and running["action"] == "update"

    done = _wait_done(c)
    assert done["finished_ok"] and done["result"] == "Updated 0.7.2 → 0.7.4"
    assert done["step"] == 3 and done["steps"] == 14
    assert "args: update --base" in done["tail"] and "--ref feature/x --force" in done["tail"]
    assert Path(done["log"]).parent == base / "logs"
    assert json.loads((base / "webui-update.json").read_text())["exit_code"] == 0

    assert c.post("/api/update/rollback").status_code == 200
    assert _wait_done(c)["result"] == "Rolled back to 0.7.2"


def test_bad_ref_and_unmanaged_install_are_refused(upd, monkeypatch):
    c, _, update_api = upd
    assert c.post("/api/update/start", json={"ref": "main; rm -rf ~"}).status_code == 400
    monkeypatch.setattr(update_api, "install_base", lambda: None)
    s = c.get("/api/update/status?check=true").json()
    assert not s["managed"] and "not a standard install" in s["not_managed_reason"]
    assert "latest_version" not in s
    assert c.post("/api/update/start", json={}).status_code == 400


def test_failed_update_is_reported(upd):
    c, base, update_api = upd
    (update_api.install_root() / "update.sh").write_text('echo "[1/14] download"; echo "network down"; exit 3\n')
    c.post("/api/update/start", json={})
    done = _wait_done(c)
    assert not done["finished_ok"] and done["exit_code"] == 3 and "network down" in done["tail"]


def test_check_reads_the_updater_json(upd, monkeypatch):
    c, _, update_api = upd
    monkeypatch.setattr(update_api, "_run_updater_json", lambda args, timeout=45: {
        "latest_version": "0.7.4", "update_available": True, "last_actions": [{"action": "update"}],
        "releases": ["0.7.1-1", "0.7.2-1"], "active_release": "0.7.2-1"})
    s = c.get("/api/update/status?check=true").json()
    assert s["latest_version"] == "0.7.4" and s["update_available"] and s["active_release"] == "0.7.2-1"

    def boom(args, timeout=45):
        raise RuntimeError("no network")
    monkeypatch.setattr(update_api, "_run_updater_json", boom)
    assert "no network" in c.get("/api/update/status?check=true").json()["check_error"]


def test_restart_is_refused_outside_webui_and_during_an_update(upd, monkeypatch):
    c, _, update_api = upd
    execs = []
    monkeypatch.setattr(update_api.os, "execve", lambda *a: execs.append(a))
    monkeypatch.setattr(update_api.sys, "argv", ["zensynora", "gateway"])
    assert c.post("/api/update/restart").status_code == 400
    monkeypatch.setattr(update_api.sys, "argv", ["zensynora", "webui", "--port", "8000"])
    c.post("/api/update/start", json={})
    assert c.post("/api/update/restart").status_code == 409
    _wait_done(c)
    monkeypatch.setattr(update_api.time, "sleep", lambda s: None)
    r = c.post("/api/update/restart")
    assert r.status_code == 200 and r.json()["restarting_with"] == "0.7.2"
    end = time.time() + 3
    while not execs and time.time() < end:
        time.sleep(0.05)
    argv, env = execs[0][1], execs[0][2]
    assert argv[1:] == ["-m", "myclaw", "webui", "--port", "8000"] and env["ZENSYNORA_NO_REEXEC"] == "1"


def test_already_up_to_date_counts_as_success(tmp_path):
    from myclaw.web.update_api import _progress

    log = tmp_path / "u.log"
    log.write_text("[1/14] Checking\n[2/14] Getting the new version\n✔ Already on 0.7.4. Use --force to reinstall it.\n")
    assert _progress(str(log))["result"] == "Already on 0.7.4"
