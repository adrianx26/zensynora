"""Built-in doctor checks. Add a new one with ``@check(...)`` (and optionally
``@fixer(id)`` for a safe automatic repair)."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from .paths import data_dir, install_base, install_root, log_file
from .registry import FAIL, OK, SKIP, WARN, check, fixer

# Python SDK each provider needs at chat time.
PROVIDER_SDK = {
    "lmstudio": ("openai", "openai"),
    "llamacpp": ("openai", "openai"),
    "openai": ("openai", "openai"),
    "groq": ("openai", "openai"),
    "openrouter": ("openai", "openai"),
    "anthropic": ("anthropic", "anthropic"),
    "gemini": ("google.generativeai", "google-generativeai"),
}
LOCAL_PROVIDERS = {"ollama", "lmstudio", "llamacpp"}
CORE_MODULES = ["fastapi", "uvicorn", "websockets", "httpx", "h2", "aiosqlite", "telegram",
                "yaml", "pydantic", "numpy", "openai", "psutil", "rich", "mcp"]
DATA_FOLDERS = ["workspace", "knowledge", "tools", "skills", "logs", "medic", "plugins", "hub"]


def _has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _providers_in_use(cfg) -> set:
    used = {cfg.agents.defaults.provider}
    used |= {a.provider for a in cfg.agents.named if a.provider}
    if cfg.agents.rotation.enabled:  # v0.7.1
        used |= {e.provider for e in cfg.agents.rotation.entries if e.provider}
    return used


# ── environment ──────────────────────────────────────────────────────────────
@check("python", "Python version", "environment")
def _python(ctx):
    v = sys.version_info
    text = f"{v.major}.{v.minor}.{v.micro}"
    if v < (3, 11):
        return FAIL, text, "ZenSynora needs Python 3.11 or newer."
    return OK, text


@check("venv", "Virtual environment", "environment")
def _venv(ctx):
    if sys.prefix == sys.base_prefix:
        return WARN, f"running outside a venv ({sys.executable})", \
            "Activate the install's venv: source ~/.local/share/zensynora/source/venv/bin/activate"
    return OK, sys.prefix


@check("core_packages", "Required Python packages", "environment")
def _core_packages(ctx):
    missing = [m for m in CORE_MODULES if not _has_module(m)]
    if missing:
        return FAIL, "missing: " + ", ".join(missing), \
            "Reinstall dependencies: zensynora update --force  (or re-run install.sh)"
    return OK, f"all {len(CORE_MODULES)} present"


@check("provider_sdk", "LLM provider libraries", "environment")
def _provider_sdk(ctx):
    cfg = ctx.get_config()
    missing = []
    from ..provider import find_custom_provider

    for p in sorted(_providers_in_use(cfg)):
        spec = PROVIDER_SDK.get(p)
        cp = None if spec else find_custom_provider(cfg, p)
        if cp is not None and cp.kind == "openai":
            spec = ("openai", "openai")  # custom OpenAI-compatible providers use the openai SDK
        if spec and not _has_module(spec[0]):
            missing.append(f"{p} needs '{spec[1]}'")
    if missing:
        return FAIL, "; ".join(missing), "pip install " + " ".join(
            sorted({m.split("'")[1] for m in missing}))
    return OK, "available for " + ", ".join(sorted(_providers_in_use(cfg)))


@check("disk", "Free disk space", "environment")
def _disk(ctx):
    target = data_dir() if data_dir().exists() else Path.home()
    free = shutil.disk_usage(target).free
    gb = free / 1e9
    if gb < 0.5:
        return FAIL, f"{gb:.1f} GB free", "Free up space; updates need about 1.5 GB."
    if gb < 2:
        return WARN, f"{gb:.1f} GB free", "Updates need about 1.5 GB; consider `zensynora update prune`."
    return OK, f"{gb:.1f} GB free"


@check("memory", "Memory (RAM)", "environment")
def _memory(ctx):
    try:
        import psutil
    except ImportError:
        return SKIP, "psutil not installed"
    m = psutil.virtual_memory()
    detail = f"{m.percent:.0f}% used of {m.total / 1e9:.1f} GB"
    if m.percent > 90:
        return WARN, detail, "Close other programs or use a smaller model."
    return OK, detail


@check("cpu", "CPU load", "environment")
def _cpu(ctx):
    try:
        import psutil
    except ImportError:
        return SKIP, "psutil not installed"
    load = os.getloadavg()[0] if hasattr(os, "getloadavg") else None
    cores = psutil.cpu_count() or 1
    if load is None:
        return OK, f"{cores} cores"
    detail = f"load {load:.1f} on {cores} cores"
    return (WARN, detail, "The machine is very busy; replies may be slow.") if load > cores * 1.5 else (OK, detail)


# ── configuration ────────────────────────────────────────────────────────────
@check("config_file", "Configuration file", "configuration")
def _config_file(ctx):
    from ..config import CONFIG_FILE

    if not CONFIG_FILE.exists():
        return WARN, "not created yet (defaults in use)", \
            "Run `zensynora onboard` or open the Web UI → Settings."
    try:
        ctx.get_config()
    except BaseException as e:  # load_config raises SystemExit on schema errors
        return FAIL, f"invalid: {str(e)[:200]}", f"Fix or restore {CONFIG_FILE} (zensynora update restore …)"
    return OK, str(CONFIG_FILE)


@check("provider_config", "Provider settings", "configuration")
def _provider_config(ctx):
    from ..provider import SUPPORTED_PROVIDERS, find_custom_provider

    cfg = ctx.get_config()
    problems = []
    for p in sorted(_providers_in_use(cfg)):
        if p not in SUPPORTED_PROVIDERS:
            cp = find_custom_provider(cfg, p)  # v0.7.1: user-added providers
            if cp is None:
                problems.append(f"unknown provider '{p}'")
            elif not cp.base_url:
                problems.append(f"{p}: server URL missing")
            continue
        if p not in LOCAL_PROVIDERS:
            pcfg = getattr(cfg.providers, p, None)
            if pcfg is not None and not pcfg.api_key.get_secret_value():
                problems.append(f"{p}: API key missing")
    if problems:
        return FAIL, "; ".join(problems), "Open Web UI → Settings → Providers."
    d = cfg.agents.defaults
    return OK, f"default: {d.provider} / {d.model}"


# ── LLM (network) ────────────────────────────────────────────────────────────
def _probe(ctx):
    if "probe" in ctx.cache:
        return ctx.cache["probe"]
    import httpx

    from ..provider import provider_endpoint

    cfg = ctx.get_config()
    p = cfg.agents.defaults.provider
    ep = provider_endpoint(cfg, p) or {"kind": "openai", "base_url": "", "api_key": ""}
    kind, base, key = ep["kind"], ep["base_url"], ep["api_key"]
    headers, params = {}, {}
    if kind == "ollama":
        url = f"{base}/api/tags"
    elif kind == "anthropic":
        url, headers = f"{base}/models", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    elif kind == "gemini":
        url, params = f"{base}/models", {"key": key}
    else:
        url = f"{base}/models"
        if key:
            headers["Authorization"] = f"Bearer {key}"
    t = time.monotonic()
    try:
        r = httpx.get(url, headers=headers, params=params, timeout=6)
        ms = int((time.monotonic() - t) * 1000)
        if r.status_code >= 400:
            res = {"ok": False, "error": f"HTTP {r.status_code} from {url}", "models": [], "ms": ms}
        else:
            body = r.json()
            if kind == "ollama":
                models = [m.get("name", "") for m in body.get("models", [])]
            elif kind == "gemini":
                models = [m.get("name", "").removeprefix("models/") for m in body.get("models", [])]
            else:
                models = [m.get("id", "") for m in body.get("data", [])]
            res = {"ok": True, "models": models, "ms": ms, "url": base}
    except Exception as e:
        res = {"ok": False, "error": f"cannot reach {base or p}: {e.__class__.__name__}", "models": []}
    ctx.cache["probe"] = res
    return res


@check("llm_reachable", "LLM server reachable", "llm", network=True)
def _llm_reachable(ctx):
    res = _probe(ctx)
    p = ctx.get_config().agents.defaults.provider
    if not res["ok"]:
        hint = "Start your model server" if p in LOCAL_PROVIDERS else "Check the API key and network"
        return FAIL, res["error"], f"{hint}, then use Settings → Test connection."
    return OK, f"{p} answered in {res['ms']} ms"


@check("llm_model", "Configured model exists", "llm", network=True)
def _llm_model(ctx):
    res = _probe(ctx)
    if not res["ok"]:
        return SKIP, "server not reachable"
    model = ctx.get_config().agents.defaults.model
    if res["models"] and model not in res["models"]:
        return WARN, f"'{model}' not offered (available: {', '.join(res['models'][:6])})", \
            "Pick a model in Settings → Model."
    return OK, model


# ── data ─────────────────────────────────────────────────────────────────────
@check("data_dir", "Data folder", "data")
def _data_dir(ctx):
    d = data_dir()
    if not d.exists():
        return WARN, f"{d} missing", "It is created on first use (or run --fix)."
    if not os.access(d, os.W_OK):
        return FAIL, f"{d} is not writable", f"Fix ownership: sudo chown -R $USER {d}"
    missing = [f for f in DATA_FOLDERS if not (d / f).is_dir()]
    if missing:
        return WARN, "missing folders: " + ", ".join(missing), "Run zensynora doctor --fix."
    return OK, str(d)


@fixer("data_dir")
def _fix_data_dir(ctx):
    for f in DATA_FOLDERS:
        (data_dir() / f).mkdir(parents=True, exist_ok=True)
    return "created missing folders"


@check("databases", "Databases", "data")
def _databases(ctx):
    dbs = sorted(data_dir().glob("*.db")) if data_dir().exists() else []
    if not dbs:
        return OK, "none yet"
    bad = []
    for db in dbs:
        try:
            con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5)
            try:
                row = con.execute("PRAGMA quick_check").fetchone()
            finally:
                con.close()
            if not row or row[0] != "ok":
                bad.append(f"{db.name}: {row[0] if row else 'no result'}")
        except sqlite3.Error as e:
            bad.append(f"{db.name}: {e}")
    if bad:
        return FAIL, "; ".join(bad), "Restore your data from a backup: zensynora update restore --backup NAME"
    return OK, f"{len(dbs)} database(s) healthy"


@check("migrations", "Data migrations", "data")
def _migrations(ctx):
    root = install_root()
    if not (root / "updater" / "migrations").is_dir():
        return SKIP, "updater not installed"
    pending = _pending_migrations()
    if pending is None:
        return SKIP, "could not read migration list"
    if pending:
        return WARN, "pending: " + ", ".join(pending), "Run zensynora doctor --fix (or zensynora update)."
    return OK, "up to date"


def _pending_migrations():
    root = str(install_root())
    added = root not in sys.path
    if added:
        sys.path.insert(0, root)
    try:
        from updater.migrations import pending

        return [m.ID for m in pending(data_dir())]
    except Exception:
        return None
    finally:
        if added:
            sys.path.remove(root)


@fixer("migrations")
def _fix_migrations(ctx):
    from .. import __version__

    r = subprocess.run([sys.executable, "-m", "updater", "migrate", "--app-version", __version__],
                       cwd=install_root(), capture_output=True, text=True, timeout=600)
    return (r.stdout.strip().splitlines() or ["done"])[-1] if r.returncode == 0 else f"failed: {r.stderr[-200:]}"


# ── security ─────────────────────────────────────────────────────────────────
def _private_files():
    d = data_dir()
    return [p for p in (d / "config.json", d / ".config_key", d / "audit" / ".audit_secret") if p.exists()]


@check("permissions", "Private files are private", "security")
def _permissions(ctx):
    loose = [p.name for p in _private_files() if p.stat().st_mode & 0o077]
    base = install_base()
    if base and (base / "backups").exists() and (base / "backups").stat().st_mode & 0o077:
        loose.append("update backups folder")
    if loose:
        return WARN, "readable by other users: " + ", ".join(loose), "Run zensynora doctor --fix."
    return OK, "config, keys and backups are owner-only"


@fixer("permissions")
def _fix_permissions(ctx):
    for p in _private_files():
        os.chmod(p, 0o600)
    base = install_base()
    if base and (base / "backups").exists():
        os.chmod(base / "backups", 0o700)
    return "permissions tightened"


@check("telegram", "Telegram bot", "configuration")
def _telegram(ctx):
    """2026-10-16: a token without allowed users = a bot that ignores everyone."""
    tg = ctx.get_config().channels.telegram
    if not tg.enabled:
        return OK, "off"
    if not tg.token.get_secret_value():
        return FAIL, "enabled but no bot token", "Add the token from @BotFather in Web UI → Settings → Channels."
    from ..channels.telegram import allow_list_problem

    problem = allow_list_problem(tg.allowFrom)
    if problem:
        return WARN, "nobody is allowed to use the bot", \
            "Send /whoami to the bot and add the number to Settings → Channels → Allowed Telegram users."
    where = "inside the Web UI" if getattr(tg, "run_in_webui", True) else "with `zensynora gateway`"
    return OK, f"{len(tg.allowFrom)} allowed user(s); runs {where}"


@check("webui_exposure", "Web UI access control", "security")
def _webui_exposure(ctx):
    cfg = ctx.get_config()
    if not cfg.security.admin_api_key.get_secret_value():
        return OK, "settings only from this computer (no admin key set)"
    return OK, "admin key set — settings reachable from other devices with the key"


# ── install ──────────────────────────────────────────────────────────────────
@check("install_layout", "Installation & updates", "install")
def _install_layout(ctx):
    from .. import __version__

    root = install_root()
    base = install_base()
    if (root / ".git").exists():
        return OK, f"{__version__} from a git checkout ({root}) — update with git pull"
    if base is None:
        return WARN, f"{__version__} in a non-standard place ({root})", \
            "Safe updates expect ~/.local/share/zensynora/source."
    if not (root / "update.sh").exists():
        return WARN, f"{__version__}, updater missing", "Re-run install-online.sh once to get safe updates."
    if (base / "source").is_symlink():
        n = len([p for p in (base / "releases").iterdir() if p.is_dir()]) if (base / "releases").exists() else 0
        return OK, f"{__version__}, managed ({n} release(s) kept for rollback)"
    return OK, f"{__version__} (switches to side-by-side releases on the next update)"


@check("webui_build", "Web UI build", "install")
def _webui_build(ctx):
    dist = install_root() / "webui" / "dist" / "index.html"
    if not dist.exists():
        return WARN, "not built", "Install Node.js, then: cd webui && npm ci && npm run build"
    return OK, "built"


# ── runtime ──────────────────────────────────────────────────────────────────
@check("recent_errors", "Errors in the last 24 h", "runtime")
def _recent_errors(ctx):
    """Errors and CRITICAL lines, plus warnings that keep repeating; messages
    that differ only in numbers/ids count as one (see doctor/logscan.py)."""
    from . import logscan
    from .checks_more import CRITICAL_FAIL, WARNING_REPEAT, log_scan

    lf = log_file()
    sc = log_scan(ctx)
    if not sc.exists:
        return SKIP, "no log file yet"
    groups = [g for g in logscan.group(sc.problems)
              if g["level"] != "WARNING" or g["count"] >= WARNING_REPEAT]
    if not groups:
        return OK, "none"
    counts = {lv: sum(g["count"] for g in groups if g["level"] == lv) for lv in ("CRITICAL", "ERROR", "WARNING")}
    kinds = {lv: sum(1 for g in groups if g["level"] == lv) for lv in counts}
    parts = [f"{lv} {counts[lv]}" + (f" ({kinds[lv]} kinds)" if kinds[lv] > 1 else "")
             for lv in counts if counts[lv]]
    if counts["WARNING"]:
        parts[-1] = parts[-1].replace("WARNING", "repeated WARNING")
    top = max(groups, key=lambda g: g["count"])
    detail = ", ".join(parts) + f"; most frequent: {top['message'][:140]} ×{top['count']}"
    hint = f"Doctor → Report → Errors shows each group with its log lines (log: {lf})."
    return (FAIL if counts["CRITICAL"] >= CRITICAL_FAIL else WARN), detail, hint


@check("service", "Background service (systemd)", "runtime")
def _service(ctx):
    if not shutil.which("systemctl") or not Path("/etc/systemd/system/myclaw.service").exists():
        return SKIP, "not installed"
    r = subprocess.run(["systemctl", "is-active", "myclaw"], capture_output=True, text=True)
    state = r.stdout.strip() or "unknown"
    if state == "active":
        return OK, "running"
    if state == "failed":
        return FAIL, "failed", "See: journalctl -u myclaw -n 50"
    return OK, state


# ── integrity ────────────────────────────────────────────────────────────────
@check("integrity", "Code integrity", "integrity")
def _integrity(ctx):
    from . import integrity

    if not integrity.hash_check_enabled():
        return SKIP, "hash check disabled"
    res = integrity.verify()
    if res["status"] in ("no-baseline", "stale-baseline"):
        # First run, or the version changed through an update: the installed
        # files are the reference now, so record them instead of warning.
        out = integrity.scan()
        return OK, f"baseline recorded for {out['version']} ({out['files']} files)"
    if res["status"] == "changed":
        changed = res["modified"] + [f"{m} (missing)" for m in res["missing"]]
        return WARN, f"{len(changed)} file(s) differ: {', '.join(changed[:5])}", \
            "If you didn't edit them, use the recover_file tool or `zensynora update --force`."
    return OK, f"{res['checked']} files match the baseline"
