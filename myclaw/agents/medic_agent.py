"""
Medic Agent — health monitoring, integrity protection and recovery tools.

Rewritten in 0.7.0. The previous module was a placeholder: two "health" tools
returned hard-coded text ("All core services are running") and 12 of the 15
medic tools advertised to the LLM did not exist (calls failed and their tool
schemas were broken). Every tool below now does real work, mostly by
delegating to ``myclaw.doctor`` (the same checks as ``zensynora doctor`` and
the Web UI Doctor page).

Import stays cheap: heavy modules are imported inside the functions.
"""

from __future__ import annotations

import ast
import json
import logging
import threading
import time
from collections import deque
from pathlib import Path

try:
    import psutil

    PSUTIL_AVAILABLE = True
except ImportError:  # pragma: no cover - psutil is a core dependency
    PSUTIL_AVAILABLE = False

logger = logging.getLogger(__name__)

# Injected by the gateway (set_config); falls back to load_config() when unset.
_config = None


def set_config(config):
    """Injects the application config into the medic agent module."""
    global _config
    _config = config


def _cfg():
    if _config is not None:
        return _config
    from ..config import load_config

    return load_config()


# ── Health ───────────────────────────────────────────────────────────────────
def check_memory_usage(threshold_percent: float = 85.0) -> str:
    """
    Checks current system memory (RAM) usage and logs a warning if it exceeds a threshold.

    Args:
        threshold_percent (float): The memory usage percentage threshold to trigger a warning. Defaults to 85.0.

    Returns:
        str: A status message indicating memory usage and whether it's high.
    """
    if not PSUTIL_AVAILABLE:
        return "Cannot check memory usage: 'psutil' library is not installed. Please run 'pip install psutil'."

    memory = psutil.virtual_memory()
    usage_percent = memory.percent
    message = (f"Current memory usage: {usage_percent:.1f}% (Total: {memory.total / (1024**3):.2f} GB, "
               f"Used: {memory.used / (1024**3):.2f} GB).")
    if usage_percent > threshold_percent:
        warning_message = (f"HIGH MEMORY USAGE DETECTED: {usage_percent:.1f}% exceeds threshold of "
                           f"{threshold_percent:.1f}%.")
        logger.warning(warning_message)
        return f"WARNING: {warning_message}"
    logger.info(message)
    return f"OK: {message}"


def check_system_health(offline: bool = False) -> str:
    """
    Check overall system health: Python packages, provider libraries, config,
    LLM server reachability, databases, disk, memory, recent errors.

    Args:
        offline (bool): Skip checks that need the network (LLM server). Defaults to False.

    Returns:
        str: One-line verdict followed by the problems found (if any).
    """
    from ..doctor import run_checks

    report = run_checks(offline=offline)
    s = report.summary
    head = (f"{report.overall.upper()}: {s['ok']} checks ok, {s['warn']} warnings, "
            f"{s['fail']} failures")
    problems = [f"- [{r.status}] {r.title}: {r.detail}" + (f" → {r.hint}" if r.hint else "")
                for r in report.results if r.status in ("warn", "fail")]
    return head + ("\n" + "\n".join(problems) if problems else "\nNo problems found.")


def get_health_report(offline: bool = False, as_json: bool = False) -> str:
    """
    Get the full health report with every check, grouped by category.

    Args:
        offline (bool): Skip network checks. Defaults to False.
        as_json (bool): Return JSON instead of text. Defaults to False.

    Returns:
        str: The formatted report.
    """
    from ..doctor import run_checks

    report = run_checks(offline=offline)
    return json.dumps(report.to_dict(), indent=2) if as_json else report.to_text()


# ── Integrity & recovery ─────────────────────────────────────────────────────
def scan_files(file_paths: str = "") -> str:
    """
    Scan files and record their hashes as the integrity baseline.

    Args:
        file_paths (str): Comma-separated paths relative to the install folder. Empty = all code files.

    Returns:
        str: How many files were recorded.
    """
    from ..doctor import integrity

    paths = [p.strip() for p in file_paths.split(",") if p.strip()] or None
    try:
        out = integrity.scan(paths)
    except (ValueError, FileNotFoundError) as e:
        return f"ERROR: {e}"
    return f"Recorded {out['files']} file hash(es) for version {out['version']} in {out['baseline']}."


def verify_file_integrity(file_path: str = "") -> str:
    """
    Verify file integrity against recorded hashes.

    Args:
        file_path (str): One file relative to the install folder. Empty = all tracked files.

    Returns:
        str: OK or the list of modified/missing files.
    """
    from ..doctor import integrity

    if not integrity.hash_check_enabled():
        return "Hash checking is disabled (enable_hash_check to turn it on)."
    try:
        res = integrity.verify(file_path or None)
    except ValueError as e:
        return f"ERROR: {e}"
    if res["status"] in ("no-baseline", "stale-baseline"):
        return f"NO BASELINE: {res['note']}"
    if res["status"] == "ok":
        return f"OK: {res['checked']} file(s) match the baseline."
    lines = [f"CHANGED: {len(res['modified'])} modified, {len(res['missing'])} missing"]
    lines += [f"- modified: {m}" for m in res["modified"]] + [f"- missing: {m}" for m in res["missing"]]
    return "\n".join(lines)


def enable_hash_check(enabled: bool = True) -> str:
    """
    Enable or disable hash (integrity) checking.

    Args:
        enabled (bool): True to enable, False to disable. Defaults to True.

    Returns:
        str: The new setting.
    """
    from ..doctor import integrity

    state = integrity.load_state()
    state["hash_check"] = bool(enabled)
    integrity.save_state(state)
    return f"Hash checking {'enabled' if enabled else 'disabled'}."


def create_backup(file_path: str) -> str:
    """
    Create a local backup copy of a file from the installation.

    Args:
        file_path (str): Path relative to the install folder (e.g. myclaw/agent.py).

    Returns:
        str: Where the backup was written.
    """
    from ..doctor import integrity

    try:
        dest = integrity.backup_file(file_path)
    except (ValueError, FileNotFoundError) as e:
        return f"ERROR: {e}"
    return f"Backup created: {dest}"


def list_backups() -> str:
    """
    List all local file backups made by the medic.

    Returns:
        str: One line per backed-up file (newest first).
    """
    from ..doctor import integrity

    items = integrity.list_file_backups()
    if not items:
        return "No file backups yet."
    return "\n".join(f"{b['stamp']}  {b['file']}  ({b['bytes']} bytes)" for b in items[:100])


def recover_file(file_path: str, allow_main: bool = False) -> str:
    """
    Recover a corrupted or missing file: newest local backup first, otherwise
    GitHub at the tag of the installed version.

    Args:
        file_path (str): Path relative to the install folder.
        allow_main (bool): Fall back to the latest main branch if no version tag exists. Defaults to False.

    Returns:
        str: What was restored and from where.
    """
    from ..doctor import integrity

    try:
        return integrity.recover(file_path, allow_main=allow_main)
    except ValueError as e:
        return f"ERROR: {e}"


# ── Code checks ──────────────────────────────────────────────────────────────
_RISKY_CALLS = {"eval", "exec", "compile", "__import__"}
_RISKY_ATTRS = {("os", "system"), ("os", "popen"), ("shutil", "rmtree"), ("os", "remove")}


def detect_errors_in_file(file_path: str) -> str:
    """
    Detect syntax errors in a Python, JSON, YAML or TOML file.

    Args:
        file_path (str): Path to the file.

    Returns:
        str: OK or the error with its line number.
    """
    p = Path(file_path).expanduser()
    if not p.is_file():
        return f"ERROR: {file_path} does not exist"
    text = p.read_text(encoding="utf-8", errors="replace")
    suffix = p.suffix.lower()
    try:
        if suffix == ".py":
            ast.parse(text, filename=str(p))
        elif suffix == ".json":
            json.loads(text)
        elif suffix in (".yaml", ".yml"):
            import yaml

            yaml.safe_load(text)
        elif suffix == ".toml":
            import tomllib

            tomllib.loads(text)
        else:
            return f"SKIPPED: no checker for '{suffix or 'no extension'}' files"
    except SyntaxError as e:
        return f"SYNTAX ERROR in {p.name} line {e.lineno}: {e.msg}"
    except Exception as e:
        return f"PARSE ERROR in {p.name}: {e}"
    return f"OK: no syntax errors in {p.name}"


def validate_modification(file_path: str, new_content: str) -> str:
    """
    Validate a proposed code modification before it is written.

    Args:
        file_path (str): File the change is meant for.
        new_content (str): The full proposed new content.

    Returns:
        str: VALID, VALID WITH WARNINGS, or INVALID with reasons.
    """
    errors, warnings = [], []
    if len(new_content) > 1_000_000:
        errors.append("content larger than 1 MB")
    if file_path.endswith(".py"):
        try:
            tree = ast.parse(new_content)
        except SyntaxError as e:
            errors.append(f"syntax error line {e.lineno}: {e.msg}")
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    fn = node.func
                    if isinstance(fn, ast.Name) and fn.id in _RISKY_CALLS:
                        warnings.append(f"line {node.lineno}: uses {fn.id}()")
                    elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                            and (fn.value.id, fn.attr) in _RISKY_ATTRS:
                        warnings.append(f"line {node.lineno}: uses {fn.value.id}.{fn.attr}()")
                    for kw in node.keywords:
                        if kw.arg == "shell" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                            warnings.append(f"line {node.lineno}: shell=True")
    elif file_path.endswith(".json"):
        try:
            json.loads(new_content)
        except ValueError as e:
            errors.append(f"invalid JSON: {e}")
    if errors:
        return "INVALID: " + "; ".join(errors)
    if warnings:
        return "VALID WITH WARNINGS: " + "; ".join(warnings[:10])
    return "VALID"


def check_file_virustotal(file_path: str, api_key: str = "") -> str:
    """
    Check a file against VirusTotal by its SHA-256 (the file is not uploaded).

    Args:
        file_path (str): Path to the file.
        api_key (str): VirusTotal API key; defaults to medic.virustotal_api_key in config.

    Returns:
        str: Detection summary, or why the check could not run.
    """
    import hashlib

    import httpx

    p = Path(file_path).expanduser()
    if not p.is_file():
        return f"ERROR: {file_path} does not exist"
    key = api_key or _cfg().medic.virustotal_api_key.get_secret_value()
    if not key:
        return "No VirusTotal API key: set medic.virustotal_api_key in Settings/config or pass api_key."
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    try:
        r = httpx.get(f"https://www.virustotal.com/api/v3/files/{digest}",
                      headers={"x-apikey": key}, timeout=20)
    except httpx.HTTPError as e:
        return f"Could not reach VirusTotal: {e}"
    if r.status_code == 404:
        return f"UNKNOWN: VirusTotal has no record of {p.name} (sha256 {digest[:16]}…)."
    if r.status_code == 401:
        return "VirusTotal rejected the API key."
    if r.status_code >= 400:
        return f"VirusTotal error HTTP {r.status_code}"
    stats = r.json().get("data", {}).get("attributes", {}).get("last_analysis_stats", {})
    bad = stats.get("malicious", 0) + stats.get("suspicious", 0)
    verdict = "DANGER" if stats.get("malicious", 0) else "SUSPICIOUS" if bad else "CLEAN"
    return f"{verdict}: {stats.get('malicious', 0)} malicious, {stats.get('suspicious', 0)} suspicious, " \
           f"{stats.get('harmless', 0)} harmless, {stats.get('undetected', 0)} undetected ({p.name})."


# ── Task analytics ───────────────────────────────────────────────────────────
def _tasks_file() -> Path:
    from ..doctor.paths import medic_dir

    return medic_dir() / "tasks.jsonl"


def record_task_execution(task_name: str, success: bool = True, duration_seconds: float = 0.0,
                          error: str = "") -> str:
    """
    Record a task execution for analytics.

    Args:
        task_name (str): Short name of the task.
        success (bool): Whether it succeeded. Defaults to True.
        duration_seconds (float): How long it took. Defaults to 0.
        error (str): Error message if it failed.

    Returns:
        str: Confirmation.
    """
    rec = {"ts": time.time(), "task": task_name[:200], "success": bool(success),
           "duration": float(duration_seconds), "error": (error or "")[:500]}
    with open(_tasks_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return f"Recorded '{task_name}' ({'success' if success else 'failure'})."


def get_task_analytics(days: int = 7) -> str:
    """
    Get task execution analytics: success rate, average duration, top failures.

    Args:
        days (int): How many days back to include. Defaults to 7.

    Returns:
        str: Summary.
    """
    f = _tasks_file()
    if not f.exists():
        return "No task executions recorded yet."
    cutoff = time.time() - days * 86400
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("ts", 0) >= cutoff:
            rows.append(r)
    if not rows:
        return f"No task executions in the last {days} day(s)."
    ok = sum(1 for r in rows if r["success"])
    avg = sum(r["duration"] for r in rows) / len(rows)
    by_task = {}
    for r in rows:
        t = by_task.setdefault(r["task"], [0, 0])
        t[0] += 1
        t[1] += 0 if r["success"] else 1
    worst = sorted(by_task.items(), key=lambda kv: kv[1][1], reverse=True)[:5]
    lines = [f"Last {days} day(s): {len(rows)} tasks, {ok / len(rows):.0%} succeeded, avg {avg:.1f}s."]
    lines += [f"- {name}: {n} runs, {fails} failed" for name, (n, fails) in worst if fails]
    return "\n".join(lines)


# ── Infinite-loop prevention ─────────────────────────────────────────────────
_WINDOW_SECONDS = 60
_calls: deque = deque()
_calls_lock = threading.Lock()


def prevent_infinite_loop(record: bool = True) -> str:
    """
    Get (and update) the status of infinite-loop prevention: blocks agent
    processing when more than medic.max_loop_iterations requests happen
    within one minute (a runaway loop).

    Args:
        record (bool): Count this call. The agent router passes True. Defaults to True.

    Returns:
        str: "OK: n/limit …" or "Execution limit reached: …".
    """
    try:
        limit = int(_cfg().medic.max_loop_iterations) or 100
    except Exception:
        limit = 100
    now = time.monotonic()
    with _calls_lock:
        while _calls and now - _calls[0] > _WINDOW_SECONDS:
            _calls.popleft()
        if record:
            _calls.append(now)
        n = len(_calls)
    if n > limit:
        logger.warning("Loop prevention: %d agent iterations in %ds (limit %d)", n, _WINDOW_SECONDS, limit)
        return f"Execution limit reached: {n} iterations in the last {_WINDOW_SECONDS}s (limit {limit})."
    return f"OK: {n}/{limit} iterations in the last {_WINDOW_SECONDS}s."


def _reset_loop_counter() -> None:
    """Testing helper."""
    with _calls_lock:
        _calls.clear()


# ── Agent fusion (2026-10-27) ────────────────────────────────────────────────
async def propose_agent_fusion(agent_a: str, agent_b: str, new_name: str = "", reason: str = "") -> str:
    """
    Propose fusing two agents into one (only the medic agent or the medic's own
    jobs may). Nothing changes until the user approves it (Settings → Agents →
    Fusion proposals, or `zensynora agents approve ID`).
    The fused agent gets both skills, a merged prompt and the LOWER level of the two.

    Args:
        agent_a (str): First agent (its name is kept unless new_name is given).
        agent_b (str): Second agent.
        new_name (str): Optional name for the fused agent.
        reason (str): Why the two should become one (shown to the user).

    Returns:
        str: The proposal id and summary, or what is wrong.
    """
    from ..agent_fusion import propose_agent_fusion as _propose

    return await _propose(agent_a, agent_b, new_name, reason)


def list_agent_fusions(status: str = "") -> str:
    """
    List agent fusion proposals.

    Args:
        status (str): pending, applied, rejected or undone (empty = all).

    Returns:
        str: One line per proposal.
    """
    from ..agent_fusion import list_agent_fusions as _list

    return _list(status)


class MedicAgent:
    """
    MedicAgent — class wrapper used by the gateway on startup.
    """

    def scan_system(self, offline: bool = False) -> str:
        """Full health report (all doctor checks)."""
        return get_health_report(offline=offline)

    def check_memory(self, threshold_percent: float = 85.0) -> str:
        return check_memory_usage(threshold_percent)

    def health_status(self, offline: bool = False) -> str:
        return check_system_health(offline=offline)

    def health_report(self, offline: bool = False) -> str:
        return get_health_report(offline=offline)

    def verify_integrity(self, file_path: str = "") -> str:
        return verify_file_integrity(file_path)

    def recover(self, file_path: str, allow_main: bool = False) -> str:
        return recover_file(file_path, allow_main)

    def task_analytics(self, days: int = 7) -> str:
        return get_task_analytics(days)

    def loop_status(self) -> str:
        return prevent_infinite_loop(record=False)


__all__ = [
    "MedicAgent", "set_config", "check_memory_usage", "check_system_health", "get_health_report",
    "scan_files", "verify_file_integrity", "enable_hash_check", "create_backup", "list_backups",
    "recover_file", "detect_errors_in_file", "validate_modification", "check_file_virustotal",
    "record_task_execution", "get_task_analytics", "prevent_infinite_loop",
    "propose_agent_fusion", "list_agent_fusions",
]
