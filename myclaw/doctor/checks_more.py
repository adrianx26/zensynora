"""More doctor checks: crash loops, risky custom tools, knowledge index, skills,
MCP servers and the chat database. Registered like the ones in ``checks.py``;
all are offline and fast (they read local files only)."""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import time
from pathlib import Path

from . import logscan
from .paths import data_dir
from .registry import FAIL, OK, SKIP, WARN, check, fixer

RESTART_LIMIT = 10        # more starts than this in 15 minutes = crash loop
WARNING_REPEAT = 5        # a warning seen this often in 24 h is reported
CRITICAL_FAIL = 3         # this many CRITICAL lines in 24 h = failure


def log_scan(ctx, hours: float = 24) -> logscan.Scan:
    """The log scan, shared by the checks of one doctor run."""
    key = f"logscan:{hours}"
    if key not in ctx.cache:
        ctx.cache[key] = logscan.scan(hours, max_bytes=6_000_000)
    return ctx.cache[key]  # type: ignore[return-value]


def _ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=3)
    con.row_factory = sqlite3.Row
    return con


# ── runtime ──────────────────────────────────────────────────────────────────
@check("restart_loop", "Restarts (crash loop)", "runtime")
def _restart_loop(ctx):
    sc = log_scan(ctx)
    if not sc.exists:
        return SKIP, "no log file yet"
    st = logscan.restart_stats(sc.starts)
    detail = f"{st['starts']} start(s) in 24 h"
    if st["max_in_window"] > RESTART_LIMIT:
        gap = f", about every {st['typical_gap_s']:.0f} s" if st["typical_gap_s"] else ""
        hint = ("ZenSynora keeps crashing and being restarted. See why: sudo systemctl status myclaw; "
                "journalctl -u myclaw -n 100 --no-pager. The errors below are often the cause.")
        if st["in_last_window"] > RESTART_LIMIT:
            return FAIL, f"crash loop now: {st['in_last_window']} starts in the last {st['window_min']} min{gap}", hint
        return WARN, f"{detail}; up to {st['max_in_window']} in {st['window_min']} min{gap}", hint
    return OK, detail


# ── tools ────────────────────────────────────────────────────────────────────
_PLACEHOLDER = re.compile(
    r"YOUR_[A-Z0-9_]*_HERE|YOUR_(?:BOT_)?(?:TOKEN|API_KEY|KEY|SECRET|CHAT_ID|PASSWORD)\b|"
    r"[\"']x{3,}[\"']|<(?:token|api[_-]?key|bot[_-]?token|secret|password)>|[\"']REPLACE_ME[\"']",
    re.IGNORECASE)


def toolbox_dir() -> Path:
    return data_dir() / "TOOLBOX"


def risky_tools() -> list[dict]:
    """Enabled custom tools with placeholder credentials or hard-coded secrets."""
    reg_file = toolbox_dir() / "toolbox_registry.json"
    try:
        registry = json.loads(reg_file.read_text(encoding="utf-8")) if reg_file.exists() else {}
    except (OSError, ValueError):
        registry = {}
    out = []
    seen = set()
    entries = list(registry.items()) if isinstance(registry, dict) else []
    entries += [(p.stem, {"path": str(p)}) for p in sorted(toolbox_dir().glob("*.py"))] if toolbox_dir().is_dir() else []
    for name, info in entries:
        info = info if isinstance(info, dict) else {"path": str(info)}
        path = Path(info.get("path") or toolbox_dir() / f"{name}.py")
        if name in seen or not path.is_file():
            continue
        seen.add(name)
        if not info.get("enabled", True):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:200_000]
        except OSError:
            continue
        problems = []
        if _PLACEHOLDER.search(text):
            problems.append("placeholder credentials")
        if logscan.looks_secret(text):
            problems.append("hard-coded secret")
        if problems:
            out.append({"name": name, "path": str(path), "problems": problems})
    return out


@check("custom_tools", "Custom tools (TOOLBOX)", "security")
def _custom_tools(ctx):
    if not toolbox_dir().is_dir():
        return OK, "none"
    bad = risky_tools()
    if bad:
        names = ", ".join(f"{b['name']} ({' + '.join(b['problems'])})" for b in bad)
        return WARN, names, (
            "These agent-made tools cannot work as written or hold a secret in plain text. "
            "Disable them (ask an agent: disable_skill('" + bad[0]["name"] + "'), or set \"enabled\": false in "
            "~/.myclaw/TOOLBOX/toolbox_registry.json) and use the built-in tools instead, e.g. "
            "send_telegram_message with the bot token from Settings → Channels.")
    n = len(list(toolbox_dir().glob("*.py")))
    return OK, f"{n} tool(s), no placeholder credentials or secrets found"


# ── data ─────────────────────────────────────────────────────────────────────
# 2026-10-25 (KB phase 6): the knowledge checks live in checks_knowledge.py
# (every knowledge base, the configured knowledge folder, checksums of a
# sample, waiting links, conflicts, embeddings, distillation, watcher, imports,
# trash). "knowledge_index" keeps its id, title and fix.


# ── configuration ────────────────────────────────────────────────────────────
@check("skills", "Agent skills", "configuration")
def _skills(ctx):
    from .. import agent_skills

    cfg = ctx.get_config()
    names = {s["name"] for s in agent_skills.list_skills()}
    broken = []
    for p in agent_skills._files(agent_skills.user_dir()):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            broken.append(f"{p.name}: unreadable ({e.__class__.__name__})")
            continue
        sk = agent_skills.parse(text, p.parent.name if p.name == "SKILL.md" else p.stem)
        if not sk["name"]:
            broken.append(f"{p}: no name")
        elif not sk["body"]:
            broken.append(f"{sk['name']}: empty instructions")
    agents = ["default"] + [a.name for a in cfg.agents.named]
    missing = []
    for a in agents:
        for s in agent_skills.skills_for(cfg, a):
            if agent_skills.slug(s) not in names:
                missing.append(f"@{a} → {s}")
    problems = ([f"unknown skill(s): {', '.join(missing)}"] if missing else []) + \
               ([f"broken: {', '.join(broken)}"] if broken else [])
    if problems:
        return WARN, "; ".join(problems), \
            "Fix the skill on the Skills page, or remove it from the agent in Settings → Agents."
    return OK, f"{len(names)} skill(s) available"


# 2026-10-28 (rules re-review): agent-made tools run in a locked-down process
@check("tool_sandbox", "Tool sandbox", "security")
def _tool_sandbox(ctx):
    from ..tools import tool_sandbox, toolbox

    skipped = dict(toolbox.SKIPPED_TOOLS)
    reg_path = toolbox.TOOLBOX_REG
    if reg_path.exists():
        try:
            reg = json.loads(reg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            reg = {}
        for name, info in (reg.items() if isinstance(reg, dict) else []):
            if not isinstance(info, dict) or not info.get("enabled", True):
                continue
            path = Path(info.get("path", ""))
            if not path.exists():
                continue
            func, problem = toolbox.load_toolbox_tool(name, path, check_code=info.get("author") != "user")
            if func is None:
                skipped[name] = str(problem)
    if not reg_path.exists() and not skipped:
        return OK, "no agent-made tools"
    st = tool_sandbox.status()
    problems = []
    if not st["ok"]:
        problems.append(f"the sandbox does not start: {st['error'][:200]}")
    if skipped:
        problems.append("not loaded: " + "; ".join(f"{n} ({r[:80]})" for n, r in sorted(skipped.items())[:5]))
    if problems:
        return WARN, "; ".join(problems), \
            "Ask an agent to rewrite the tool with register_tool, or disable it (Tools page lists them)."
    return OK, f"agent-made tools run in a separate process; kernel lock-down (Landlock): {st['landlock']}"


@check("shell_commands", "Shell commands", "security")
def _shell_commands(ctx):
    from ..tools.core import refused_command

    allowed = list(ctx.get_config().security.allowed_commands or [])
    bad = sorted({c for c in allowed if refused_command(c)})
    if bad:
        return WARN, f"not used (interpreters/shells): {', '.join(bad)}", \
            "Remove them from Settings → Security → shell commands; they could run any program."
    return OK, f"{len(allowed)} allowed command(s)"


# 2026-10-27: the user's rules and the agents' proposals (myclaw/rules.py)
@check("rules", "Rules", "configuration")
def _rules(ctx):
    from .. import rules

    items = rules.list_rules()
    active = sum(1 for r in items if r["enabled"] and not rules.needs_review(r))
    off = sum(1 for r in items if not r["enabled"])
    pending = rules.list_proposals("pending")
    detail = f"{active} active" + (f" ({off} switched off)" if off else "") + \
        f", {len(pending)} proposal(s) waiting"
    problems, hints = [], []
    review = [r["slug"] for r in items if rules.needs_review(r)]
    if review:
        problems.append(f"changed outside ZenSynora, not used until you review: {', '.join(review)}")
        hints.append("Review them in the Web UI → Rules (Keep this version / Restore approved version) or "
                     "with zensynora rules accept SLUG.")
    skipped = rules.skipped_files()
    if skipped:
        problems.append("skipped: " + "; ".join(f"{n} ({why})" for n, why in skipped[:5]))
        hints.append("Rule files are named like answer-in-romanian.md (lowercase letters, digits, dashes).")
    stale = [p for p in pending if rules.waiting_since_days(p) > rules.STALE_DAYS]
    if stale:
        ids = ", ".join(f"#{p['id']}" for p in stale)
        problems.append(f"{len(stale)} waiting more than {rules.STALE_DAYS} days ({ids})")
        hints.append("Approve or reject them in the Web UI → Rules, or with zensynora rules approve/reject ID.")
    if problems:
        return WARN, f"{detail}; " + "; ".join(problems), " ".join(hints)
    return OK, detail


@check("mcp_servers", "MCP servers", "configuration")
def _mcp_servers(ctx):
    mcp = ctx.get_config().mcp
    if not mcp.servers:
        return OK, "none configured"
    if not mcp.enabled:
        return OK, f"{len(mcp.servers)} configured, MCP turned off"
    bad = []
    for name, srv in mcp.servers.items():
        cmd = (srv.command or "").strip()
        if not cmd:
            bad.append(f"{name}: no command")
        elif not (shutil.which(cmd) or (Path(cmd).expanduser().is_file())):
            bad.append(f"{name}: '{Path(cmd).name}' not found")
    if bad:
        return WARN, "; ".join(bad), \
            "Install the program (for npx: Node.js; for uvx: uv) or fix the command in Settings → MCP."
    return OK, f"{len(mcp.servers)} server(s), commands found"


# ── chat ─────────────────────────────────────────────────────────────────────
@check("chat_db", "Chat channels", "data")
def _chat_db(ctx):
    db = data_dir() / "chat.db"
    if not db.exists():
        return OK, "no chats yet"
    cfg = ctx.get_config()
    known = {"default"} | {a.name.lower() for a in cfg.agents.named}
    con = _ro(db)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"channels", "messages"} <= tables:
            return OK, "empty"
        n_ch = con.execute("SELECT COUNT(*) FROM channels").fetchone()[0]
        n_msg = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        stuck = con.execute("SELECT COUNT(*) FROM messages WHERE role = 'agent' AND meta LIKE '%\"streaming\": true%' "
                            "AND created < ?", (time.time() - 600,)).fetchone()[0]
        ghosts = []
        for r in con.execute("SELECT name, members FROM channels WHERE kind = 'channel'"):
            try:
                members = json.loads(r["members"] or "[]")
            except ValueError:
                continue
            ghosts += [f"#{r['name']}: @{m}" for m in members if str(m).lower() not in known]
    finally:
        con.close()
    detail = f"{n_ch} channel(s), {n_msg} message(s)"
    problems = []
    if stuck:
        problems.append(f"{stuck} answer(s) left unfinished by a stop")
    if ghosts:
        problems.append("members that are no longer agents: " + ", ".join(ghosts[:6]))
    if problems:
        return WARN, detail + "; " + "; ".join(problems), \
            "Restart the Web UI: it closes unfinished answers and removes deleted agents from channels."
    return OK, detail


__all__ = ["log_scan", "risky_tools", "CRITICAL_FAIL", "WARNING_REPEAT"]
