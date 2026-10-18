"""Read the JSON log (~/.myclaw/logs/zensynora.log) for the doctor, the medic
and the diagnostics report.

* ``scan(hours)`` reads the newest part of the log (and rotated files when
  needed) and returns warnings/errors plus every program start.
* ``group(entries)`` groups messages that differ only in numbers or ids
  ("task_webui_123 timed out" and "task_webui_456 timed out" are one group).
* ``restart_stats(starts)`` finds crash loops (many starts in a short time).
* ``redact(text)`` hides anything that looks like a token, key or password.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime

from .paths import log_file

LEVELS = ("WARNING", "ERROR", "CRITICAL")
_SEVERITY = {"CRITICAL": 0, "ERROR": 1, "WARNING": 2}

# "ZenSynora v0.7.5 initialised (log_level=INFO, cmd=gateway)" — written once
# per program start by myclaw.init_app(). Short CLI commands (doctor, knowledge
# …) also start the program; only long-running ones count as restarts.
_START_RE = re.compile(r"^ZenSynora v\S+ initialised")
_CMD_RE = re.compile(r"cmd=([\w-]*)")
SERVICE_COMMANDS = {"gateway", "webui", ""}

# ── Redaction ────────────────────────────────────────────────────────────────
SECRET_PATTERNS = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{30,}"),                # Telegram bot token
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),                       # OpenAI / Anthropic style
    re.compile(r"\b(?:gsk|ghp|gho|ghs|xox[abprs]|glpat)[_-][A-Za-z0-9_-]{16,}"),  # Groq, GitHub, Slack, GitLab
    re.compile(r"\bAIza[0-9A-Za-z_-]{30,}"),                      # Google / Gemini
    re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"),
]
_KV_SECRET = re.compile(
    r"(?i)([\"']?\b(?:api[_-]?key|token|secret|password|passwd|authorization|access[_-]?key)[\"']?\s*[:=]\s*[\"']?)"
    r"([^\s\"',;}{]{6,})")
_URL_USERINFO = re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")


def redact(text: str) -> str:
    """Hide tokens, API keys and passwords in a piece of text."""
    if not text:
        return text
    for rx in SECRET_PATTERNS:
        text = rx.sub("[redacted]", text)
    text = _KV_SECRET.sub(lambda m: m.group(1) + "[redacted]", text)
    return _URL_USERINFO.sub(lambda m: m.group(1) + "[redacted]@", text)


def looks_secret(text: str) -> bool:
    return any(rx.search(text or "") for rx in SECRET_PATTERNS[:4])


# ── Reading ──────────────────────────────────────────────────────────────────
@dataclass
class Entry:
    ts: float | None
    time: str
    level: str
    module: str
    message: str
    exception: str = ""

    def local_time(self) -> str:
        if self.ts is None:
            return self.time[:19]
        return datetime.fromtimestamp(self.ts).strftime("%Y-%m-%d %H:%M:%S")

    def line(self) -> str:
        out = f"{self.time} {self.level} {self.module}: {self.message}"
        if self.exception:
            out += "\n" + self.exception
        return redact(out)


@dataclass
class Scan:
    hours: float
    problems: list[Entry] = field(default_factory=list)    # WARNING and up
    starts: list[tuple[float, str]] = field(default_factory=list)  # (ts, cmd)
    lines: int = 0
    files: int = 0
    truncated: bool = False  # older lines in the window were not read (size limit)
    exists: bool = False

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys(LEVELS, 0)
        for e in self.problems:
            out[e.level] = out.get(e.level, 0) + 1
        return out


def _ts(value: str) -> float | None:
    try:
        return datetime.fromisoformat(value).timestamp()
    except (TypeError, ValueError):
        return None


def _log_files() -> list:
    lf = log_file()
    return [lf] + [lf.with_name(f"{lf.name}.{i}") for i in range(1, 6)]


def _read_tail(path, budget: int) -> tuple[list[str], bool]:
    """Last ``budget`` bytes of a file as lines; True when the whole file was read."""
    size = path.stat().st_size
    n = min(size, budget)
    with open(path, "rb") as f:
        f.seek(size - n)
        data = f.read(n)
    lines = data.decode("utf-8", errors="replace").splitlines()
    whole = n >= size
    if not whole and lines:
        lines = lines[1:]  # first line is cut
    return lines, whole


def scan(hours: float = 24, max_bytes: int = 8_000_000) -> Scan:
    """Warnings, errors and program starts of the last ``hours`` hours."""
    cutoff = time.time() - hours * 3600
    out = Scan(hours=hours)
    budget = max_bytes
    chunks: list[list[str]] = []
    for path in _log_files():
        if budget <= 0:
            out.truncated = True
            break
        try:
            if not path.exists():
                break
            out.exists = True
            lines, whole = _read_tail(path, budget)
        except OSError:
            break
        out.files += 1
        budget -= sum(len(x) + 1 for x in lines)
        chunks.append(lines)
        # Stop when this file already reaches back past the cutoff.
        first = next((ln for ln in lines if ln.startswith("{")), "")
        try:
            t = _ts(json.loads(first).get("timestamp")) if first else None
        except ValueError:
            t = None
        if t is not None and t < cutoff:
            break
        if not whole:
            out.truncated = True
            break
    for lines in reversed(chunks):  # oldest file first
        for line in lines:
            out.lines += 1
            if ('"level": "INFO"' in line or '"level": "DEBUG"' in line) and "initialised" not in line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            ts = _ts(rec.get("timestamp", ""))
            if ts is not None and ts < cutoff:
                continue
            level = str(rec.get("level", ""))
            msg = str(rec.get("message", ""))
            if level == "INFO" and _START_RE.match(msg):
                m = _CMD_RE.search(msg)
                out.starts.append((ts or 0.0, m.group(1) if m else ""))
            elif level in LEVELS:
                out.problems.append(Entry(ts, str(rec.get("timestamp", "")), level,
                                          str(rec.get("module") or rec.get("logger") or rec.get("component") or ""),
                                          msg, str(rec.get("exception") or "")))
    return out


def tail_lines(n: int = 200) -> list[str]:
    """Last ``n`` log lines, readable and redacted."""
    lf = log_file()
    if not lf.exists():
        return []
    try:
        lines, _ = _read_tail(lf, 400_000)
    except OSError:
        return []
    out = []
    for line in lines[-n:]:
        try:
            rec = json.loads(line)
            e = Entry(None, str(rec.get("timestamp", "")), str(rec.get("level", "")),
                      str(rec.get("module", "")), str(rec.get("message", "")), str(rec.get("exception") or ""))
            out.append(e.line())
        except (ValueError, AttributeError):
            out.append(redact(line))
    return out


# ── Grouping ─────────────────────────────────────────────────────────────────
_NUM = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F-]{22}|0x[0-9a-fA-F]+|\b[0-9a-fA-F]{8,}\b|\d+")


def normalize(message: str) -> str:
    """Message with numbers and ids replaced by '#', for grouping."""
    return re.sub(r"\s+", " ", _NUM.sub("#", message or "")).strip()[:300]


def group(entries: list[Entry], samples: int = 3) -> list[dict]:
    """Group entries by level + message without numbers; most severe/frequent first."""
    groups: dict[tuple[str, str], dict] = {}
    for e in entries:
        key = (e.level, normalize(e.message))
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"level": e.level, "key": key[1], "count": 0, "first": e.local_time(), "last": "",
                               "module": e.module, "message": "", "samples": [], "traceback": False}
        g["count"] += 1
        g["last"] = e.local_time()
        g["message"] = redact(e.message[:500])
        g["traceback"] = g["traceback"] or bool(e.exception)
        g["samples"].append(e.line()[:4000])
        if len(g["samples"]) > samples:
            # keep the newest samples, but always one with a traceback if there is one
            drop = next((i for i, s in enumerate(g["samples"]) if "Traceback" not in s), 0)
            g["samples"].pop(drop)
    return sorted(groups.values(), key=lambda g: (_SEVERITY.get(g["level"], 9), -g["count"]))


# ── Restarts ─────────────────────────────────────────────────────────────────
def restart_stats(starts: list[tuple[float, str]], window_s: int = 900) -> dict:
    """Starts of the long-running program: total, most in any window, latest."""
    times = sorted(t for t, cmd in starts if cmd in SERVICE_COMMANDS and t)
    best, j = 0, 0
    for i, t in enumerate(times):
        while times[j] < t - window_s:
            j += 1
        best = max(best, i - j + 1)
    recent = len([t for t in times if t >= time.time() - window_s])
    gaps = [b - a for a, b in zip(times, times[1:], strict=False)]
    return {"starts": len(times), "max_in_window": best, "window_min": window_s // 60,
            "in_last_window": recent, "last": times[-1] if times else None,
            "typical_gap_s": round(sorted(gaps)[len(gaps) // 2], 1) if gaps else None}
