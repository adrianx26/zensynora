"""Agent skills (v0.7.5): reusable instructions an agent loads when a task needs them.

A skill is a Markdown file with a short header::

    ---
    name: web-research
    description: Find current facts on the web and cite the pages used.
    always: false
    ---
    # Web research
    1. …

Where skills come from
----------------------
* **Built-in** — ``myclaw/skills_builtin/<name>/SKILL.md`` (shipped, read-only).
* **Yours** — ``~/.myclaw/skills/<name>/SKILL.md`` (or ``<name>.md``). Made on
  the Skills page, imported, or copied from a built-in one to change it. A skill
  of yours with the same name as a built-in one replaces it.

How agents use them
-------------------
Each agent has a list of skills (Settings → Agents, or the Skills page;
``agents.defaults.skills`` for the default agent, ``agents.named[].skills``).
Its system prompt lists those skills by name and description; the agent calls
the ``use_skill`` tool to read the full instructions when a task matches. A skill
marked ``always: true`` is included in full instead (useful for small models
that rarely call tools).

The older ``.py`` files in ``~/.myclaw/skills`` (ZenHub tools) are not skills in
this sense and are left alone.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

BUILTIN_DIR = Path(__file__).resolve().parent / "skills_builtin"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
MAX_BODY = 40_000


def user_dir() -> Path:
    return Path.home() / ".myclaw" / "skills"


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s[:63]


# ── parsing ──────────────────────────────────────────────────────────────────
def parse(text: str, fallback_name: str = "") -> dict[str, Any]:
    """SKILL.md text → {name, description, always, body}."""
    meta: dict[str, Any] = {}
    body = text or ""
    m = re.match(r"^﻿?---\s*\n(.*?)\n---\s*\n?", body, re.DOTALL)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.startswith((" ", "\t", "#")):
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"').strip("'")
        body = body[m.end():]
    body = body.strip()
    desc = meta.get("description", "")
    if not desc:  # first plain line of text
        for line in body.splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                desc = line
                break
    return {
        "name": slug(meta.get("name") or fallback_name),
        "description": desc[:300],
        "always": str(meta.get("always", "")).lower() in ("true", "yes", "1"),
        "body": body,
    }


def render(skill: dict[str, Any]) -> str:
    desc = str(skill.get("description", "")).replace("\n", " ").strip()
    head = f"---\nname: {skill['name']}\ndescription: {desc}\n"
    if skill.get("always"):
        head += "always: true\n"
    return head + "---\n\n" + str(skill.get("body", "")).strip() + "\n"


# ── finding skills ───────────────────────────────────────────────────────────
def _files(base: Path) -> list[Path]:
    if not base.is_dir():
        return []
    out = sorted(p for p in base.glob("*/SKILL.md") if p.is_file())
    out += sorted(p for p in base.glob("*.md") if p.is_file() and p.name.lower() != "readme.md")
    return out


def _load(path: Path, source: str) -> dict[str, Any] | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Cannot read skill %s: %s", path, e)
        return None
    fallback = path.parent.name if path.name == "SKILL.md" else path.stem
    sk = parse(text, fallback)
    if not sk["name"]:
        return None
    sk.update(source=source, path=str(path))
    return sk


def list_skills(include_body: bool = False) -> list[dict[str, Any]]:
    """All skills; yours replace built-in ones with the same name."""
    found: dict[str, dict[str, Any]] = {}
    for path in _files(BUILTIN_DIR):
        sk = _load(path, "builtin")
        if sk:
            found[sk["name"]] = sk
    for path in _files(user_dir()):
        sk = _load(path, "yours")
        if sk:
            if sk["name"] in found:
                sk["overrides_builtin"] = True
            found[sk["name"]] = sk
    out = sorted(found.values(), key=lambda s: (s["source"] != "yours", s["name"]))
    if not include_body:
        for s in out:
            s["size"] = len(s.pop("body"))
    return out


def get_skill(name: str) -> dict[str, Any] | None:
    key = slug(name)
    return next((s for s in list_skills(include_body=True) if s["name"] == key), None)


# ── changing your skills ─────────────────────────────────────────────────────
def trash_dir() -> Path:
    """Where replaced and deleted skills are kept (2026-10-31): skills/.trash."""
    return user_dir() / ".trash"


def _keep(path: Path, key: str) -> str | None:
    """Move a skill file or folder into the trash; returns its name there."""
    if not path.exists():
        return None
    t = trash_dir()
    t.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = t / (f"{key}-{stamp}" + ("" if path.is_dir() else ".md"))
    n = 1
    while dest.exists():
        dest = t / (f"{key}-{stamp}-{n}" + ("" if path.is_dir() else ".md"))
        n += 1
    shutil.move(str(path), str(dest))
    return dest.name


def export_skill(name: str) -> tuple[str, str]:
    """(file name, SKILL.md text) of any skill — yours or built-in."""
    sk = get_skill(name)
    if sk is None:
        raise ValueError(f"No skill named '{name}'.")
    return f"{sk['name']}.SKILL.md", render(sk)


def save_skill(name: str, description: str, body: str, always: bool = False) -> dict[str, Any]:
    key = slug(name)
    if not NAME_RE.match(key):
        raise ValueError("A skill name uses letters, digits and dashes (e.g. web-research).")
    if not (body or "").strip():
        raise ValueError("Write the skill's instructions.")
    if len(body) > MAX_BODY:
        raise ValueError(f"A skill can be at most {MAX_BODY // 1000} kB.")
    folder = user_dir() / key
    folder.mkdir(parents=True, exist_ok=True)
    text = render({"name": key, "description": description, "body": body, "always": always})
    target = folder / "SKILL.md"
    # 2026-10-31: the previous version is kept in skills/.trash, never overwritten
    if target.is_file() and target.read_text(encoding="utf-8", errors="replace") != text:
        t = trash_dir()
        t.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, t / f"{key}-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.md")
    target.write_text(text, encoding="utf-8")
    flat = user_dir() / f"{key}.md"  # an older flat file would shadow nothing but confuse
    if flat.exists():
        _keep(flat, key)
    return get_skill(key)  # type: ignore[return-value]


def import_skill(text: str, name: str = "") -> dict[str, Any]:
    sk = parse(text, name)
    if not sk["name"]:
        raise ValueError("The file has no skill name — add 'name: …' at the top or give a name.")
    return save_skill(sk["name"], sk["description"], sk["body"], sk["always"])


def delete_skill(name: str) -> bool:
    key = slug(name)
    removed = False
    folder = user_dir() / key
    # 2026-10-31: deleted skills move to skills/.trash (restorable), not erased
    if (folder / "SKILL.md").exists():
        _keep(folder, key)
        removed = True
    flat = user_dir() / f"{key}.md"
    if flat.exists():
        _keep(flat, key)
        removed = True
    return removed


# ── agents ───────────────────────────────────────────────────────────────────
def skills_for(config: Any, agent_name: str) -> list[str]:
    if agent_name == "default":
        return list(getattr(config.agents.defaults, "skills", []) or [])
    for a in config.agents.named:
        if a.name == agent_name:
            return list(getattr(a, "skills", []) or [])
    return []


def prompt_section(names: list[str]) -> str:
    """The part of an agent's system prompt that tells it about its skills."""
    if not names:
        return ""
    by_name = {s["name"]: s for s in list_skills(include_body=True)}
    picked = [by_name[n] for n in dict.fromkeys(names) if n in by_name]
    if not picked:
        return ""
    lines = ["## Your skills",
             "Before a task that matches one of these, call use_skill(name) and follow the instructions it "
             "returns."]
    full = []
    for s in picked:
        lines.append(f"- {s['name']}: {s['description']}")
        if s.get("always"):
            full.append(f"### Skill: {s['name']}\n{s['body']}")
    if full:
        lines.append("\nThese skills always apply:\n\n" + "\n\n".join(full))
    return "\n".join(lines)


# ── tools for agents ─────────────────────────────────────────────────────────
def use_skill(name: str) -> str:
    """Load a skill's full instructions by name (see the skills listed in your system prompt).

    name: the skill name, e.g. web-research
    """
    sk = get_skill(name)
    if sk is None:
        names = ", ".join(s["name"] for s in list_skills()) or "none"
        return f"No skill named '{name}'. Available skills: {names}"
    return f"# Skill: {sk['name']}\n\n{sk['body']}"


def list_agent_skills() -> str:
    """List all skills (name and what each is for). Load one with use_skill(name)."""
    rows = list_skills()
    if not rows:
        return "No skills yet. Add them on the Skills page of the Web UI."
    return "\n".join(f"- {s['name']}: {s['description']}" for s in rows)
