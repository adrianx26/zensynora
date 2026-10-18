"""Diagnostics report: everything needed to understand a problem, in one file.

``build_report()`` collects the doctor checks, grouped warnings/errors from
the log (with tracebacks), program restarts, failed job runs, open change
proposals, a configuration summary and the last log lines. Secrets are never
included: the configuration summary only says *whether* a key or token is
set, and all log text goes through ``logscan.redact``.

``to_markdown`` / ``to_json`` / ``to_zip`` render it (``zensynora doctor
--report``, Web UI → Doctor → Report, ``/api/doctor/report``).
"""

from __future__ import annotations

import io
import json
import os
import platform
import shutil
import sys
import time
import zipfile
from datetime import datetime
from typing import Any

from pydantic import BaseModel, SecretStr

from . import logscan
from .paths import active_release, data_dir, install_base, install_root, log_file, stale_release

MAX_GROUPS = 100
TAIL_LINES = 200


# ── parts ────────────────────────────────────────────────────────────────────
def _install() -> dict[str, Any]:
    lf = log_file()
    try:
        stale = stale_release()
    except Exception:
        stale = None
    return {
        "program_folder": str(install_root()),
        "install_base": str(install_base() or ""),
        "active_release": str(active_release() or ""),
        "git_checkout": (install_root() / ".git").exists(),
        "update_installed_not_running": stale,
        "data_folder": str(data_dir()),
        "log_file": str(lf),
        "log_size_kb": lf.stat().st_size // 1024 if lf.exists() else 0,
    }


def _system() -> dict[str, Any]:
    out: dict[str, Any] = {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "cpu_cores": os.cpu_count(),
    }
    if hasattr(os, "getloadavg"):
        out["load_1m"] = round(os.getloadavg()[0], 2)
    try:
        import psutil

        m = psutil.virtual_memory()
        out["ram_total_gb"] = round(m.total / 1e9, 1)
        out["ram_used_pct"] = m.percent
    except Exception:
        pass
    try:
        target = data_dir() if data_dir().exists() else os.path.expanduser("~")
        out["disk_free_gb"] = round(shutil.disk_usage(target).free / 1e9, 1)
    except OSError:
        pass
    return out


def _secret_set(value: Any) -> bool:
    if isinstance(value, SecretStr):
        return bool(value.get_secret_value())
    if isinstance(value, (list, tuple)):
        return any(_secret_set(v) for v in value)
    if isinstance(value, dict):
        return any(_secret_set(v) for v in value.values())
    if isinstance(value, BaseModel):
        return any(_secret_set(getattr(value, k, None)) for k in type(value).model_fields)
    return False


def _config_summary() -> dict[str, Any]:
    """What is configured — never the keys or tokens themselves."""
    from ..config import CONFIG_FILE, load_config

    try:
        cfg = load_config()
    except BaseException as e:  # invalid config raises SystemExit
        return {"error": logscan.redact(str(e))[:300]}
    providers = []
    for name in type(cfg.providers).model_fields:
        if name == "custom":
            continue
        p = getattr(cfg.providers, name)
        entry: dict[str, Any] = {"name": name, "key_set": _secret_set(p)}
        if getattr(p, "base_url", ""):
            entry["url"] = logscan.redact(str(p.base_url))
        providers.append(entry)
    for cp in cfg.providers.custom:
        providers.append({"name": cp.name, "kind": cp.kind, "url": logscan.redact(cp.base_url),
                          "key_set": _secret_set(cp)})
    d = cfg.agents.defaults
    agents = [{"name": "default", "provider": d.provider, "model": d.model,
               "skills": len(getattr(d, "skills", []) or [])}]
    agents += [{"name": a.name, "provider": a.provider or d.provider, "model": a.model,
                "skills": len(a.skills or [])} for a in cfg.agents.named]
    tg, wa = cfg.channels.telegram, cfg.channels.whatsapp
    return {
        "config_file": str(CONFIG_FILE),
        "config_file_exists": CONFIG_FILE.exists(),
        "providers": providers,
        "agents": agents,
        "rotation": cfg.agents.rotation.enabled,
        "channels": {
            "telegram": {"enabled": tg.enabled, "token_set": _secret_set(tg.token),
                         "allowed_users": len(tg.allowFrom), "run_in_webui": getattr(tg, "run_in_webui", None)},
            "whatsapp": {"enabled": wa.enabled, "token_set": _secret_set(wa.access_token)},
        },
        "mcp": {"enabled": cfg.mcp.enabled,
                "servers": {n: os.path.basename(s.command or "") for n, s in cfg.mcp.servers.items()}},
        "knowledge": cfg.knowledge.enabled,
        "swarm": cfg.swarm.enabled,
        "medic": getattr(cfg.medic, "enabled", None),
        "self_update": getattr(getattr(cfg.medic, "self_update", None), "enabled", None),
        "admin_key_set": _secret_set(cfg.security.admin_api_key),
    }


def _job_runs(hours: float) -> list[dict[str, Any]]:
    from ..jobs import store

    if not store.db_path().exists():
        return []
    cutoff = time.time() - hours * 3600
    out = []
    for job in store.list_jobs():
        for run in store.list_runs(job["id"], limit=50):
            if (run.get("started") or 0) < cutoff:
                continue
            if run.get("status") in ("error", "timeout", "interrupted"):
                out.append({"job": job["name"], "run": run.get("id"), "status": run["status"],
                            "started": _fmt_ts(run.get("started")),
                            "error": logscan.redact(str(run.get("error") or ""))[:500]})
    return sorted(out, key=lambda r: r["started"], reverse=True)


def _proposals() -> list[dict[str, Any]]:
    from ..selfupdate import list_proposals

    return [{"id": p["id"], "title": p["title"], "status": p["status"], "risk": p.get("risk")}
            for p in list_proposals() if p["status"] in ("pending", "approved", "applying", "failed")]


def _lessons_count() -> int:
    from ..selfupdate import LESSONS_NOTE

    path = data_dir() / "knowledge" / "default" / f"{LESSONS_NOTE}.md"
    if not path.exists():
        return 0
    from ..knowledge.parser import parse_note

    return sum(1 for o in parse_note(path).observations if o.category == "lesson")


def _fmt_ts(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts)).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception as e:  # one broken part must not break the report
        return default if default is not None else {"error": logscan.redact(str(e))[:300]}


def error_view(hours: float = 24, levels: list[str] | None = None, q: str = "",
               samples: int = 5) -> dict[str, Any]:
    """Grouped warnings/errors for the Errors viewer and the report."""
    sc = logscan.scan(hours, max_bytes=30_000_000)
    entries = sc.problems
    if levels:
        wanted = {lv.upper() for lv in levels}
        entries = [e for e in entries if e.level in wanted]
    if q:
        ql = q.lower()
        entries = [e for e in entries if ql in e.message.lower() or ql in e.module.lower()
                   or ql in e.exception.lower()]
    groups = logscan.group(entries, samples=samples)
    restarts = logscan.restart_stats(sc.starts)
    restarts["last"] = _fmt_ts(restarts["last"]) if restarts["last"] else None
    return {"hours": hours, "counts": sc.counts(), "groups": groups[:MAX_GROUPS],
            "total_groups": len(groups), "restarts": restarts, "log_exists": sc.exists,
            "truncated": sc.truncated, "lines_read": sc.lines}


# ── the report ───────────────────────────────────────────────────────────────
def build_report(hours: float = 24, include_log_tail: bool = True, offline: bool = False) -> dict[str, Any]:
    from .. import __version__
    from .registry import run_checks

    doctor = run_checks(offline=offline).to_dict()
    for r in doctor["results"]:
        r["detail"], r["hint"] = logscan.redact(r["detail"]), logscan.redact(r["hint"])
    errors = error_view(hours, samples=3)
    report: dict[str, Any] = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "version": __version__,
        "hours": hours,
        "install": _safe(_install),
        "system": _safe(_system),
        "doctor": doctor,
        "errors": errors,
        "job_runs": _safe(_job_runs, hours, default=[]),
        "open_proposals": _safe(_proposals, default=[]),
        "medic_lessons": _safe(_lessons_count, default=0),
        "config": _safe(_config_summary),
    }
    if include_log_tail:
        report["log_tail"] = logscan.tail_lines(TAIL_LINES)
    report["summary"] = {
        "checks": doctor["summary"],
        "overall": doctor["overall"],
        "errors": errors["counts"],
        "error_groups": errors["total_groups"],
        "restarts": errors["restarts"]["starts"],
        "crash_loop": errors["restarts"]["max_in_window"] > 10,
        "failed_jobs": len(report["job_runs"]) if isinstance(report["job_runs"], list) else 0,
        "open_proposals": len(report["open_proposals"]) if isinstance(report["open_proposals"], list) else 0,
    }
    return report


# ── renderers ────────────────────────────────────────────────────────────────
def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, default=str)


def _cell(v: Any) -> str:
    return str(v if v is not None else "").replace("|", "\\|").replace("\n", " ")


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    if not rows:
        return ["_none_", ""]
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return out + [""]


def to_markdown(report: dict[str, Any]) -> str:
    s = report.get("summary", {})
    checks = s.get("checks", {})
    errs = s.get("errors", {})
    md = ["# ZenSynora diagnostics report", "",
          f"Version {report.get('version')} · generated {report.get('generated_at')} · "
          f"last {report.get('hours')} h", "",
          "## Summary", "",
          f"- Health: **{str(s.get('overall', '?')).upper()}** — {checks.get('ok', 0)} ok, "
          f"{checks.get('warn', 0)} warnings, {checks.get('fail', 0)} failures, {checks.get('skip', 0)} skipped",
          f"- Log: {errs.get('CRITICAL', 0)} critical, {errs.get('ERROR', 0)} errors, "
          f"{errs.get('WARNING', 0)} warnings ({s.get('error_groups', 0)} kinds)",
          f"- Restarts: {s.get('restarts', 0)}" + (" — **crash loop**" if s.get("crash_loop") else ""),
          f"- Failed job runs: {s.get('failed_jobs', 0)} · open change proposals: {s.get('open_proposals', 0)}"
          f" · medic lessons: {report.get('medic_lessons', 0)}", ""]

    md += ["## Doctor checks", ""]
    icons = {"ok": "✔", "warn": "!", "fail": "✘", "skip": "–"}
    order = {"fail": 0, "warn": 1, "ok": 2, "skip": 3}
    results = sorted(report.get("doctor", {}).get("results", []), key=lambda r: (order.get(r["status"], 9), r["category"]))
    md += _table(["", "Check", "Area", "Result", "How to fix"],
                 [[icons.get(r["status"], ""), r["title"], r["category"], r["detail"],
                   r["hint"] if r["status"] in ("warn", "fail") else ""] for r in results])

    e = report.get("errors", {})
    r = e.get("restarts", {})
    md += ["## Restarts", "",
           f"{r.get('starts', 0)} start(s) of the service in the period; at most {r.get('max_in_window', 0)} "
           f"in {r.get('window_min', 15)} minutes; last start: {r.get('last') or '—'}"
           + (f"; typical gap {r['typical_gap_s']} s" if r.get("typical_gap_s") else "") + ".", ""]

    md += ["## Errors and warnings", ""]
    if e.get("truncated"):
        md += ["_Only the newest part of the log was read._", ""]
    groups = e.get("groups", [])
    md += _table(["Level", "Count", "First", "Last", "Module", "Message"],
                 [[g["level"], g["count"], g["first"][:19], g["last"][:19], g["module"], g["message"][:200]]
                  for g in groups])
    for i, g in enumerate(groups[:30], 1):
        md += [f"### {i}. {g['level']} ×{g['count']} — {g['message'][:120]}", "", "```"]
        md += list(g["samples"])
        md += ["```", ""]

    md += ["## Failed job runs", ""]
    md += _table(["Started", "Job", "Status", "Error"],
                 [[j["started"], j["job"], j["status"], j["error"][:200]] for j in report.get("job_runs") or []])
    md += ["## Open change proposals", ""]
    md += _table(["Id", "Title", "Status", "Risk"],
                 [[p["id"], p["title"], p["status"], p.get("risk")] for p in report.get("open_proposals") or []])

    cfg = report.get("config", {})
    md += ["## Configuration (no secrets)", ""]
    if "error" in cfg:
        md += [f"Could not read the configuration: {cfg['error']}", ""]
    else:
        md += _table(["Provider", "Key set", "URL"],
                     [[p["name"], "yes" if p.get("key_set") else "no", p.get("url", "")] for p in cfg.get("providers", [])])
        md += _table(["Agent", "Provider", "Model", "Skills"],
                     [[a["name"], a["provider"], a["model"], a["skills"]] for a in cfg.get("agents", [])])
        ch = cfg.get("channels", {})
        tg = ch.get("telegram", {})
        md += [f"- Telegram: {'on' if tg.get('enabled') else 'off'} (token set: {'yes' if tg.get('token_set') else 'no'}, "
               f"{tg.get('allowed_users', 0)} allowed user(s))",
               f"- WhatsApp: {'on' if ch.get('whatsapp', {}).get('enabled') else 'off'}",
               f"- MCP: {'on' if cfg.get('mcp', {}).get('enabled') else 'off'}, servers: "
               f"{', '.join(f'{k} ({v})' for k, v in cfg.get('mcp', {}).get('servers', {}).items()) or 'none'}",
               f"- Knowledge: {'on' if cfg.get('knowledge') else 'off'} · Swarm: {'on' if cfg.get('swarm') else 'off'}"
               f" · Medic: {'on' if cfg.get('medic') else 'off'} · Self-update: {'on' if cfg.get('self_update') else 'off'}"
               f" · Admin key set: {'yes' if cfg.get('admin_key_set') else 'no'}", ""]

    md += ["## System", ""]
    md += [f"- {k}: {v}" for k, v in (report.get("system") or {}).items()] + [""]
    md += ["## Installation", ""]
    md += [f"- {k}: {v}" for k, v in (report.get("install") or {}).items()] + [""]

    if report.get("log_tail"):
        md += [f"## Last {len(report['log_tail'])} log lines", "", "```"] + report["log_tail"] + ["```", ""]
    return "\n".join(md)


def filename(ext: str, when: datetime | None = None) -> str:
    return f"zensynora-report-{(when or datetime.now()).strftime('%Y%m%d-%H%M')}.{ext}"


def to_zip(report: dict[str, Any]) -> bytes:
    """report.md + report.json + the redacted log tail."""
    buf = io.BytesIO()
    tail = report.get("log_tail") or logscan.tail_lines(TAIL_LINES)
    slim = {k: v for k, v in report.items() if k != "log_tail"}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.md", to_markdown(slim))
        z.writestr("report.json", to_json(slim))
        z.writestr("zensynora.log.tail.txt", "\n".join(tail) + "\n")
    return buf.getvalue()


def write(path: str, hours: float = 24, offline: bool = False) -> str:
    """Write the report to ``path`` (.json, .zip or Markdown) and return the path."""
    from pathlib import Path

    p = Path(path).expanduser()
    if p.is_dir():
        p = p / filename("md")
    rep = build_report(hours=hours, offline=offline)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix == ".json":
        p.write_text(to_json(rep), encoding="utf-8")
    elif p.suffix == ".zip":
        p.write_bytes(to_zip(rep))
    else:
        p.write_text(to_markdown(rep), encoding="utf-8")
    return str(p.resolve())
