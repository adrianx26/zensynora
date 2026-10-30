"""Manage your own tools and skills (2026-10-31): create, edit, delete, migrate,
export and import as a file.

Tools are the Python files in ``~/.myclaw/TOOLBOX`` (made by agents with
``register_tool`` or by you here); skills are the Markdown instructions in
``~/.myclaw/skills`` (``myclaw/agent_skills.py``).

* **Create / edit** — the code is saved, the previous version is kept in
  ``TOOLBOX/.trash``, and the tool is loaded again. A tool you write or edit is
  marked ``author: user`` (its code is not re-checked on load), but every
  TOOLBOX tool still runs in the tool sandbox (``tool_sandbox``).
* **Delete** — the files move to ``TOOLBOX/.trash``; nothing is erased.
* **Migrate**
  - ``tool_to_skill``: many agent-made "tools" only return a fixed description
    (instructions written as Python). They become a real skill (Markdown) and
    the tool is disabled — kept, not deleted. The code is read, never run.
  - ``repair``: registry entries whose file is gone, tool files that are not
    registered, README files without a tool, old registry formats, paths from
    another computer, and old flat skill files.
* **Export / import** — one JSON file (``zensynora-bundle``) holds any number of
  tools and skills. Import also takes a single ``.py`` tool or a ``SKILL.md``.
  Imported tool code is checked like agent code (``tool_policy``); code that
  fails the check is saved *disabled* with the reason, unless you mark it as
  trusted (then it is ``author: user`` and still sandboxed).
"""

from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import agent_skills
from . import toolbox
from .core import TOOLS, TOOL_SCHEMAS, _generate_schemas

logger = logging.getLogger(__name__)

BUNDLE_FORMAT = "zensynora-bundle"
BUNDLE_VERSION = 1
MAX_CODE = 200_000          # one tool's code
MAX_DOC = 100_000           # one tool's README
MAX_BUNDLE = 5_000_000      # one import file
MAX_ITEMS = 200             # tools + skills in one bundle
_DOC_SUFFIX = "_README.md"


class ToolboxError(ValueError):
    """A request that cannot be done; the message says what to change."""


# ── files and registry ───────────────────────────────────────────────────────
def _dir() -> Path:
    return Path(toolbox.TOOLBOX_DIR)


def _reg_path() -> Path:
    return Path(toolbox.TOOLBOX_REG)


def _trash_dir() -> Path:
    return _dir() / ".trash"


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def read_registry() -> Dict[str, Dict[str, Any]]:
    """The TOOLBOX registry; the old format (name → path string) is read as entries."""
    p = _reg_path()
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, ValueError) as e:
        raise ToolboxError(f"The tool registry {p.name} cannot be read ({e}). Fix or remove the file.") from e
    if not isinstance(raw, dict):
        raise ToolboxError(f"The tool registry {p.name} is not a JSON object.")
    out: Dict[str, Dict[str, Any]] = {}
    for name, info in raw.items():
        out[name] = dict(info) if isinstance(info, dict) else {"path": str(info), "_old_format": True}
    return out


def _write_registry(reg: Dict[str, Dict[str, Any]]) -> None:
    p = _reg_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    clean = {n: {k: v for k, v in i.items() if not k.startswith("_")} for n, i in reg.items()}
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(clean, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def _tool_file(name: str) -> Path:
    return _dir() / f"{name}.py"


def _doc_file(name: str) -> Path:
    return _dir() / f"{name}{_DOC_SUFFIX}"


def _entry_path(name: str, info: Dict[str, Any]) -> Path:
    """The tool's file: the registry path if it exists, else the TOOLBOX file."""
    p = Path(str(info.get("path") or ""))
    return p if str(p) not in ("", ".") and p.is_file() else _tool_file(name)


def _to_trash(path: Path, copy: bool = False) -> Optional[str]:
    """Move (or copy) a file into TOOLBOX/.trash; returns its new name."""
    if not path.is_file():
        return None
    t = _trash_dir()
    t.mkdir(parents=True, exist_ok=True)
    dest = t / f"{path.stem}-{_stamp()}{path.suffix}"
    n = 1
    while dest.exists():
        dest = t / f"{path.stem}-{_stamp()}-{n}{path.suffix}"
        n += 1
    (shutil.copy2 if copy else shutil.move)(str(path), str(dest))
    return dest.name


def _refresh() -> None:
    TOOL_SCHEMAS.clear()
    TOOL_SCHEMAS.extend(_generate_schemas())
    try:
        toolbox._update_toolbox_readme()
    except OSError as e:  # the README is only a convenience
        logger.info("TOOLBOX README not updated: %s", e)


def _reload(name: str, info: Dict[str, Any]) -> Optional[str]:
    """Load (or unload) one tool after a change; returns why it is not loaded."""
    TOOLS.pop(name, None)
    toolbox.SKIPPED_TOOLS.pop(name, None)
    if not info.get("enabled", True):
        return None
    path = _entry_path(name, info)
    if not path.is_file():
        return "file missing"
    func, problem = toolbox.load_toolbox_tool(name, path, check_code=info.get("author") != "user")
    if func is None:
        toolbox.SKIPPED_TOOLS[name] = str(problem)
        return str(problem)
    toolbox._install(name, func)
    return None


# ── reading code (never running it) ──────────────────────────────────────────
def _function(code: str, name: str) -> Optional[ast.FunctionDef | ast.AsyncFunctionDef]:
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    return next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
                None)


def _docstring(code: str, name: str) -> str:
    fn = _function(code, name)
    return (ast.get_docstring(fn) or "").strip() if fn else ""


def _static_value(code: str, name: str) -> Tuple[bool, Any]:
    """(True, value) when the tool only returns a fixed value (instructions as code).

    The function takes no required arguments and its body (after the
    docstring, optionally inside ``try:``) is a single ``return <literal>``.
    """
    fn = _function(code, name)
    if fn is None:
        return False, None
    a = fn.args
    if len(a.posonlyargs) + len(a.args) > len(a.defaults) or any(d is None for d in a.kw_defaults):
        return False, None
    body = list(fn.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if len(body) == 1 and isinstance(body[0], ast.Try):
        body = list(body[0].body)
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return False, None
    try:
        return True, ast.literal_eval(body[0].value)
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False, None


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:300]
    return ""


def _summary(text: str, limit: int = 300) -> str:
    """One line from a description: its first paragraph(s) before any heading."""
    paras: List[str] = []
    for block in re.split(r"\n\s*\n", (text or "").strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        if lines[0].startswith("#"):
            if paras:
                break
            lines = lines[1:]
            if not lines:
                continue
        paras.append(" ".join(lines))
        if sum(len(p) for p in paras) >= 60 or paras[0].endswith((".", "!", "?")):
            break
    out = " — ".join(paras[:2]) if len(paras) > 1 else (paras[0] if paras else "")
    out = " ".join(out.split())
    return out if len(out) <= limit else out[: limit - 1].rsplit(" ", 1)[0] + "…"


# ── listing ──────────────────────────────────────────────────────────────────
def list_tools() -> Dict[str, Any]:
    """Your tools with their state, plus what ``repair`` would fix."""
    reg = read_registry()
    tools = []
    for name, info in sorted(reg.items()):
        path = _entry_path(name, info)
        code = ""
        if path.is_file():
            try:
                code = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                code = ""
        static, _ = _static_value(code, name) if code else (False, None)
        tools.append({
            "name": name,
            "description": _summary(str(info.get("description") or _docstring(code, name))),
            "version": info.get("version", "1.0.0"),
            "author": info.get("author", "agent"),
            "enabled": bool(info.get("enabled", True)),
            "created": info.get("created"),
            "last_modified": info.get("last_modified"),
            "file": path.is_file(),
            "docs": _doc_file(name).is_file(),
            "loaded": name in TOOLS,
            "problem": toolbox.SKIPPED_TOOLS.get(name) or (None if path.is_file() else "file missing"),
            "instructions_only": static,
        })
    return {"tools": tools, "repair": repair(dry_run=True)["actions"]}


def get_tool(name: str) -> Dict[str, Any]:
    reg = read_registry()
    if name not in reg:
        raise ToolboxError(f"No tool named '{name}' in your TOOLBOX.")
    info = reg[name]
    path = _entry_path(name, info)
    code = path.read_text(encoding="utf-8") if path.is_file() else ""
    doc = _doc_file(name).read_text(encoding="utf-8") if _doc_file(name).is_file() else ""
    row = next(t for t in list_tools()["tools"] if t["name"] == name)
    return {**row, "code": code, "documentation": doc, "tags": info.get("tags") or []}


# ── create / edit / delete ───────────────────────────────────────────────────
def _check_new_code(name: str, code: str) -> None:
    if not name or not name.isidentifier() or name.startswith("_"):
        raise ToolboxError("A tool name is a Python name: letters, digits and _ (e.g. weather_report), "
                           "not starting with _ or a digit.")
    if not (code or "").strip():
        raise ToolboxError("Write the tool's code.")
    if len(code) > MAX_CODE:
        raise ToolboxError(f"A tool's code can be at most {MAX_CODE // 1000} kB.")
    try:
        compile(code, f"{name}.py", "exec")
    except SyntaxError as e:
        raise ToolboxError(f"Syntax error on line {e.lineno}: {e.msg}") from e
    if _function(code, name) is None:
        raise ToolboxError(f"The code must define a function named '{name}' (def {name}(...):).")


def _is_builtin(name: str, reg: Dict[str, Any]) -> bool:
    return name in TOOLS and name not in reg


def _render_doc(name: str, description: str, documentation: str) -> str:
    if documentation.strip():
        return documentation if documentation.endswith("\n") else documentation + "\n"
    return f"# {name}\n\n## Description\n{description.strip() or 'No description.'}\n"


def _bump(version: str) -> str:
    try:
        parts = [int(p) for p in str(version).split(".")]
        parts[-1] += 1
        return ".".join(str(p) for p in parts)
    except (ValueError, IndexError):
        return "1.0.1"


def save_tool(name: str, code: str, description: str = "", documentation: str = "",
              create: bool = False, author: str = "user", enabled: Optional[bool] = None,
              tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Create (``create=True``) or change one of your tools; the old version goes to the trash."""
    name = (name or "").strip()
    _check_new_code(name, code)
    if len(documentation or "") > MAX_DOC:
        raise ToolboxError(f"The documentation can be at most {MAX_DOC // 1000} kB.")
    reg = read_registry()
    if _is_builtin(name, reg):
        raise ToolboxError(f"'{name}' is a built-in tool; choose another name.")
    exists = name in reg
    if create and (exists or _tool_file(name).exists()):
        raise ToolboxError(f"A tool named '{name}' already exists — open it to edit, or choose another name.")
    if not create and not exists:
        raise ToolboxError(f"No tool named '{name}' in your TOOLBOX.")

    _dir().mkdir(parents=True, exist_ok=True)
    info = dict(reg.get(name) or {})
    path = _tool_file(name)
    kept = []
    old_path = _entry_path(name, info) if exists else path
    if old_path.is_file() and old_path.read_text(encoding="utf-8", errors="replace") != code:
        kept.append(_to_trash(old_path, copy=True))
    path.write_text(code, encoding="utf-8")
    if old_path != path and old_path.is_file() and old_path.parent == _dir():
        _to_trash(old_path)  # a stray file under another name

    desc = (description or info.get("description") or _first_line(_docstring(code, name)))[:2000]
    doc_text = _render_doc(name, desc, documentation or "")
    doc = _doc_file(name)
    if documentation or not doc.exists():
        if doc.is_file() and doc.read_text(encoding="utf-8", errors="replace") != doc_text:
            kept.append(_to_trash(doc, copy=True))
        doc.write_text(doc_text, encoding="utf-8")

    now = _now()
    info.update({
        "path": str(path), "name": name, "description": desc,
        "version": "1.0.0" if not exists else _bump(info.get("version", "1.0.0")),
        "author": author, "last_modified": now,
        "enabled": info.get("enabled", True) if enabled is None else bool(enabled),
    })
    info.setdefault("created", now)
    info.setdefault("tags", list(tags or []))
    if tags is not None:
        info["tags"] = [str(t)[:40] for t in tags][:20]
    info.setdefault("eval_score", None)
    info.setdefault("eval_count", 0)
    info.setdefault("errors", [])
    info.pop("_old_format", None)
    reg[name] = info
    _write_registry(reg)
    problem = _reload(name, info)
    _refresh()
    logger.info("Tool %s %s (by %s)", name, "created" if create else "changed", author)
    return {**get_tool(name), "problem": problem, "kept": [k for k in kept if k]}


def delete_tool(name: str) -> Dict[str, Any]:
    """Remove a tool: its files move to TOOLBOX/.trash (restorable)."""
    reg = read_registry()
    info = reg.get(name)
    if info is None:
        if _is_builtin(name, reg):
            raise ToolboxError(f"'{name}' is a built-in tool; it cannot be deleted.")
        raise ToolboxError(f"No tool named '{name}' in your TOOLBOX.")
    moved = []
    for p in {_entry_path(name, info), _tool_file(name), _doc_file(name), _tool_file(name).with_suffix(".py.bak")}:
        if p.is_file() and p.resolve().parent == _dir().resolve():  # only files inside TOOLBOX
            moved.append(_to_trash(p))
    entry = _trash_dir() / f"{name}-registry-{_stamp()}.json"
    _trash_dir().mkdir(parents=True, exist_ok=True)
    entry.write_text(json.dumps({name: {k: v for k, v in info.items() if not k.startswith("_")}}, indent=2),
                     encoding="utf-8")
    moved.append(entry.name)
    del reg[name]
    _write_registry(reg)
    TOOLS.pop(name, None)
    toolbox.SKIPPED_TOOLS.pop(name, None)
    _refresh()
    logger.info("Tool %s deleted (files kept in TOOLBOX/.trash)", name)
    return {"name": name, "trash": [m for m in moved if m]}


def set_enabled(name: str, enabled: bool) -> Dict[str, Any]:
    reg = read_registry()
    if name not in reg:
        raise ToolboxError(f"No tool named '{name}' in your TOOLBOX.")
    reg[name]["enabled"] = bool(enabled)
    reg[name]["last_modified"] = _now()
    _write_registry(reg)
    problem = _reload(name, reg[name])
    _refresh()
    return {"name": name, "enabled": bool(enabled), "loaded": name in TOOLS, "problem": problem}


# ── migrate: tool → skill ────────────────────────────────────────────────────
_SKIP_SECTIONS = {"code", "created", "error logging", "source", "source code", "implementation"}


def _doc_without_code(doc: str) -> str:
    """README text without the sections that only repeat the code."""
    out: List[str] = []
    skip = False
    in_fence = False
    for line in doc.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^(#{1,6})\s+(.*?)\s*$", line)
        if m and len(m.group(1)) >= 2:
            skip = m.group(2).strip().lower() in _SKIP_SECTIONS
        if not skip:
            out.append(line)
    text = "\n".join(out).strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _value_markdown(value: Any, depth: int = 0) -> str:
    pad = "  " * depth
    if isinstance(value, dict):
        lines = []
        for k, v in value.items():
            label = str(k).replace("_", " ").strip().capitalize()
            if isinstance(v, (dict, list, tuple)):
                lines.append(f"{pad}- **{label}**")
                lines.append(_value_markdown(v, depth + 1))
            else:
                lines.append(f"{pad}- **{label}:** {v}")
        return "\n".join(lines)
    if isinstance(value, (list, tuple)):
        return "\n".join(_value_markdown(v, depth) if isinstance(v, (dict, list, tuple)) else f"{pad}- {v}"
                         for v in value)
    return f"{pad}{value}"


def skill_from_tool(name: str) -> Dict[str, Any]:
    """The skill a tool would become (name, description, body) — nothing is saved."""
    t = get_tool(name)
    code, doc = t["code"], t["documentation"]
    static, value = _static_value(code, name)
    parts = []
    doc_text = _doc_without_code(doc) if doc else ""
    if doc_text:
        parts.append(doc_text)
    else:
        parts.append(f"# {name.replace('_', ' ').title()}")
        ds = _docstring(code, name)
        if ds:
            parts.append(ds)
    if static and value not in (None, "", {}, []):
        parts.append("## Details\n" + _value_markdown(value))
    body = "\n\n".join(p for p in parts if p.strip())
    if len(body) > agent_skills.MAX_BODY:
        body = body[: agent_skills.MAX_BODY - 200].rstrip() + "\n\n(Shortened when it was made a skill.)"
    reg_desc = read_registry().get(name, {}).get("description") or ""
    desc = _summary(reg_desc or _docstring(code, name) or body)  # one line: agents see it in their prompt
    return {"name": agent_skills.slug(name.replace("_", "-")), "description": desc[:300], "body": body,
            "instructions_only": static}


def tool_to_skill(name: str, skill_name: str = "", keep_tool: bool = False,
                  overwrite: bool = False) -> Dict[str, Any]:
    """Make a tool a skill; the tool is disabled (kept) unless ``keep_tool``."""
    draft = skill_from_tool(name)
    key = agent_skills.slug(skill_name) if skill_name else draft["name"]
    existing = agent_skills.get_skill(key)
    if existing and existing["source"] == "yours" and not overwrite:
        raise ToolboxError(f"You already have a skill named '{key}' — choose another name or replace it.")
    skill = agent_skills.save_skill(key, draft["description"], draft["body"], False)
    state = None if keep_tool else set_enabled(name, False)
    logger.info("Tool %s migrated to skill %s%s", name, key, "" if keep_tool else " (tool disabled)")
    return {"skill": skill, "tool": name, "tool_disabled": not keep_tool, "tool_state": state}


# ── migrate: repair the registry and old formats ─────────────────────────────
def repair(dry_run: bool = False) -> Dict[str, Any]:
    """Fix what is inconsistent; ``dry_run`` only lists it. Nothing is erased."""
    actions: List[Dict[str, str]] = []
    try:
        reg = read_registry()
    except ToolboxError as e:
        return {"actions": [{"kind": "registry_unreadable", "name": "", "text": str(e)}], "done": False}
    d = _dir()
    changed = False
    for name, info in list(reg.items()):
        if info.get("_old_format"):
            actions.append({"kind": "old_format", "name": name, "text": f"{name}: registry entry in the old format → updated"})
            info.pop("_old_format", None)
            info.update({"name": name, "version": "1.0.0", "author": "agent", "enabled": True,
                         "description": "", "created": _now(), "last_modified": _now(), "tags": []})
            changed = True
        p = Path(str(info.get("path") or ""))
        local = _tool_file(name)
        if not p.is_file() and local.is_file():
            actions.append({"kind": "path", "name": name,
                            "text": f"{name}: file path points elsewhere (another computer?) → {local.name}"})
            info["path"] = str(local)
            changed = True
        elif not p.is_file() and not local.is_file():
            actions.append({"kind": "missing_file", "name": name,
                            "text": f"{name}: its file is gone → registry entry moved to TOOLBOX/.trash"})
            if not dry_run:
                _trash_dir().mkdir(parents=True, exist_ok=True)
                (_trash_dir() / f"{name}-registry-{_stamp()}.json").write_text(
                    json.dumps({name: {k: v for k, v in info.items() if not k.startswith("_")}}, indent=2),
                    encoding="utf-8")
                if _doc_file(name).is_file():
                    _to_trash(_doc_file(name))
            del reg[name]
            changed = True
    if d.is_dir():
        for f in sorted(d.glob("*.py")):
            n = f.stem
            if n in reg or not n.isidentifier():
                continue
            try:
                code = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            if _function(code, n) is None:
                continue
            actions.append({"kind": "unregistered", "name": n,
                            "text": f"{n}: tool file not in the registry → added, disabled (turn it on after a look)"})
            reg[n] = {"path": str(f), "name": n, "version": "1.0.0", "author": "agent", "enabled": False,
                      "description": _first_line(_docstring(code, n)), "created": _now(),
                      "last_modified": _now(), "tags": [], "eval_score": None, "eval_count": 0, "errors": []}
            changed = True
        for f in sorted(d.glob(f"*{_DOC_SUFFIX}")):
            n = f.name[: -len(_DOC_SUFFIX)]
            if n in reg or (d / f"{n}.py").exists():
                continue
            actions.append({"kind": "orphan_doc", "name": n,
                            "text": f"{f.name}: README without a tool → moved to TOOLBOX/.trash"})
            if not dry_run:
                _to_trash(f)
    # old flat skill files (skills/<name>.md) → skills/<name>/SKILL.md
    sd = agent_skills.user_dir()
    if sd.is_dir():
        for f in sorted(sd.glob("*.md")):
            if f.name.lower() == "readme.md":
                continue
            key = agent_skills.slug(f.stem)
            if not key or (sd / key / "SKILL.md").exists():
                continue
            actions.append({"kind": "old_skill_file", "name": key,
                            "text": f"skills/{f.name}: old single-file skill → skills/{key}/SKILL.md"})
            if not dry_run:
                try:
                    agent_skills.import_skill(f.read_text(encoding="utf-8"), key)
                except (OSError, ValueError) as e:
                    actions[-1]["text"] += f" (not done: {e})"
    if changed and not dry_run:
        _write_registry(reg)
        for name, info in reg.items():
            if name not in TOOLS or toolbox.SKIPPED_TOOLS.get(name):
                _reload(name, info)
        _refresh()
    return {"actions": actions, "done": not dry_run}


# ── export / import as a file ────────────────────────────────────────────────
def export_bundle(tools: Optional[List[str]] = None, skills: Optional[List[str]] = None) -> Dict[str, Any]:
    """A bundle with these tools and skills (None = all of yours)."""
    reg = read_registry()
    tool_names = sorted(reg) if tools is None else list(dict.fromkeys(tools))
    out_tools = []
    for n in tool_names:
        if n not in reg:
            raise ToolboxError(f"No tool named '{n}' in your TOOLBOX.")
        t = get_tool(n)
        if not t["code"]:
            continue  # nothing to export (file missing)
        out_tools.append({"name": n, "description": t["description"], "code": t["code"],
                          "documentation": t["documentation"], "version": t["version"],
                          "tags": t.get("tags") or [], "enabled": t["enabled"]})
    if skills is None:
        skill_rows = [s for s in agent_skills.list_skills(include_body=True) if s["source"] == "yours"]
    else:
        skill_rows = []
        for n in dict.fromkeys(skills):
            s = agent_skills.get_skill(n)
            if s is None:
                raise ToolboxError(f"No skill named '{n}'.")
            skill_rows.append(s)
    out_skills = [{"name": s["name"], "description": s["description"], "always": bool(s["always"]),
                   "body": s["body"]} for s in skill_rows]
    try:
        from .. import __version__ as app_version
    except ImportError:  # pragma: no cover
        app_version = ""
    return {"format": BUNDLE_FORMAT, "version": BUNDLE_VERSION, "exported_at": _now(),
            "app_version": app_version, "tools": out_tools, "skills": out_skills}


def _import_tool(item: Dict[str, Any], overwrite: bool, trust: bool) -> Dict[str, Any]:
    name = str(item.get("name") or "").strip()
    code = str(item.get("code") or "")
    _check_new_code(name, code)
    reg = read_registry()
    if _is_builtin(name, reg):
        raise ToolboxError(f"'{name}' is a built-in tool name.")
    exists = name in reg or _tool_file(name).exists()
    if exists and not overwrite:
        raise ToolboxError("exists")
    problem = None if trust else toolbox._validate_tool_ast(code)
    tags = item.get("tags") if isinstance(item.get("tags"), list) else None
    want_on = bool(item.get("enabled", True))
    if exists and name not in reg:  # a stray file of that name: keep it, then register fresh
        _to_trash(_tool_file(name))
        exists = False
    saved = save_tool(name, code, str(item.get("description") or ""), str(item.get("documentation") or ""),
                      create=not exists, author="user" if trust else "imported",
                      enabled=want_on and problem is None, tags=tags)
    return {"kind": "tool", "name": name, "enabled": saved["enabled"], "loaded": saved["loaded"],
            "replaced": exists, "problem": (problem or "").removeprefix("Error: ") or saved.get("problem")}


def _import_skill(item: Dict[str, Any], overwrite: bool) -> Dict[str, Any]:
    key = agent_skills.slug(str(item.get("name") or ""))
    if not key:
        raise ToolboxError("A skill in the file has no name.")
    existing = agent_skills.get_skill(key)
    if existing and existing["source"] == "yours" and not overwrite:
        raise ToolboxError("exists")
    agent_skills.save_skill(key, str(item.get("description") or ""), str(item.get("body") or ""),
                            bool(item.get("always")))
    return {"kind": "skill", "name": key, "replaced": bool(existing and existing["source"] == "yours")}


def import_file(text: str, filename: str = "", overwrite: bool = False, trust: bool = False) -> Dict[str, Any]:
    """Import a bundle (.json), one tool (.py) or one skill (.md)."""
    if len(text or "") > MAX_BUNDLE:
        raise ToolboxError(f"The file is too large (at most {MAX_BUNDLE // 1_000_000} MB).")
    fname = (filename or "").strip()
    low = fname.lower()
    tools: List[Dict[str, Any]] = []
    skills: List[Dict[str, Any]] = []
    stripped = (text or "").lstrip("﻿").strip()
    if low.endswith(".json") or (stripped.startswith("{") and not low.endswith((".py", ".md"))):
        try:
            data = json.loads(stripped)
        except ValueError as e:
            raise ToolboxError(f"The file is not valid JSON ({e}).") from e
        if not isinstance(data, dict) or data.get("format") != BUNDLE_FORMAT:
            raise ToolboxError("This JSON file is not a ZenSynora export (format 'zensynora-bundle').")
        if int(data.get("version") or 0) > BUNDLE_VERSION:
            raise ToolboxError("The file was exported by a newer ZenSynora — update first.")
        tools = [t for t in data.get("tools") or [] if isinstance(t, dict)]
        skills = [s for s in data.get("skills") or [] if isinstance(s, dict)]
    elif low.endswith(".py"):
        stem = Path(fname).stem
        name = stem if _function(text, stem) else ""
        if not name:
            try:
                tree = ast.parse(text)
            except SyntaxError as e:
                raise ToolboxError(f"Syntax error on line {e.lineno}: {e.msg}") from e
            fns = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                   and not n.name.startswith("_")]
            if not fns:
                raise ToolboxError("The .py file defines no function to use as a tool.")
            name = fns[0]
        tools = [{"name": name, "code": text, "description": _first_line(_docstring(text, name))}]
    elif low.endswith(".md") or stripped.startswith("---") or not stripped.startswith("{"):
        base = Path(fname).stem if fname else ""
        sk = agent_skills.parse(text, "" if base.upper() == "SKILL" else base)
        if not sk["name"]:
            raise ToolboxError("The skill file has no name — add 'name: …' at the top, or rename the file.")
        skills = [sk]
    if len(tools) + len(skills) > MAX_ITEMS:
        raise ToolboxError(f"At most {MAX_ITEMS} tools and skills in one file.")
    if not tools and not skills:
        raise ToolboxError("The file contains no tools or skills.")

    imported: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for kind, items in (("tool", tools), ("skill", skills)):
        for item in items:
            name = str(item.get("name") or "?")
            try:
                imported.append(_import_tool(item, overwrite, trust) if kind == "tool"
                                else _import_skill(item, overwrite))
            except ToolboxError as e:
                reason = (f"a {kind} named '{name}' already exists — import again with 'replace' to overwrite it "
                          "(the current one is kept in the trash)") if str(e) == "exists" else str(e)
                skipped.append({"kind": kind, "name": name, "reason": reason})
            except ValueError as e:  # agent_skills validation
                skipped.append({"kind": kind, "name": name, "reason": str(e)})
    logger.info("Import of %s: %d imported, %d skipped", fname or "file", len(imported), len(skipped))
    return {"imported": imported, "skipped": skipped}
