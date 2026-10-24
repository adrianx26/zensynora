"""Architecture overview of a code repository or a folder (KB phase 5, 2026-10-24).

Used for GitHub repositories (file list from the REST API) and local folders
(file list from the disk) alike: languages, folders, entry points,
dependencies, the key files (README, docs, manifests, entry points, main
modules) as part notes, imports between files/top-level packages (Python,
JavaScript/TypeScript; regex, no parsing) and a Mermaid diagram of the
folders and their imports.
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

from .documents import CODE_LANG, IMAGE_EXT, MD_EXT, SPECIAL_NAMES, TEXT_EXT
from .model import Budget, Document, Section
from .notes import mermaid_label
from .safety import is_secret_file, skip_dir

logger = logging.getLogger(__name__)

MAX_FILE_BYTES = 200_000       # larger files are listed, not read
MAX_PART_CHARS = 24_000        # text kept from one file (≈ 2 notes)
MAX_READ_FILES = 80

LANG_NAMES = {
    "python": "Python", "javascript": "JavaScript", "typescript": "TypeScript", "tsx": "TypeScript",
    "jsx": "JavaScript", "go": "Go", "rust": "Rust", "java": "Java", "kotlin": "Kotlin",
    "swift": "Swift", "ruby": "Ruby", "php": "PHP", "c": "C", "cpp": "C++", "csharp": "C#",
    "bash": "Shell", "powershell": "PowerShell", "lua": "Lua", "sql": "SQL", "r": "R",
    "scala": "Scala", "dart": "Dart", "vue": "Vue", "svelte": "Svelte", "css": "CSS",
    "scss": "CSS", "elixir": "Elixir", "zig": "Zig", "julia": "Julia", "perl": "Perl",
    "objectivec": "Objective-C", "hcl": "Terraform", "protobuf": "Protobuf", "graphql": "GraphQL",
    "nim": "Nim", "groovy": "Groovy", "dockerfile": "Dockerfile", "makefile": "Makefile",
}
NON_CODE_LANGS = {"toml", "yaml", "ini", "xml", "text"}

MANIFESTS = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "go.mod",
    "cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "gemfile", "composer.json",
    "pipfile", "environment.yml", "dockerfile", "docker-compose.yml", "docker-compose.yaml",
    "compose.yaml", "makefile", "deno.json", "mix.exs", "pubspec.yaml",
}
AGENT_DOCS = {"agents.md", "claude.md", "gemini.md", ".cursorrules", "contributing.md",
              "architecture.md", "design.md", "copilot-instructions.md"}
ENTRY_NAMES = {
    "__main__.py", "main.py", "cli.py", "app.py", "server.py", "manage.py", "wsgi.py", "asgi.py",
    "index.js", "index.ts", "main.js", "main.ts", "server.js", "server.ts", "app.js", "app.ts",
    "index.tsx", "main.tsx", "main.go", "main.rs", "lib.rs", "program.cs", "main.java",
    "application.java", "main.kt", "index.php", "main.swift",
}
BINARY_EXT = IMAGE_EXT | {
    ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war", ".exe", ".dll", ".so",
    ".dylib", ".bin", ".o", ".a", ".class", ".pyc", ".pyo", ".whl", ".egg", ".ico", ".icns",
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".mp3", ".mp4", ".wav", ".ogg", ".webm", ".mov",
    ".avi", ".mkv", ".flac", ".pdf", ".psd", ".ai", ".sketch", ".fig", ".db", ".sqlite",
    ".sqlite3", ".parquet", ".npy", ".npz", ".pt", ".pth", ".onnx", ".h5", ".pkl", ".lock",
    ".map", ".min.js",
}
LOCK_NAMES = {"package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "cargo.lock",
              "composer.lock", "gemfile.lock", "go.sum", "uv.lock", "pipfile.lock"}


@dataclass
class TreeEntry:
    path: str     # relative, '/' separators
    size: int = 0


def language_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1].lower()
    ext = posixpath.splitext(name)[1]
    return CODE_LANG.get(ext) or SPECIAL_NAMES.get(name, "")


def usable(entries: Iterable[TreeEntry]) -> Tuple[List[TreeEntry], List[str]]:
    """Entries without hidden/tool folders and secret files; plus what was skipped."""
    keep: List[TreeEntry] = []
    skipped: List[str] = []
    for e in entries:
        parts = e.path.split("/")
        if any(skip_dir(p) for p in parts[:-1]):
            continue
        name = parts[-1]
        if is_secret_file(name):
            skipped.append(f"{e.path} (may contain secrets)")
            continue
        if name.startswith(".") and name.lower() not in AGENT_DOCS and name != ".gitignore":
            continue
        keep.append(e)
    return keep, skipped


def _priority(e: TreeEntry, entry_points: Set[str]) -> Tuple[int, int, str]:
    path = e.path
    name = path.rsplit("/", 1)[-1].lower()
    depth = path.count("/")
    ext = posixpath.splitext(name)[1]
    if depth == 0 and name.startswith("readme"):
        return (0, depth, path)
    if depth == 0 and name in MANIFESTS:
        return (1, depth, path)
    if name in AGENT_DOCS and depth <= 2:
        return (2, depth, path)
    if path in entry_points or (name in ENTRY_NAMES and depth <= 2):
        return (3, depth, path)
    if ext in MD_EXT and (depth == 0 or path.lower().startswith(("docs/", "doc/"))) and depth <= 2:
        return (4, depth, path)
    if name == "__init__.py" and depth == 1:
        return (5, depth, path)
    if name.startswith("readme") and depth <= 2:
        return (5, depth, path)
    if language_of(path) and language_of(path) not in NON_CODE_LANGS and depth <= 2:
        return (6, depth, path)
    if ext in MD_EXT | TEXT_EXT or name in MANIFESTS:
        return (7, depth, path)
    if language_of(path):
        return (8, depth, path)
    return (9, depth, path)


def _readable(e: TreeEntry) -> bool:
    name = e.path.rsplit("/", 1)[-1].lower()
    if name in LOCK_NAMES or name.endswith(".min.js") or name.endswith(".min.css"):
        return False
    ext = posixpath.splitext(name)[1]
    if ext in BINARY_EXT and ext not in {".pdf"}:
        return False
    return e.size <= MAX_FILE_BYTES


# ── manifests ────────────────────────────────────────────────────────────────
def parse_manifest(name: str, text: str) -> Tuple[List[str], List[str], str]:
    """``(dependencies, entry points, description)`` from a manifest file."""
    low = name.lower()
    deps: List[str] = []
    entries: List[str] = []
    desc = ""
    try:
        if low == "pyproject.toml":
            try:
                import tomllib
            except ImportError:  # Python < 3.11 is not supported anyway
                return deps, entries, desc
            data = tomllib.loads(text)
            proj = data.get("project") or {}
            deps = [str(d) for d in proj.get("dependencies") or []]
            desc = str(proj.get("description") or "")
            for script, target in (proj.get("scripts") or {}).items():
                entries.append(f"{script} → {target}")
            poetry = ((data.get("tool") or {}).get("poetry") or {})
            for k, v in (poetry.get("dependencies") or {}).items():
                if k.lower() != "python":
                    deps.append(f"{k} {v}" if isinstance(v, str) else k)
            for script, target in (poetry.get("scripts") or {}).items():
                entries.append(f"{script} → {target}")
        elif low.startswith("requirements") and low.endswith(".txt"):
            for line in text.splitlines():
                line = line.split("#", 1)[0].strip()
                if line and not line.startswith("-"):
                    deps.append(line)
        elif low == "package.json":
            data = json.loads(text)
            desc = str(data.get("description") or "")
            for key in ("dependencies", "peerDependencies"):
                for k, v in (data.get(key) or {}).items():
                    deps.append(f"{k} {v}")
            if data.get("main"):
                entries.append(f"main → {data['main']}")
            binv = data.get("bin")
            if isinstance(binv, dict):
                entries += [f"{k} → {v}" for k, v in binv.items()]
            elif isinstance(binv, str):
                entries.append(f"bin → {binv}")
            for k in ("start", "dev", "build"):
                if (data.get("scripts") or {}).get(k):
                    entries.append(f"npm run {k} → {data['scripts'][k]}")
        elif low == "go.mod":
            block = False
            for line in text.splitlines():
                s = line.strip()
                if s.startswith("require ("):
                    block = True
                elif block and s == ")":
                    block = False
                elif block and s and not s.startswith("//"):
                    deps.append(s.split("//")[0].strip())
                elif s.startswith("require "):
                    deps.append(s[8:].strip())
        elif low == "cargo.toml":
            import tomllib

            data = tomllib.loads(text)
            for k, v in (data.get("dependencies") or {}).items():
                deps.append(f"{k} {v}" if isinstance(v, str) else k)
            desc = str((data.get("package") or {}).get("description") or "")
    except Exception as e:  # a broken manifest is only less information
        logger.debug("Ingest: could not parse %s: %s", name, e)
    return deps[:200], entries[:30], desc


# ── imports ──────────────────────────────────────────────────────────────────
_PY_IMPORT = re.compile(r"^\s*(?:from\s+(\.*[\w.]*)\s+import\s+([\w*, ()]+)|import\s+([\w.]+))", re.M)
_JS_IMPORT = re.compile(r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|^\s*import\s+)['"]([^'"]+)['"]""", re.M)


def _py_module_files(module: str, paths: Set[str], roots: List[str]) -> Optional[str]:
    rel = module.replace(".", "/")
    for root in roots:
        prefix = f"{root}/" if root else ""
        for cand in (f"{prefix}{rel}.py", f"{prefix}{rel}/__init__.py"):
            if cand in paths:
                return cand
    return None


def find_imports(path: str, text: str, all_paths: Set[str], py_roots: List[str]) -> List[str]:
    """Files of this repository that ``path`` imports (Python / JS / TS)."""
    lang = language_of(path)
    out: List[str] = []
    if lang == "python":
        pkg = posixpath.dirname(path)
        for m in _PY_IMPORT.finditer(text[:200_000]):
            mod = m.group(1) if m.group(1) is not None else m.group(3)
            names = [n.strip(" ()") for n in (m.group(2) or "").split(",") if n.strip(" ()")]
            if mod is None:
                continue
            if mod.startswith("."):
                dots = len(mod) - len(mod.lstrip("."))
                base = pkg
                for _ in range(dots - 1):
                    base = posixpath.dirname(base)
                rest = mod.lstrip(".")
                cands = []
                target = f"{base}/{rest.replace('.', '/')}" if rest else base
                target = target.lstrip("/")
                cands.append(target)
                cands += [f"{target}/{n}" for n in names if n != "*"]
                for c in cands:
                    for f in (f"{c}.py", f"{c}/__init__.py"):
                        if f in all_paths and f != path and f not in out:
                            out.append(f)
                            break
            else:
                hit = _py_module_files(mod, all_paths, py_roots)
                if hit is None:
                    for n in names:
                        hit = _py_module_files(f"{mod}.{n}", all_paths, py_roots)
                        if hit:
                            break
                if hit and hit != path and hit not in out:
                    out.append(hit)
    elif lang in ("javascript", "typescript", "tsx", "jsx", "vue", "svelte"):
        base = posixpath.dirname(path)
        for spec in _JS_IMPORT.findall(text[:200_000]):
            if not spec.startswith("."):
                continue
            target = posixpath.normpath(posixpath.join(base, spec))
            for cand in (target, *(target + e for e in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".vue")),
                         *(f"{target}/index{e}" for e in (".ts", ".tsx", ".js", ".jsx"))):
                if cand in all_paths and cand != path:
                    if cand not in out:
                        out.append(cand)
                    break
    return out


def module_imports(path: str, text: str, top_dirs: Set[str]) -> Set[str]:
    """Top-level folders a Python file imports by absolute name (cheap, no file list)."""
    out: Set[str] = set()
    if language_of(path) != "python":
        return out
    for m in _PY_IMPORT.finditer(text[:200_000]):
        mod = m.group(1) if m.group(1) is not None else m.group(3)
        if mod and not mod.startswith("."):
            top = mod.split(".", 1)[0]
            if top in top_dirs:
                out.add(top)
    return out


# ── content tags (a light version of memopad's content detector) ─────────────
_DETECT = [
    ("agent-instructions", re.compile(r"(AGENTS\.md|CLAUDE\.md|system[_ -]?prompt|\.cursorrules|copilot-instructions)", re.I)),
    ("api", re.compile(r"(@app\.(get|post|put|delete)|@router\.|APIRouter|express\(\)|FastAPI\(|flask)", re.I)),
    ("tools", re.compile(r"(@tool\b|@mcp\.tool|register_tool|function[_ ]calling|tool_schema)", re.I)),
    ("config", re.compile(r"(pyproject\.toml|package\.json|dockerfile|docker-compose|makefile|\.ya?ml$|\.toml$)", re.I)),
    ("tests", re.compile(r"(^|/)(tests?|spec)(/|_)", re.I)),
]


def content_tags(path: str, text: str) -> List[str]:
    combined = path + "\n" + text[:3000]
    return [tag for tag, rx in _DETECT if rx.search(combined if tag != "tests" else path)]


# ── the analysis ─────────────────────────────────────────────────────────────
def analyze_repo(*, title: str, source: str, kind: str, permalink_hint: str,
                 entries: List[TreeEntry], read: Callable[[str], Optional[str]],
                 budget: Budget, max_files: int, description: str = "",
                 link_base: str = "", extra_facts: Iterable[Tuple[str, str]] = ()) -> Document:
    """Build the architecture :class:`Document` of a repository or folder.

    ``read(path)`` returns a file's text (Markdown for documents) or None.
    ``link_base`` (e.g. ``https://github.com/o/r/blob/main/``) gives each file
    note a link to the original.
    """
    entries, skipped = usable(entries)
    doc = Document(title=title, source=source, kind=kind, permalink_hint=permalink_hint,
                   split_all=True)
    doc.skipped += skipped
    doc.size = sum(e.size for e in entries)
    doc.facts += list(extra_facts)
    paths = {e.path for e in entries}
    if not entries:
        doc.summary = "No readable files were found."
        return doc

    # languages and folders
    langs: Counter = Counter()
    lang_bytes: Counter = Counter()
    for e in entries:
        lang = language_of(e.path)
        if lang and lang not in NON_CODE_LANGS:
            name = LANG_NAMES.get(lang, lang)
            langs[name] += 1
            lang_bytes[name] += e.size
    folders: Counter = Counter()
    sub: Dict[str, Counter] = defaultdict(Counter)
    root_files = 0
    for e in entries:
        parts = e.path.split("/")
        if len(parts) == 1:
            root_files += 1
            continue
        folders[parts[0]] += 1
        if len(parts) > 2:
            sub[parts[0]][parts[1]] += 1
    top_dirs = set(folders)
    py_roots = [""] + (["src"] if "src" in top_dirs else [])

    # manifests first: dependencies, entry points, description
    deps: List[Tuple[str, str]] = []
    dep_names: Set[str] = set()
    entry_points: List[str] = []
    texts: Dict[str, str] = {}
    candidates = sorted((e for e in entries if _readable(e)), key=lambda e: _priority(e, set()))
    for e in candidates:
        name = e.path.rsplit("/", 1)[-1].lower()
        if e.path.count("/") == 0 and (name in MANIFESTS or (
                name.startswith("requirements") and name.endswith(".txt") and "lock" not in name)):
            text = _read(read, e, budget, doc)
            if text is None:
                continue
            texts[e.path] = text
            d, ep, desc = parse_manifest(name, text)
            for dep in d:
                dep_name = _DEP_NAME.split(dep, maxsplit=1)[0].lower()
                if dep_name and dep_name not in dep_names:
                    dep_names.add(dep_name)
                    deps.append((dep, e.path))
            entry_points += ep
            description = description or desc
    for e in entries:
        name = e.path.rsplit("/", 1)[-1].lower()
        if name in ENTRY_NAMES and e.path.count("/") <= 2:
            entry_points.append(e.path)

    # key files
    ep_paths = {p for p in entry_points if p in paths}
    ranked = sorted((e for e in candidates), key=lambda e: _priority(e, ep_paths))
    chosen: List[TreeEntry] = []
    for e in ranked:
        if len(chosen) >= max_files:
            break
        if _priority(e, ep_paths)[0] >= 9:
            continue
        chosen.append(e)
    not_read = len([e for e in ranked if _priority(e, ep_paths)[0] < 9]) - len(chosen)
    if not_read > 0:
        doc.skipped.append(f"{not_read} more files not read (limit of {max_files} files per run)")

    sections: List[Section] = []
    folder_edges: Set[Tuple[str, str]] = set()
    for e in chosen:
        if budget.out_of_time(margin=10.0):
            doc.stopped = budget.stop_reason() or "time limit reached"
            doc.skipped.append(f"{len(chosen) - len(sections)} files not read ({doc.stopped})")
            break
        text = texts.get(e.path)
        if text is None:
            text = _read(read, e, budget, doc)
        if text is None:
            continue
        texts[e.path] = text
        lang = language_of(e.path)
        ext = posixpath.splitext(e.path)[1].lower()
        body = text
        cut = len(body) > MAX_PART_CHARS
        if cut:
            body = body[:MAX_PART_CHARS]
        if lang and ext not in MD_EXT:
            body = f"```{lang}\n{body.rstrip()}\n```"
        if cut:
            body += f"\n\n(… only the first {MAX_PART_CHARS:,} characters of {len(text):,})"
        if link_base:
            body = f"Original: <{link_base}{e.path}>\n\n{body}"
        links = [("imports", p) for p in find_imports(e.path, text, paths, py_roots)]
        for m in re.findall(r"\]\(([^)\s#]+\.md)\)", text) if ext in MD_EXT else []:
            target = posixpath.normpath(posixpath.join(posixpath.dirname(e.path), m))
            if target in paths:
                links.append(("links_to", target))
        sections.append(Section(heading=e.path, text=body, key=e.path, links=links,
                                tags=content_tags(e.path, text)))
        src_top = e.path.split("/", 1)[0] if "/" in e.path else ""
        for rel, target in links:
            if rel != "imports":
                continue
            dst_top = target.split("/", 1)[0] if "/" in target else ""
            if src_top and dst_top and src_top != dst_top:
                folder_edges.add((src_top, dst_top))
        if src_top:
            for dst_top in module_imports(e.path, text, top_dirs):
                if dst_top != src_top:
                    folder_edges.add((src_top, dst_top))
        budget.report(f"read {len(sections)}/{len(chosen)} files")
    doc.sections = sections

    # summary: description + README + architecture
    readme = next((texts[e.path] for e in chosen if e.path.count("/") == 0
                   and e.path.lower().startswith("readme") and e.path in texts), "")
    arch: List[str] = ["## Architecture", ""]
    if langs:
        arch.append("**Languages:** " + ", ".join(f"{n} ({c} files)" for n, c in langs.most_common(8)))
    if folders:
        arch.append("**Folders:** " + ", ".join(f"`{f}/` ({c})" for f, c in folders.most_common(15))
                    + (f", and {root_files} files at the top" if root_files else ""))
    if entry_points:
        arch.append("**Entry points:** " + ", ".join(f"`{p}`" for p in list(dict.fromkeys(entry_points))[:12]))
    if deps:
        names = [_DEP_NAME.split(d, maxsplit=1)[0] for d, _ in deps[:25]]
        arch.append(f"**Dependencies ({len(deps)}):** " + ", ".join(f"`{n}`" for n in names)
                    + (" …" if len(deps) > 25 else ""))
    arch.append(f"**Files:** {len(entries)} ({_size(doc.size)}); {len(sections)} read into notes")
    parts = [description.strip()] if description.strip() else []
    parts.append("\n".join(arch))  # before the README, so it is never cut
    if readme:
        parts.append("## From the README\n\n" + _readme_intro(readme))
    doc.summary = "\n\n".join(p for p in parts if p)

    # facts
    for name, count in langs.most_common(6):
        doc.facts.append(("language", f"{name}: {count} files"))
    for p in list(dict.fromkeys(entry_points))[:10]:
        doc.facts.append(("entry_point", p))
    for dep, where in deps[:40]:
        doc.facts.append(("dependency", f"{dep} (source: {where})"))
    for f, c in folders.most_common(10):
        doc.facts.append(("folder", f"{f}/ — {c} files"))
    doc.diagram = repo_diagram(title, folders, sub, root_files, folder_edges, entry_points, paths)
    return doc


_DEP_NAME = re.compile(r"[ <>=!~;\[]")


def _size(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB" if n >= 1_000_000 else f"{max(1, n // 1000)} KB"


def _readme_intro(text: str, limit: int = 1500) -> str:
    text = re.sub(r"<[^>]+>", "", text)                       # badges / html
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)           # images
    text = re.sub(r"\[\s*\]\([^)]*\)", "", text)                # links left without text
    text = re.sub(r"^﻿?---\n.*?\n---\n", "", text, flags=re.S)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    # demote headings, so the summary's headings stay below the note title
    text = re.sub(r"^(#{1,2})\s", lambda m: "#" * (len(m.group(1)) + 2) + " ", text, flags=re.M)
    if len(text) > limit:
        cut = text.rfind("\n\n", 0, limit)
        text = text[: cut if cut > limit // 2 else limit].rstrip() + "\n\n…"
    return text


def _read(read: Callable[[str], Optional[str]], e: TreeEntry, budget: Budget,
          doc: Document) -> Optional[str]:
    if not budget.take_bytes(max(e.size, 1)):
        doc.skipped.append(f"{e.path} (size limit of this run)")
        return None
    try:
        return read(e.path)
    except Exception as ex:  # one unreadable file must not stop the others
        doc.errors.append(f"{e.path}: {ex}")
        return None


def repo_diagram(title: str, folders: Counter, sub: Dict[str, Counter], root_files: int,
                 edges: Set[Tuple[str, str]], entry_points: List[str], paths: Set[str]) -> str:
    """``graph TD``: repository → top folders (→ main sub-folders), imports dashed,
    entry points as rounded nodes."""
    if not folders and root_files < 2:
        return ""
    lines = ["graph TD", f'  repo["{mermaid_label(title, 60)}"]']
    ids: Dict[str, str] = {}
    count = 0
    for i, (f, c) in enumerate(folders.most_common(15)):
        ids[f] = f"d{i}"
        lines.append(f'  repo --> d{i}["{mermaid_label(f)}/ ({c})"]')
        count += 1
    if root_files:
        lines.append(f'  repo --> rootfiles["top-level files ({root_files})"]')
    big = [f for f, _ in folders.most_common(3)]
    for f in big:
        for j, (s, c) in enumerate(sub[f].most_common(5)):
            if count >= 38:
                break
            nid = f"{ids[f]}s{j}"
            ids[f"{f}/{s}"] = nid
            lines.append(f'  {ids[f]} --> {nid}["{mermaid_label(s)}/ ({c})"]')
            count += 1
    for k, p in enumerate([p for p in dict.fromkeys(entry_points) if p in paths][:6]):
        top = p.split("/", 1)[0] if "/" in p else ""
        parent = ids.get(top, "repo") if top else ("rootfiles" if root_files else "repo")
        lines.append(f'  {parent} --> e{k}(["▶ {mermaid_label(p.rsplit("/", 1)[-1])}"])')
    for a, b in sorted(edges)[:30]:
        if a in ids and b in ids:
            lines.append(f"  {ids[a]} -.->|imports| {ids[b]}")
    return "\n".join(lines)
