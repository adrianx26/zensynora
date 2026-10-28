"""Rules (2026-10-27): instructions from the user that every agent must follow.

A rule is a Markdown file ``~/.myclaw/rules/<slug>.md`` with a short header::

    ---
    title: Answer in Romanian
    scope: all                # or a list of agent names: Researcher, default
    priority: high            # high | normal | low
    enabled: true
    author: user              # or agent:<name> (an agent proposed it, you approved it)
    created: 2026-10-27T10:00:00
    updated: 2026-10-27T10:00:00
    ---
    Always answer in Romanian, even when the question is in English.

Who can change rules
--------------------
* **You** (Web UI → Rules, or ``zensynora rules …``) write, change, switch off
  and delete rules directly, and approve or reject proposals.
* **Agents can only propose** (tools ``propose_rule``, ``propose_rule_change``,
  ``propose_rule_deletion``). A proposal is a JSON file in
  ``~/.myclaw/rules/.proposals/<id>.json``; nothing is active until you approve
  it. Rejected proposals stay as history.
* Nothing is lost: a replaced or deleted rule is moved to
  ``~/.myclaw/rules/.trash/`` first.

How agents see them
-------------------
:func:`prompt_section` is added to every agent's system prompt (chat, jobs,
delegation, Telegram all build it in ``Agent._load_system_prompt``): the
enabled rules whose scope is ``all`` or the agent's name, highest priority
first, at most ``rules.prompt_chars`` characters. Only the files in the rules
folder itself are read — never proposals or the trash. The parsed rules are
cached by the files' modification times, so a turn costs one directory listing.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import hashlib
import json
import logging
import os
import re
import shutil
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

try:  # cross-process lock (Linux/macOS); Windows falls back to the thread lock
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows
    _fcntl = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
AGENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
PRIORITIES = ("high", "normal", "low")
_PRIORITY_ORDER = {p: i for i, p in enumerate(PRIORITIES)}
ACTIONS = ("create", "modify", "delete")
STATUSES = ("pending", "approved", "rejected")
MAX_TITLE = 120
MAX_TEXT = 3000          # one rule's text; fits in the default prompt budget
MAX_REASON = 1000
MAX_RULES = 100
DEFAULT_PROMPT_CHARS = 4000
DEFAULT_MAX_PENDING = 5
STALE_DAYS = 7           # doctor warns when a proposal waits longer
PROPOSED_NOTE = "Proposal #{id} sent to the user for approval — it is not active yet."
KEEP_DECIDED = 500       # decided proposals kept as history (older ones are pruned)
# 2026-10-27 (review): a rule is used only when its file matches what the user
# last approved (sha256 in .manifest.json, written only by the user's actions).
OK_STATE, CHANGED_STATE, UNVERIFIED_STATE = "ok", "changed", "unverified"

_lock = threading.RLock()


# ── paths ────────────────────────────────────────────────────────────────────
def rules_dir() -> Path:
    return Path.home() / ".myclaw" / "rules"


def proposals_dir() -> Path:
    return rules_dir() / ".proposals"


def trash_dir() -> Path:
    return rules_dir() / ".trash"


def manifest_path() -> Path:
    return rules_dir() / ".manifest.json"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@contextmanager
def _locked() -> Iterator[None]:
    """One writer at a time — also across processes (Web UI, gateway, CLI)."""
    with _lock:
        if _fcntl is None:  # pragma: no cover - Windows
            yield
            return
        proposals_dir().mkdir(parents=True, exist_ok=True)
        with open(proposals_dir() / ".lock", "a+") as f:
            _fcntl.flock(f, _fcntl.LOCK_EX)
            try:
                yield
            finally:
                _fcntl.flock(f, _fcntl.LOCK_UN)


def slugify(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").strip().lower()).strip("-")
    return s[:63].strip("-")


def _check_slug(slug: str) -> str:
    """The slug as given, or ValueError — never a path (no '/', '..', dots)."""
    s = (slug or "").strip()
    if s.endswith(".md"):
        s = s[:-3]
    if not SLUG_RE.match(s):
        raise ValueError(f"'{slug}' is not a rule name — use lowercase letters, digits and dashes "
                         "(e.g. answer-in-romanian); list_rules shows the names.")
    return s


def _rule_path(slug: str) -> Path:
    # The slug pattern allows no '/', '\\', '.' or '..', so this stays in the folder.
    return rules_dir() / f"{_check_slug(slug)}.md"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ── parsing and writing ──────────────────────────────────────────────────────
def _parse_scope(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple)):
        items = [str(x) for x in raw]
    else:
        items = re.split(r"[,\s]+", str(raw or "").strip().strip("[]"))
    out = [i.strip().strip("'\"").lstrip("@") for i in items]
    out = [i for i in out if i]
    if not out or any(i.lower() == "all" for i in out):
        return ["all"]
    return list(dict.fromkeys(out))


def _parse_priority(raw: Any) -> str:
    p = str(raw or "normal").strip().lower()
    return p if p in PRIORITIES else "normal"


def parse(text: str, slug: str = "") -> dict[str, Any]:
    """Rule file text → dict (title, scope, priority, enabled, author, created, updated, text)."""
    meta: dict[str, str] = {}
    body = text or ""
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?", body, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t", "#")):
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.split(" #")[0].strip().strip('"').strip("'")
        body = body[m.end():]
    body = body.strip()
    return {
        "slug": slug,
        "title": (meta.get("title") or slug.replace("-", " ").capitalize())[:MAX_TITLE],
        "scope": _parse_scope(meta.get("scope", "all")),
        "priority": _parse_priority(meta.get("priority")),
        "enabled": str(meta.get("enabled", "true")).lower() not in ("false", "no", "0", "off"),
        "author": meta.get("author") or "user",
        "created": meta.get("created", ""),
        "updated": meta.get("updated", ""),
        "text": body,
    }


def render(rule: dict[str, Any]) -> str:
    title = " ".join(str(rule.get("title", "")).split())
    scope = _parse_scope(rule.get("scope", "all"))
    return (
        "---\n"
        f"title: {title}\n"
        f"scope: {', '.join(scope)}\n"
        f"priority: {_parse_priority(rule.get('priority'))}\n"
        f"enabled: {'true' if rule.get('enabled', True) else 'false'}\n"
        f"author: {rule.get('author') or 'user'}\n"
        f"created: {rule.get('created') or _now()}\n"
        f"updated: {rule.get('updated') or _now()}\n"
        "---\n\n"
        f"{str(rule.get('text', '')).strip()}\n"
    )


def _looks_secret(text: str) -> bool:
    try:
        from .knowledge.ingest.safety import redact
    except Exception:  # pragma: no cover - the scrubber ships with ZenSynora
        return False
    return redact(text) != text


def _known_agents() -> list[str] | None:
    try:
        from .config import load_config

        cfg = load_config()
        return ["default"] + [a.name for a in cfg.agents.named]
    except Exception:
        return None


def validate(title: str, text: str, scope: Any = "all", priority: Any = "normal") -> dict[str, Any]:
    """Check the parts of a rule; returns them cleaned or raises ValueError (readable)."""
    title = " ".join(str(title or "").split())
    text = str(text or "").strip()
    if not title:
        raise ValueError("Give the rule a short title.")
    if len(title) > MAX_TITLE:
        raise ValueError(f"The title is too long ({len(title)} characters, at most {MAX_TITLE}).")
    if not text:
        raise ValueError("Write the rule's text (what agents must do or not do).")
    if len(text) > MAX_TEXT:
        raise ValueError(f"The rule is too long ({len(text)} characters, at most {MAX_TEXT}). "
                         "Split it into several rules.")
    if _looks_secret(title + "\n" + text):
        raise ValueError("The rule looks like it contains a password, token or API key. Rules are sent "
                         "to the model with every message — never put secrets in them.")
    if str(priority or "normal").strip().lower() not in PRIORITIES:
        raise ValueError(f"Priority must be one of: {', '.join(PRIORITIES)}.")
    sc = _parse_scope(scope)
    if sc != ["all"]:
        bad = [s for s in sc if not AGENT_RE.match(s)]
        if bad:
            raise ValueError(f"Not an agent name: {', '.join(bad)}. Use 'all' or agent names, comma-separated.")
        known = _known_agents()
        if known is not None:
            lower = {k.lower(): k for k in known}
            unknown = [s for s in sc if s.lower() not in lower]
            if unknown:
                raise ValueError(f"Unknown agent(s): {', '.join(unknown)}. Agents: {', '.join(known)}; "
                                 "or use 'all'.")
            sc = [lower[s.lower()] for s in sc]
    return {"title": title, "text": text, "scope": sc, "priority": _parse_priority(priority)}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
    _invalidate()


def _invalidate() -> None:
    """Our own writes re-read the rules even when the clock did not move."""
    with _lock:
        _cache.update(sig=None, sections={})


_manifest_warned: set[str] = set()


def _read_manifest() -> dict[str, Any]:
    try:
        data = json.loads(manifest_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        key = str(e)[:80]
        if key not in _manifest_warned:
            _manifest_warned.add(key)
            logger.warning("Rules manifest unreadable (%s): every rule waits for your review", e)
        return {}
    return data if isinstance(data, dict) else {}


def _record(slug: str, content: str | None, by: str) -> None:
    """Remember the content the user approved (None = the rule is gone). User paths only."""
    m = _read_manifest()
    if content is None:
        m.pop(slug, None)
    else:
        m[slug] = {"sha256": _sha(content), "approved": content, "at": _now(), "by": by or "user"}
    _atomic_write(manifest_path(), json.dumps(m, indent=1, ensure_ascii=False))


def _write_rule(path: Path, content: str, by: str) -> None:
    _atomic_write(path, content)
    _record(path.stem, content, by)


def approved_content(slug: str) -> str | None:
    """The version of a rule the user last approved (None = never approved)."""
    entry = _read_manifest().get(_check_slug(slug))
    return entry.get("approved") if isinstance(entry, dict) else None


def _to_trash(path: Path, why: str) -> Path | None:
    """Move a rule file to .trash (never lost). Returns where it went."""
    if not path.exists():
        return None
    trash_dir().mkdir(parents=True, exist_ok=True)
    now = time.time()  # microseconds: names sort by age
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1e6) % 1_000_000:06d}"
    target = trash_dir() / f"{path.stem}.{stamp}.{why}.md"
    n = 1
    while target.exists():
        n += 1
        target = trash_dir() / f"{path.stem}.{stamp}.{why}-{n}.md"
    shutil.copy2(path, target)
    return target


# ── reading (cached by mtime) ────────────────────────────────────────────────
_cache: dict[str, Any] = {"sig": None, "rules": [], "sections": {}, "skipped": []}


def _signature() -> tuple:
    """Names, times and sizes of the rule files and the manifest (no reading)."""
    base = rules_dir()
    try:
        entries = list(os.scandir(base))
    except OSError:
        return (str(base),)
    sig = []
    for e in entries:
        if e.name.startswith(".") or not e.name.endswith(".md"):
            continue
        try:
            if e.is_symlink():  # 2026-10-27 (review): never follow links out of the folder
                sig.append((e.name, 0, 0, "symlink"))
                continue
            if not e.is_file(follow_symlinks=False):
                continue
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        sig.append((e.name, st.st_mtime_ns, st.st_size, "file"))
    try:
        mst = manifest_path().stat()
        man = (mst.st_mtime_ns, mst.st_size)
    except OSError:
        man = (0, 0)
    return (str(base), tuple(sorted(sig)), man)


def _sort_key(r: dict[str, Any]) -> tuple:
    return (_PRIORITY_ORDER.get(r["priority"], 1), r.get("created") or "", r["slug"])


def _load_all() -> list[dict[str, Any]]:
    sig = _signature()
    with _lock:
        if _cache["sig"] == sig:
            return _cache["rules"]
        rules, skipped = [], []
        manifest = _read_manifest() if len(sig) > 1 else {}
        for name, _mt, _size, kind in (sig[1] if len(sig) > 1 else ()):
            slug = name[:-3]
            if kind == "symlink":
                skipped.append((name, "a symbolic link (not used)"))
                continue
            if not SLUG_RE.match(slug):
                skipped.append((name, "name not allowed (use lowercase letters, digits and dashes)"))
                continue
            try:
                with open(rules_dir() / name, "rb") as fh:
                    data = fh.read(MAX_TEXT * 8 + 4096)
                text = data.decode("utf-8")
            except (OSError, UnicodeDecodeError) as e:
                logger.info("Rule file %s cannot be read: %s", name, e)
                skipped.append((name, f"unreadable ({e.__class__.__name__})"))
                continue
            rule = parse(text, slug)
            entry = manifest.get(slug)
            if not isinstance(entry, dict):
                rule["state"] = UNVERIFIED_STATE
            elif entry.get("sha256") != _sha(text):
                rule["state"] = CHANGED_STATE
            else:
                rule["state"] = OK_STATE
            rules.append(rule)
        rules.sort(key=_sort_key)
        _cache.update(sig=sig, rules=rules, sections={}, skipped=skipped)
        return rules


def skipped_files() -> list[tuple[str, str]]:
    """Files in the rules folder that are not rules (bad name, link, unreadable)."""
    _load_all()
    return list(_cache["skipped"])


def needs_review(rule: dict[str, Any]) -> bool:
    return rule.get("state", OK_STATE) != OK_STATE


def list_rules() -> list[dict[str, Any]]:
    """All rules (enabled or not), highest priority first."""
    return [dict(r) for r in _load_all()]


def get_rule(slug: str) -> dict[str, Any] | None:
    key = _check_slug(slug)
    return next((dict(r) for r in _load_all() if r["slug"] == key), None)


def _raw(slug: str) -> str | None:
    p = _rule_path(slug)
    try:
        return p.read_text(encoding="utf-8") if p.is_file() and not p.is_symlink() else None
    except (OSError, UnicodeDecodeError):
        return None


# ── your changes (Web UI / CLI only) ─────────────────────────────────────────
def save_rule(title: str, text: str, scope: Any = "all", priority: str = "normal", enabled: bool = True,
              slug: str = "", author: str = "user", create: bool = False) -> dict[str, Any]:
    """Create or replace a rule directly (the user). The previous version goes to .trash.

    create=True refuses to replace an existing rule.
    """
    clean = validate(title, text, scope, priority)
    key = _check_slug(slug) if slug else slugify(clean["title"])
    if not key:
        raise ValueError("The title needs at least one letter or digit.")
    _check_slug(key)
    with _locked():
        path = _rule_path(key)
        old = get_rule(key) if path.exists() else None
        if old is not None and create:
            raise ValueError(f"A rule named '{key}' already exists — change it instead, or pick another title.")
        if old is None and len(_load_all()) >= MAX_RULES:
            raise ValueError(f"There are already {MAX_RULES} rules; delete some first.")
        now = _now()
        rule = {**clean, "enabled": bool(enabled),
                "author": (old or {}).get("author") or author or "user",
                "created": (old or {}).get("created") or now, "updated": now}
        if old is not None:
            _to_trash(path, "replaced")
        _write_rule(path, render(rule), "user")
    return get_rule(key) or {**rule, "slug": key}


def update_rule(slug: str, **changes: Any) -> dict[str, Any]:
    """Change some fields of a rule (title, text, scope, priority, enabled)."""
    cur = get_rule(slug)
    if cur is None:
        raise KeyError(slug)
    merged = {k: (changes[k] if changes.get(k) is not None else cur[k])
              for k in ("title", "text", "scope", "priority", "enabled")}
    return save_rule(merged["title"], merged["text"], merged["scope"], merged["priority"],
                     bool(merged["enabled"]), slug=cur["slug"])


def delete_rule(slug: str) -> str:
    """Delete a rule (moved to .trash). Returns the trash file."""
    with _locked():
        path = _rule_path(slug)
        if not (path.is_file() or path.is_symlink()):
            raise KeyError(slug)
        target = _to_trash(path, "deleted") if not path.is_symlink() else None
        path.unlink()
        _record(path.stem, None, "user")
    return str(target or "")


def accept_rule(slug: str, by: str = "user") -> dict[str, Any]:
    """The user keeps a rule file changed outside ZenSynora: it is used from now on."""
    with _locked():
        key = _check_slug(slug)
        text = _raw(key)
        if text is None:
            raise KeyError(slug)
        r = parse(text, key)
        validate(r["title"], r["text"], r["scope"], r["priority"])
        _record(key, text, by)
    return get_rule(key) or r


def restore_rule(slug: str, by: str = "user") -> dict[str, Any]:
    """Put back the version the user last approved (the changed file goes to .trash)."""
    with _locked():
        key = _check_slug(slug)
        old = approved_content(key)
        if old is None:
            raise ValueError(f"'{key}' was never approved — keep it, or delete the file.")
        path = _rule_path(key)
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            _to_trash(path, "changed-outside")
        _write_rule(path, old, by)
    return get_rule(key) or parse(old, key)


# ── proposals (agents) ───────────────────────────────────────────────────────
def _prop_path(pid: Any) -> Path:
    try:
        n = int(str(pid).lstrip("#"))
    except (TypeError, ValueError):
        raise ValueError(f"'{pid}' is not a proposal number.") from None
    if n < 1:
        raise ValueError(f"'{pid}' is not a proposal number.")
    return proposals_dir() / f"{n}.json"


def get_proposal(pid: Any) -> dict[str, Any] | None:
    try:
        return json.loads(_prop_path(pid).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_proposals(status: str | None = None, agent: str | None = None) -> list[dict[str, Any]]:
    out = []
    try:
        files = list(proposals_dir().glob("*.json"))
    except OSError:
        files = []
    for f in files:
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if status and p.get("status") != status:
            continue
        if agent and str(p.get("proposed_by", "")).lower() != agent.lower():
            continue
        out.append(p)
    return sorted(out, key=lambda p: int(p.get("id", 0)), reverse=True)


def proposal_stale(prop: dict[str, Any]) -> bool:
    """The rule changed after the proposal was made (the diff is against the file now)."""
    base = prop.get("base_sha")
    if not base or prop.get("action") == "create":
        return False
    cur = _raw(prop["slug"])
    return cur is None or _sha(cur) != base


def proposal_diff(prop: dict[str, Any]) -> str:
    """Line diff between the rule as it is NOW and the proposal."""
    cur = _raw(prop["slug"]) or ""
    new = "" if prop.get("action") == "delete" else prop.get("content", "")
    return "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{prop['slug']}.md (now)" if cur else "/dev/null",
        tofile=f"{prop['slug']}.md (proposed)" if new else "/dev/null"))


def _save_proposal(prop: dict[str, Any], new: bool = False) -> None:
    path = _prop_path(prop["id"])
    data = json.dumps(prop, indent=2, ensure_ascii=False)
    if new:  # claim the number even when another process proposes at the same time
        with open(path, "x", encoding="utf-8") as f:
            f.write(data)
    else:
        _atomic_write(path, data)


def _max_pending() -> int:
    try:
        from .config import load_config

        return max(1, int(load_config().rules.max_pending_per_agent))
    except Exception:
        return DEFAULT_MAX_PENDING


def create_proposal(action: str, slug: str, reason: str, proposed_by: str, title: str = "", text: str = "",
                    scope: Any = "all", priority: str = "normal") -> dict[str, Any]:
    """Store a proposal (agents). Raises ValueError with a message the agent can act on."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}")
    agent = (proposed_by or "default").strip() or "default"
    reason = (reason or "").strip()
    if len(reason) > MAX_REASON:
        raise ValueError(f"The reason is too long ({len(reason)} characters, at most {MAX_REASON}).")
    if _looks_secret(reason):
        raise ValueError("The reason looks like it contains a password, token or API key — leave secrets out.")
    with _locked():
        pending = list_proposals("pending")
        mine = [p for p in pending if str(p.get("proposed_by", "")).lower() == agent.lower()]
        limit = _max_pending()
        if len(mine) >= limit:
            raise ValueError(f"You already have {len(mine)} proposals waiting for the user (at most {limit}): "
                             f"{', '.join('#' + str(p['id']) for p in mine)}. Wait until the user decides.")
        if action == "create":
            clean = validate(title, text, scope, priority)
            key = slugify(clean["title"])
            if not key:
                raise ValueError("The title needs at least one letter or digit.")
            if get_rule(key) is not None:
                raise ValueError(f"A rule '{key}' already exists — use propose_rule_change('{key}', text, reason).")
            now = _now()
            content = render({**clean, "enabled": True, "author": f"agent:{agent}", "created": now, "updated": now})
        else:
            key = _check_slug(slug)
            cur = get_rule(key)
            if cur is None:
                names = ", ".join(r["slug"] for r in _load_all()) or "none"
                raise ValueError(f"There is no rule '{key}'. Rules: {names}.")
            if action == "modify":
                clean = validate(title or cur["title"], text, cur["scope"] if scope in (None, "") else scope,
                                 priority or cur["priority"])
                if clean["text"] == cur["text"] and clean["title"] == cur["title"] \
                        and clean["scope"] == cur["scope"] and clean["priority"] == cur["priority"]:
                    raise ValueError(f"That is the same as rule '{key}' now — nothing to propose.")
                content = render({**cur, **clean, "updated": _now()})
            else:
                content = ""
        dup = next((p for p in pending if p["slug"] == key and p["action"] == action
                    and p.get("content", "") == content), None)
        if dup:
            raise ValueError(f"The same change is already waiting as proposal #{dup['id']}.")
        proposals_dir().mkdir(parents=True, exist_ok=True)
        n = max([int(p.get("id", 0)) for p in list_proposals()] + [0]) + 1
        prop = {
            "id": n, "action": action, "slug": key,
            "title": parse(content, key)["title"] if content else (get_rule(key) or {}).get("title", key),
            "content": content, "reason": reason, "proposed_by": agent, "created": _now(),
            "base_sha": _sha(_raw(key) or "") if action != "create" else "",
            "status": "pending", "decided_at": None, "decided_by": None, "note": "",
        }
        for _ in range(50):
            try:
                _save_proposal(prop, new=True)
                break
            except FileExistsError:
                n += 1
                prop["id"] = n
        else:  # pragma: no cover - 50 concurrent proposals
            raise ValueError("Could not store the proposal; try again.")
    _notify(prop)
    logger.info("Rule proposal #%s (%s %s) by @%s", prop["id"], action, key, agent)
    return prop


def _decide(pid: Any, status: str, by: str, note: str = "") -> dict[str, Any]:
    prop = get_proposal(pid)
    if not prop:
        raise KeyError(pid)
    if prop["status"] != "pending":
        raise ValueError(f"Proposal #{prop['id']} is already {prop['status']}.")
    prop.update(status=status, decided_at=_now(), decided_by=by or "user", note=(note or "").strip()[:1000])
    _save_proposal(prop)
    _emit("rule_proposal_" + status, prop)
    _prune()
    return prop


def _prune(keep: int = KEEP_DECIDED) -> int:
    """Forget decided proposals beyond the newest ``keep`` (pending ones always stay)."""
    decided = [p for p in list_proposals() if p.get("status") != "pending"]
    removed = 0
    for p in decided[keep:]:
        try:
            _prop_path(p["id"]).unlink()
            removed += 1
        except (OSError, ValueError):
            pass
    return removed


def approve(pid: Any, by: str = "user", note: str = "") -> dict[str, Any]:
    """Apply a proposal (the user). create/modify write the file, delete moves it to .trash."""
    with _locked():
        prop = get_proposal(pid)
        if not prop:
            raise KeyError(pid)
        if prop["status"] != "pending":
            raise ValueError(f"Proposal #{prop['id']} is already {prop['status']}.")
        key = _check_slug(prop["slug"])
        path = _rule_path(key)
        action = prop["action"]
        if action == "create":
            if path.exists():
                raise ValueError(f"A rule '{key}' exists now (made after the proposal). Reject the proposal, "
                                 "or delete or rename that rule first.")
            new = parse(prop["content"], key)
            validate(new["title"], new["text"], new["scope"], new["priority"])
            new["updated"] = _now()
            _write_rule(path, render(new), by)
        elif action == "modify":
            cur = get_rule(key)
            if cur is None:
                raise ValueError(f"Rule '{key}' no longer exists — reject the proposal.")
            new = parse(prop["content"], key)
            validate(new["title"], new["text"], new["scope"], new["priority"])
            # Keep what the proposal is not about: on/off, who made it, when.
            new.update(enabled=cur["enabled"], author=cur["author"], created=cur["created"], updated=_now())
            _to_trash(path, "replaced")
            _write_rule(path, render(new), by)
        else:
            if not path.exists():
                raise ValueError(f"Rule '{key}' no longer exists — reject the proposal.")
            _to_trash(path, "deleted")
            path.unlink()
            _record(key, None, by)
        return _decide(pid, "approved", by, note)


def reject(pid: Any, by: str = "user", note: str = "") -> dict[str, Any]:
    with _locked():
        return _decide(pid, "rejected", by, note)


# ── notifications ────────────────────────────────────────────────────────────
def _emit(kind: str, prop: dict[str, Any]) -> None:
    try:
        from . import events

        events.publish(events.ALL, {"type": kind, "proposal_id": prop["id"], "rule": prop["slug"],
                                    "action": prop["action"], "status": prop["status"],
                                    "by": prop.get("proposed_by"), "ts": time.time()})
    except Exception:
        pass


def _notify(prop: dict[str, Any]) -> None:
    """Tell the user: Web UI event (the Rules badge polls too) and, when on, Telegram."""
    _emit("rule_proposal_created", prop)
    try:
        from .config import load_config

        if not bool(getattr(load_config().rules, "notify_telegram", True)):
            return
    except Exception:
        pass
    try:
        from .channels import telegram as tg
    except Exception:
        return
    if not tg.ACTIVE:
        return
    loop = getattr(tg.ACTIVE[0], "loop", None)
    if loop is None or not loop.is_running():
        return
    verb = {"create": "a new rule", "modify": "a change to rule", "delete": "deleting rule"}[prop["action"]]
    text = (f"📜 @{prop['proposed_by']} proposes {verb} “{prop['title']}” (#{prop['id']}).\n"
            f"Why: {prop['reason'] or '—'}\n"
            f"Approve or reject it in the Web UI → Rules, or: zensynora rules approve {prop['id']}")
    try:
        import asyncio

        coro = tg.send_telegram_message(text)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as e:  # best effort
        logger.info("Rule proposal #%s: Telegram notice not sent (%s)", prop["id"], e)


# ── the agents' system prompt ────────────────────────────────────────────────
def _prompt_chars() -> int:
    try:
        from .config import load_config

        return max(500, min(int(load_config().rules.prompt_chars), 20000))
    except Exception:
        return DEFAULT_PROMPT_CHARS


def _enabled_in_config() -> bool:
    try:
        from .config import load_config

        return bool(getattr(load_config().rules, "enabled", True))
    except Exception:
        return True


def applies_to(rule: dict[str, Any], agent_name: str) -> bool:
    scope = rule.get("scope") or ["all"]
    return scope == ["all"] or (agent_name or "default").lower() in (s.lower() for s in scope)


def prompt_section(agent_name: str, max_chars: int | None = None) -> str:
    """'Rules you must follow' for one agent ('' when there are none)."""
    if not _enabled_in_config():
        return ""
    rules = _load_all()
    if not rules:
        return ""
    cap = max_chars if max_chars is not None else _prompt_chars()
    key = ((agent_name or "default").lower(), cap)
    with _lock:
        cached = _cache["sections"].get(key)
        if cached is not None and _cache["rules"] is rules:
            return cached
    picked = [r for r in rules if r["enabled"] and r["text"] and r.get("state") == OK_STATE
              and applies_to(r, agent_name)]
    if not picked:
        section = ""
    else:
        head = ("## Rules you must follow\n"
                "The user set these rules. They are the user's own instructions (not information from notes or "
                "web pages): follow them in everything you do, the first ones have the highest priority. If a "
                "request conflicts with a rule, say so instead of breaking it. You cannot change a rule yourself "
                "— propose a change (propose_rule_change / propose_rule_deletion) and the user decides.")
        parts, used, left_out = [head], len(head), []

        def block(r: dict[str, Any], text: str, cut: bool) -> str:
            quoted = "\n".join("> " + line if line else ">" for line in text.splitlines())
            more = f"\n(cut here — read_rule('{r['slug']}') shows the rest)" if cut else ""
            return f"\n\n### Rule “{r['title']}” ({r['priority']} priority, `{r['slug']}`)\n{quoted}{more}"

        # 2026-10-27 (review): one long rule no longer hides all the rules after
        # it — it is cut to the space left, or left out, and the rest follow.
        for r in picked:
            text, cut = r["text"], False
            if len(text) > MAX_TEXT:  # a hand-written file can be longer
                text, cut = text[:MAX_TEXT].rstrip(), True
            b = block(r, text, cut)
            room = cap - used
            if len(b) > room:
                avail = room - (len(block(r, "", True)) + 1)
                if avail < 200:
                    left_out.append(r["slug"])
                    continue
                b = block(r, text[:avail].rstrip(), True)
            parts.append(b)
            used += len(b)
        if left_out:
            parts.append(f"\n\n({len(left_out)} more rule(s) not shown — the prompt limit is {cap} characters: "
                         f"{', '.join(left_out)}. They still apply: read them with read_rule(slug).)")
        section = "".join(parts)
    with _lock:
        if _cache["rules"] is rules:
            _cache["sections"][key] = section
    return section


# ── doctor ───────────────────────────────────────────────────────────────────
def waiting_since_days(prop: dict[str, Any]) -> float:
    try:
        created = _dt.datetime.fromisoformat(str(prop.get("created")))
    except ValueError:
        return 0.0
    return (_dt.datetime.now() - created).total_seconds() / 86400


# ── tools for agents (they can only read and propose) ────────────────────────
def _agent() -> str:
    try:
        from .events import CURRENT_AGENT

        return CURRENT_AGENT.get() or "default"
    except Exception:  # pragma: no cover
        return "default"


def agent_list_rules() -> str:
    """List the user's rules (active and switched off) and your proposals that wait for the user."""
    me = _agent()
    rules = _load_all()
    lines = []
    if rules:
        lines.append("Rules (the user's; the enabled ones that apply to you are in your system prompt):")
        for r in rules:
            scope = "all agents" if r["scope"] == ["all"] else ", ".join("@" + s for s in r["scope"])
            mark = "" if r["enabled"] else " [switched off]"
            if needs_review(r):
                mark += " [changed outside ZenSynora — not active until the user reviews it]"
            lines.append(f"- {r['slug']}: {r['title']} ({r['priority']}, {scope}){mark}")
    else:
        lines.append("There are no rules yet. You can suggest one with propose_rule(title, text, scope, reason).")
    mine = list_proposals("pending", agent=me)
    if mine:
        lines.append("\nYour proposals waiting for the user (not active):")
        lines += [f"- #{p['id']} {p['action']} {p['slug']}: {p['reason'] or p['title']}" for p in mine]
    return "\n".join(lines)


def agent_read_rule(slug: str) -> str:
    """Read one rule in full.

    slug: the rule's name from list_rules, e.g. answer-in-romanian
    """
    try:
        r = get_rule(slug)
    except ValueError as e:
        return f"Error: {e}"
    if r is None:
        names = ", ".join(x["slug"] for x in _load_all()) or "none"
        return f"Error: there is no rule '{slug}'. Rules: {names}."
    scope = "all agents" if r["scope"] == ["all"] else ", ".join("@" + s for s in r["scope"])
    state = "enabled" if r["enabled"] else "switched off by the user"
    return (f"Rule {r['slug']}: {r['title']}\npriority: {r['priority']} · applies to: {scope} · {state} · "
            f"by {r['author']} · updated {r['updated']}\n\n{r['text']}")


def _propose(action: str, **kw: Any) -> str:
    try:
        prop = create_proposal(action, proposed_by=_agent(), **kw)
    except ValueError as e:
        return f"Not proposed: {e}"
    return PROPOSED_NOTE.format(id=prop["id"])


def propose_rule(title: str, text: str, scope: str = "all", reason: str = "", priority: str = "normal") -> str:
    """Propose a NEW rule. The user approves or rejects it; it is not active until then.

    title: short title, e.g. Answer in Romanian
    text: what agents must do or not do (Markdown, at most 3000 characters)
    scope: 'all' or agent names, comma-separated
    reason: why the rule helps
    priority: high, normal or low
    """
    return _propose("create", slug="", title=title, text=text, scope=scope, reason=reason, priority=priority)


def propose_rule_change(slug: str, text: str, reason: str) -> str:
    """Propose new text for an existing rule (see list_rules). Not active until the user approves.

    slug: the rule's name, e.g. answer-in-romanian
    text: the complete new text of the rule
    reason: why it should change
    """
    return _propose("modify", slug=slug, text=text, reason=reason, scope="", priority="")


def propose_rule_deletion(slug: str, reason: str) -> str:
    """Propose deleting a rule. It stays active until the user approves.

    slug: the rule's name, e.g. answer-in-romanian
    reason: why it should go
    """
    return _propose("delete", slug=slug, reason=reason)
