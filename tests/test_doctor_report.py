"""Doctor: more checks (restarts, repeated warnings, custom tools, knowledge
index, skills, MCP, chat DB), the diagnostics report and its API."""

from __future__ import annotations

import io
import json
import sqlite3
import time
import zipfile
from datetime import UTC, datetime, timedelta

import pytest

from tests.test_v071_features import home  # noqa: F401

TG_TOKEN = "123456789:" + "A" * 35
SK_KEY = "sk-" + "b" * 40


def _ts(seconds_ago: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


def _write_log(h, records):
    lf = h / ".myclaw" / "logs" / "zensynora.log"
    lf.parent.mkdir(parents=True, exist_ok=True)
    lf.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return lf


def _rec(level, message, ago=0, module="mod", **extra):
    return {"timestamp": _ts(ago), "level": level, "message": message, "module": module, **extra}


def _run(*ids):
    from myclaw.doctor import run_checks

    return {r.id: r for r in run_checks(offline=True, only=ids).results}


# ── grouping, redaction ──────────────────────────────────────────────────────
def test_grouping_strips_numbers_and_ids():
    from myclaw.doctor import logscan

    entries = [logscan.Entry(None, _ts(), "CRITICAL", "task_timer", f"Task task_webui_{n} timed out after {n}s")
               for n in (123, 456, 789)]
    entries.append(logscan.Entry(None, _ts(), "ERROR", "x", "Request 3f2a9c1e-1111-2222-3333-444455556666 failed"))
    entries.append(logscan.Entry(None, _ts(), "ERROR", "x", "Request 9b2a9c1e-1111-2222-3333-444455556666 failed"))
    groups = logscan.group(entries)
    assert [(g["level"], g["count"]) for g in groups] == [("CRITICAL", 3), ("ERROR", 2)]
    assert groups[0]["key"] == "Task task_webui_# timed out after #s"


def test_redact_hides_tokens_and_keys():
    from myclaw.doctor.logscan import redact

    text = (f"bot {TG_TOKEN} key {SK_KEY} api_key=supersecret123 "
            "url https://user:pw@host/x AIza" + "c" * 35 + " Bearer abcdefghijklmnop")
    out = redact(text)
    for secret in (TG_TOKEN, SK_KEY, "supersecret123", "user:pw", "c" * 35, "abcdefghijklmnop"):
        assert secret not in out
    assert redact("Telegram: Another program") == "Telegram: Another program"


# ── checks ───────────────────────────────────────────────────────────────────
def test_restart_loop_now_is_a_failure(home):  # noqa: F811
    recs = [_rec("INFO", "ZenSynora v0.7.5 initialised (log_level=INFO, cmd=gateway)", ago=7 * i)
            for i in range(15)]
    recs += [_rec("INFO", "ZenSynora v0.7.5 initialised (log_level=INFO, cmd=doctor)", ago=1) for _ in range(30)]
    _write_log(home, recs)
    r = _run("restart_loop")["restart_loop"]
    assert r.status == "fail" and "crash loop" in r.detail and "journalctl -u myclaw" in r.hint


def test_restart_loop_earlier_is_a_warning_and_cli_runs_do_not_count(home):  # noqa: F811
    recs = [_rec("INFO", "ZenSynora v0.7.5 initialised (log_level=INFO)", ago=7200 + 7 * i) for i in range(12)]
    _write_log(home, recs)
    assert _run("restart_loop")["restart_loop"].status == "warn"
    _write_log(home, [_rec("INFO", "ZenSynora v0.7.5 initialised (log_level=INFO, cmd=knowledge)", ago=i)
                      for i in range(40)])
    assert _run("restart_loop")["restart_loop"].status == "ok"


def test_recent_errors_counts_levels_and_repeated_warnings(home):  # noqa: F811
    recs = [_rec("WARNING", f"Could not start background knowledge extraction: attempt {i}", ago=i)
            for i in range(8)]
    recs += [_rec("WARNING", "rare warning", ago=3)]
    recs += [_rec("ERROR", "Telegram: Another program is using this bot", ago=5) for _ in range(3)]
    recs += [_rec("CRITICAL", f"Task task_webui_{i} timed out", ago=9) for i in range(2)]
    recs += [_rec("ERROR", "old error", ago=3 * 86400)]
    _write_log(home, recs)
    r = _run("recent_errors")["recent_errors"]
    assert r.status == "warn"
    assert "CRITICAL 2" in r.detail and "ERROR 3" in r.detail and "repeated WARNING 8" in r.detail
    assert "rare warning" not in r.detail and "old error" not in r.detail
    _write_log(home, recs + [_rec("CRITICAL", "Task task_webui_99 timed out")])
    assert _run("recent_errors")["recent_errors"].status == "fail"


def _toolbox(h, name, code, enabled=True):
    d = h / ".myclaw" / "TOOLBOX"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.py").write_text(code)
    reg = d / "toolbox_registry.json"
    data = json.loads(reg.read_text()) if reg.exists() else {}
    data[name] = {"path": str(d / f"{name}.py"), "enabled": enabled}
    reg.write_text(json.dumps(data))


def test_custom_tools_placeholders_and_secrets(home):  # noqa: F811
    _toolbox(home, "fine", "def fine():\n    return 1\n")
    assert _run("custom_tools")["custom_tools"].status == "ok"
    _toolbox(home, "telegram_integration", "TOKEN = 'YOUR_BOT_TOKEN'\nCHAT = 'YOUR_CHAT_ID_HERE'\n")
    _toolbox(home, "leaky", f"KEY = '{SK_KEY}'\n")
    _toolbox(home, "off", "TOKEN = 'YOUR_BOT_TOKEN'\n", enabled=False)
    r = _run("custom_tools")["custom_tools"]
    assert r.status == "warn"
    assert "telegram_integration (placeholder credentials)" in r.detail and "leaky (hard-coded secret)" in r.detail
    assert "off" not in r.detail.split("(")[0] and SK_KEY not in r.detail
    assert "disable_skill" in r.hint and "send_telegram_message" in r.hint


def test_knowledge_index_warns_and_fixer_syncs(home):  # noqa: F811
    from myclaw.doctor import apply_fixes, run_checks
    from myclaw.knowledge import write_note

    write_note(name="indexed", title="Indexed", user_id="default")
    kdir = home / ".myclaw" / "knowledge" / "default"
    for i in range(4):
        (kdir / f"loose-{i}.md").write_text(f"---\ntitle: Loose {i}\n---\n# Loose {i}\n")
    r = _run("knowledge_index")["knowledge_index"]
    assert r.status == "warn" and "4 not indexed" in r.detail and "knowledge sync" in r.hint
    rep = run_checks(offline=True, only=["knowledge_index"])
    assert rep.results[0].fixable
    done = apply_fixes(rep)
    assert "knowledge synced" in done[0]
    assert _run("knowledge_index")["knowledge_index"].status == "ok"


def test_skills_unknown_and_broken(home):  # noqa: F811
    from myclaw.config import load_config, save_config

    cfg = load_config()
    cfg.agents.defaults.skills = ["no-such-skill"]
    save_config(cfg)
    sk = home / ".myclaw" / "skills" / "empty"
    sk.mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: empty\ndescription: x\n---\n")
    import myclaw.config as cfg_mod

    cfg_mod._cached_config = None
    r = _run("skills")["skills"]
    assert r.status == "warn" and "@default → no-such-skill" in r.detail and "empty: empty instructions" in r.detail


def test_mcp_servers_missing_command(home):  # noqa: F811
    from myclaw.config import MCPServerConfig, load_config, save_config

    cfg = load_config()
    cfg.mcp.enabled = True
    cfg.mcp.servers = {"ok": MCPServerConfig(command="python3"),
                       "gone": MCPServerConfig(command="definitely-not-installed-xyz")}
    save_config(cfg)
    import myclaw.config as cfg_mod

    cfg_mod._cached_config = None
    r = _run("mcp_servers")["mcp_servers"]
    assert r.status == "warn" and "gone" in r.detail and "ok:" not in r.detail


def test_chat_db_leftovers_and_ghost_members(home):  # noqa: F811
    from myclaw.chat import store

    ch = store.create_channel("team", ["default"])
    store.add_message(ch["id"], "agent", "default", "half", {"streaming": True})
    con = sqlite3.connect(store.db_path())
    con.execute("UPDATE messages SET created = ?", (time.time() - 3600,))
    con.execute("UPDATE channels SET members = ? WHERE id = ?", (json.dumps(["default", "ghost"]), ch["id"]))
    con.commit()
    con.close()
    r = _run("chat_db")["chat_db"]
    assert r.status == "warn" and "1 answer(s) left unfinished" in r.detail and "@ghost" in r.detail


# ── report ───────────────────────────────────────────────────────────────────
def _noisy_install(h):
    from myclaw.config import load_config, save_config

    cfg = load_config()
    cfg.channels.telegram.token = TG_TOKEN
    cfg.providers.openai.api_key = SK_KEY
    save_config(cfg)
    import myclaw.config as cfg_mod

    cfg_mod._cached_config = None
    tb = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: bad " + SK_KEY
    _write_log(h, [
        _rec("INFO", "ZenSynora v0.7.5 initialised (log_level=INFO, cmd=webui)", ago=60),
        _rec("ERROR", f"Telegram failed with token {TG_TOKEN}", ago=30, exception=tb),
        _rec("WARNING", "slow 1", ago=20), _rec("WARNING", "slow 2", ago=10),
    ])
    _toolbox(h, "telegram_integration", "TOKEN = 'YOUR_BOT_TOKEN'\n")


def test_build_report_and_renderers_have_no_secrets(home):  # noqa: F811
    from myclaw.doctor import report

    _noisy_install(home)
    rep = report.build_report(hours=24, offline=True)
    assert rep["version"] and rep["summary"]["errors"]["ERROR"] == 1 and rep["summary"]["restarts"] == 1
    g = next(g for g in rep["errors"]["groups"] if g["level"] == "ERROR")
    assert g["traceback"] and "Traceback" in g["samples"][0]
    assert {"telegram", "whatsapp"} <= set(rep["config"]["channels"])
    assert rep["config"]["channels"]["telegram"]["token_set"] is True
    assert next(p for p in rep["config"]["providers"] if p["name"] == "openai")["key_set"] is True
    assert any(r["id"] == "custom_tools" and r["status"] == "warn" for r in rep["doctor"]["results"])
    assert rep["log_tail"]
    md, js, z = report.to_markdown(rep), report.to_json(rep), report.to_zip(rep)
    assert "## Errors and warnings" in md and "## Doctor checks" in md and "Traceback" in md
    with zipfile.ZipFile(io.BytesIO(z)) as zf:
        assert set(zf.namelist()) == {"report.md", "report.json", "zensynora.log.tail.txt"}
        blobs = [zf.read(n).decode() for n in zf.namelist()]
    for text in [md, js, *blobs]:
        assert TG_TOKEN not in text and SK_KEY not in text and TG_TOKEN.split(":")[1] not in text


def test_report_api(home):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import doctor_api, settings_api

    _noisy_install(home)
    app = FastAPI()
    app.include_router(doctor_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    with TestClient(app) as c:
        rep = c.get("/api/doctor/report?hours=24&offline=true").json()
        assert rep["summary"]["errors"]["ERROR"] == 1
        for fmt, ctype in (("md", "text/markdown"), ("json", "application/json"), ("zip", "application/zip")):
            r = c.get(f"/api/doctor/report/download?format={fmt}&offline=true")
            assert r.status_code == 200 and r.headers["content-type"].startswith(ctype)
            assert 'filename="zensynora-report-' in r.headers["content-disposition"]
            assert r.headers["content-disposition"].rstrip('"').endswith("." + fmt)
            if fmt != "zip":
                assert TG_TOKEN not in r.text and SK_KEY not in r.text
        assert c.get("/api/doctor/report/download?format=exe").status_code == 422
        e = c.get("/api/doctor/errors?hours=1&level=WARNING").json()
        assert e["counts"]["WARNING"] == 2 and {g["level"] for g in e["groups"]} == {"WARNING"}
        assert len(c.get("/api/doctor/errors?q=telegram").json()["groups"]) == 1
        assert c.get("/api/doctor/errors?level=DEBUG").status_code == 400


def test_cli_report(home, tmp_path):  # noqa: F811
    from click.testing import CliRunner

    from myclaw.cli import cli

    _noisy_install(home)
    out = tmp_path / "r.json"
    res = CliRunner().invoke(cli, ["doctor", "--offline", "--report", str(out)])
    assert res.exit_code == 0, res.output
    assert res.output.strip() == str(out)
    data = json.loads(out.read_text())
    assert data["summary"]["errors"]["ERROR"] == 1 and SK_KEY not in out.read_text()
    md = tmp_path / "r.md"
    CliRunner().invoke(cli, ["doctor", "--offline", "--report", str(md)])
    assert md.read_text().startswith("# ZenSynora diagnostics report")


def test_recent_problems_sees_new_signals(home):  # noqa: F811
    from myclaw import selfupdate

    recs = [_rec("INFO", "ZenSynora v0.7.5 initialised (log_level=INFO, cmd=gateway)", ago=7 * i) for i in range(12)]
    recs += [_rec("WARNING", f"Could not start background knowledge extraction {i}", ago=i) for i in range(6)]
    recs += [_rec("CRITICAL", f"Task task_webui_{i} timed out", ago=i) for i in range(3)]
    _write_log(home, recs)
    _toolbox(home, "telegram_integration", "TOKEN = 'YOUR_BOT_TOKEN'\n")
    p = selfupdate.recent_problems()
    assert p["errors"][0]["count"] == 3 and p["errors"][0]["level"] == "CRITICAL"
    assert p["repeated_warnings"][0]["count"] == 6
    assert p["restarts"]["crash_loop"] is True
    assert p["risky_custom_tools"][0]["name"] == "telegram_integration"
    assert "crash_loop" in selfupdate.SELF_REVIEW_PROMPT and "disable_skill" in selfupdate.SELF_REVIEW_PROMPT


@pytest.mark.parametrize("check_id", ["restart_loop", "recent_errors", "custom_tools", "knowledge_index",
                                      "skills", "mcp_servers", "chat_db"])
def test_new_checks_are_fast_and_clean_on_empty_home(home, check_id):  # noqa: F811
    r = _run(check_id)[check_id]
    assert r.status in ("ok", "skip") and r.duration_ms < 1000


# ── 2026-10-31: Errors viewer — failures, failed jobs, restarts ─────────────
def _kinds_install(home):  # noqa: F811
    from myclaw.jobs import store

    recs = [
        _rec("INFO", "ZenSynora v0.8.0 initialised (log_level=INFO, cmd=webui)", ago=5000),
        _rec("INFO", "ZenSynora v0.8.0 initialised (log_level=INFO, cmd=webui)", ago=4970),
        _rec("INFO", "ZenSynora v0.8.0 initialised (log_level=INFO, cmd=doctor)", ago=100),  # CLI: not a restart
        _rec("CRITICAL", "Task 'task_1' FAILED - exceeded 330s timeout", ago=60, module="task_timer"),
        _rec("ERROR", "Tool execution error (shell): boom", ago=50, module="tools",
             exception="Traceback (most recent call last):\n  ValueError: boom"),
        _rec("WARNING", "Rate limit exceeded for browse", ago=40, module="tools"),
        _rec("WARNING", "Config file reloaded", ago=30, module="config"),           # not a failure
        _rec("WARNING", "Something old failed", ago=11 * 3600, module="old"),    # outside 8 h
    ]
    _write_log(home, recs)
    job = store.create_job({"name": "daily research", "prompt": "do it", "agent": "default"})
    ok = store.start_run(job["id"])
    store.finish_run(ok, "ok", output="fine")
    bad = store.start_run(job["id"])
    store.finish_run(bad, "error", error="Model server unreachable")
    return job


def test_error_view_kinds(home):  # noqa: F811
    from myclaw.doctor import report

    _kinds_install(home)
    v = report.error_view(8)
    assert v["kinds"] == {"failures": 3, "jobs": 1, "restarts": 2}
    assert v["job_runs"] == [] and v["restart_events"] == []

    f = report.error_view(8, kind="failures")
    msgs = {g["message"] for g in f["groups"]}
    assert any("FAILED" in m for m in msgs) and any("Rate limit exceeded" in m for m in msgs)
    assert not any("reloaded" in m for m in msgs) and not any("old failed" in m for m in msgs)
    assert len(report.error_view(8, ["CRITICAL"], kind="failures")["groups"]) == 1

    j = report.error_view(8, kind="jobs")
    assert j["groups"] == [] and len(j["job_runs"]) == 1
    run = j["job_runs"][0]
    assert run["job"] == "daily research" and run["status"] == "error" and "unreachable" in run["error"]
    assert report.error_view(8, q="nothing-like-this", kind="jobs")["job_runs"] == []

    r = report.error_view(8, kind="restarts")
    assert [e["command"] for e in r["restart_events"]] == ["webui", "webui"]
    assert r["restart_events"][0]["after_s"] == 30 and r["restart_events"][1]["after_s"] is None
    assert report.error_view(12)["kinds"]["failures"] == 4  # the 11 h old one is inside 12 h


def test_errors_api_kind_and_new_periods(home):  # noqa: F811
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.web import doctor_api, settings_api

    _kinds_install(home)
    app = FastAPI()
    app.include_router(doctor_api.router)
    app.dependency_overrides[settings_api.require_local_or_admin] = lambda: None
    with TestClient(app) as c:
        for hours in (2, 4, 8, 12):
            e = c.get(f"/api/doctor/errors?hours={hours}").json()
            assert e["hours"] == hours and set(e["kinds"]) == {"failures", "jobs", "restarts"}
        assert len(c.get("/api/doctor/errors?hours=8&kind=jobs").json()["job_runs"]) == 1
        assert len(c.get("/api/doctor/errors?hours=8&kind=restarts").json()["restart_events"]) == 2
        assert c.get("/api/doctor/errors?kind=bogus").status_code == 400


def test_every_error_counts_as_a_failure(home):  # noqa: F811
    from myclaw.doctor import report

    _write_log(home, [_rec("ERROR", "Tool execution error (x): unexpected keyword argument 'url'", ago=10),
                      _rec("WARNING", "Config file reloaded", ago=10)])
    v = report.error_view(2, kind="failures")
    assert v["kinds"]["failures"] == 1 and [g["level"] for g in v["groups"]] == ["ERROR"]
