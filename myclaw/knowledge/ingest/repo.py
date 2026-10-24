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
# 2026-10-24 (KB phase 5, review): one file = one note, so "max_notes" is
# also the number of files read
MAX_PART_CHARS = 10_500        # text kept from one file
MAX_READ_FILES = 80
PREFETCH_FILES = 600           # local folders: code files read to rank them by imports
PREFETCH_BYTES = 4_000_000

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


_LOW_DOC = re.compile(
    r"(^|/)(archive[sd]?|old|deprecated|legacy|plans?|drafts?|changelog|history|todo|roadmap|"
    r"incidents?|graphify-out|diagrams?|examples?|samples?|demos?|fixtures?|benchmarks?|"
    r"migrations?|generated|snapshots?)(/|\.|$)|changelog|todo|release[-_]?notes", re.I)
_TEST = re.compile(r"(^|/)(tests?|spec|specs|__tests__|testing|e2e)/|(^|/)test_[^/]*$|"
                   r"_test\.\w+$|\.(test|spec)\.\w+$|(^|/)conftest\.py$", re.I)
DOCUMENT_EXT = {".pdf", ".docx", ".xlsx", ".xlsm", ".csv", ".tsv"}
_CENTRAL_NAMES = {"core", "main", "app", "server", "api", "models", "engine", "agent", "cli",
                  "client", "service", "services", "router", "routes", "config", "storage",
                  "db", "database", "handler", "handlers", "manager", "index", "lib"}


def category(e: TreeEntry) -> str:
    """readme, manifest, agentdoc, doc, lowdoc, document, code, lowcode, test, config, other."""
    path = e.path
    name = path.rsplit("/", 1)[-1].lower()
    depth = path.count("/")
    ext = posixpath.splitext(name)[1]
    lang = language_of(path)
    if depth == 0 and name.startswith("readme"):
        return "readme"
    if depth == 0 and (name in MANIFESTS or (name.startswith("requirements") and name.endswith(".txt")
                                            and "lock" not in name)):
        return "manifest"
    low = bool(_LOW_DOC.search(path))
    if ext in DOCUMENT_EXT:
        return "lowdoc" if low or _TEST.search(path) else "document"
    if "lock" in name and ext in (".txt", ".json", ".yaml", ".yml", ".toml"):
        return "config"
    if name in AGENT_DOCS and depth <= 2 and not low:
        return "agentdoc"
    if ext in MD_EXT | TEXT_EXT or name.startswith("readme"):
        return "lowdoc" if low else "doc"
    if lang and lang not in NON_CODE_LANGS:
        if _TEST.search(path):
            return "test"
        return "lowcode" if low else "code"
    if lang:
        return "config"
    return "other"


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


def entry_file(ep: str, paths: Set[str], py_roots: List[str]) -> Optional[str]:
    """The file of an entry point ("cli → pkg.cli:main", "main → index.js", a path)."""
    target = ep.split("→", 1)[-1].strip()
    if target in paths:
        return target
    if target.startswith(("node ", "python ", "npx ", "deno ")):
        target = target.split(None, 1)[-1].strip()
    cand = posixpath.normpath(target.lstrip("./")) if ("/" in target or "." in target.rsplit("/", 1)[-1]
                                                       and not re.match(r"^[\w.]+:\w+$", target)) else ""
    if cand in paths:
        return cand
    module = target.split(":", 1)[0].strip()
    if re.match(r"^[A-Za-z_][\w.]*$", module):
        return _py_module_files(module, paths, py_roots)
    return None


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
                 link_base: str = "", extra_facts: Iterable[Tuple[str, str]] = (),
                 prefetch: bool = False, done: Optional[Set[str]] = None) -> Document:
    """Build the architecture :class:`Document` of a repository or folder.

    ``read(path)`` returns a file's text (Markdown for documents) or None.
    ``link_base`` (e.g. ``https://github.com/o/r/blob/main/``) gives each file
    note a link to the original. ``prefetch`` (local folders) reads the code
    files first to rank them by imports. ``done``: files already written by an
    unfinished earlier run of the same source (the next batch is taken).
    """
    done = set(done or ())
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
    readable = [e for e in entries if _readable(e)]
    cats = {e.path: category(e) for e in readable}
    for e in readable:
        if cats[e.path] != "manifest":
            continue
        text = _read(read, e, budget, doc)
        if text is None:
            continue
        texts[e.path] = text
        d, ep, desc = parse_manifest(e.path.lower(), text)
        for dep in d:
            dep_name = _DEP_NAME.split(dep, maxsplit=1)[0].lower()
            if dep_name and dep_name not in dep_names:
                dep_names.add(dep_name)
                deps.append((dep, e.path))
        entry_points += ep
        description = description or desc
    for e in entries:
        name = e.path.rsplit("/", 1)[-1].lower()
        if name in ENTRY_NAMES and e.path.count("/") <= 2 and cats.get(e.path) == "code":
            entry_points.append(e.path)
    ep_paths = {p for p in (entry_file(ep, paths, py_roots) for ep in entry_points) if p}

    # 2026-10-24 (KB phase 5, review): rank code by how many files import it
    # (local folders: up to PREFETCH_FILES code files are read for that),
    # entry points first; docs are at most a quarter of the notes; archives,
    # plans, changelogs and tests come last.
    indeg: Counter = Counter()
    folder_edges: Set[Tuple[str, str]] = set()
    code_paths = [e.path for e in readable if cats[e.path] == "code"]
    main_lang = Counter(language_of(p) for p in code_paths).most_common(1)
    root = code_root([p for p in code_paths if main_lang and language_of(p) == main_lang[0][0]])

    def unit(path: str) -> str:
        """The diagram node of a file: a package below the code root, or a top folder."""
        if root and path.startswith(root):
            rest = path[len(root):]
            return root + rest.split("/", 1)[0] if "/" in rest else root.rstrip("/")
        return path.split("/", 1)[0] if "/" in path else ""

    def add_edges(path: str, text: str, targets: Iterable[str]) -> None:
        src = unit(path)
        for target in targets:
            dst = unit(target)
            if src and dst and src != dst:
                folder_edges.add((src, dst))
        if src and not root:
            for dst in module_imports(path, text, top_dirs):
                if dst != src:
                    folder_edges.add((src, dst))

    if prefetch:
        pre_bytes = 0
        code_files = sorted((e for e in readable if cats[e.path] in ("code", "lowcode")),
                            key=lambda e: (e.path.count("/"), e.path))
        for e in code_files[:PREFETCH_FILES]:
            if pre_bytes + e.size > PREFETCH_BYTES or budget.out_of_time(margin=15.0):
                break
            text = _read(read, e, budget, doc, quiet=True)
            if text is None:
                continue
            pre_bytes += e.size
            texts[e.path] = text
            targets = find_imports(e.path, text, paths, py_roots)
            for target in targets:
                indeg[target] += 1
            add_edges(e.path, text, targets)

    def code_score(e: TreeEntry) -> Tuple[float, str]:
        name = posixpath.splitext(e.path.rsplit("/", 1)[-1].lower())[0]
        score = 4.0 * min(indeg[e.path], 10)
        if e.path in ep_paths:
            score += 20
        if name in _CENTRAL_NAMES:
            score += 3
        if name == "__init__" and e.size < 200:
            score -= 6
        score -= 1.5 * max(0, e.path.count("/") - 1)
        score += min(3.0, e.size / 6000)
        if cats[e.path] == "lowcode":
            score -= 15
        return (-score, e.path)

    by_cat: Dict[str, List[TreeEntry]] = defaultdict(list)
    for e in readable:
        if e.path not in done:
            by_cat[cats[e.path]].append(e)
    for c in ("code", "lowcode", "test"):
        by_cat[c].sort(key=code_score)
    for c in ("doc", "agentdoc", "lowdoc", "document", "config"):
        by_cat[c].sort(key=lambda e: (e.path.count("/"), e.path))
    slots = max(1, max_files)
    docs_cap = max(1, slots // 4)
    chosen: List[TreeEntry] = []
    reasons: Counter = Counter()

    def take(items: List[TreeEntry], limit: int) -> None:
        for e in list(items):
            if limit <= 0 or len(chosen) >= slots:
                break
            chosen.append(e)
            items.remove(e)
            limit -= 1

    take(by_cat["readme"], 1)
    take(by_cat["manifest"], min(3, max(1, slots // 8)))
    has_code = bool(by_cat["code"])
    take(by_cat["agentdoc"], max(0, docs_cap - 1))
    take(by_cat["doc"], max(0, docs_cap - sum(1 for e in chosen if cats[e.path] in
                                                ("readme", "agentdoc", "doc"))))
    if has_code:
        take(by_cat["document"], slots // 10)  # spreadsheets / PDFs in a code repository
        take(by_cat["code"], slots)
    else:  # a folder of documents
        take(by_cat["document"], slots)
    for c in ("doc", "lowcode", "config", "lowdoc", "test", "document"):
        take(by_cat[c], slots)
    for c, why in (("code", "code files ranked lower"), ("doc", "documentation (at most a quarter "
                   "of the notes are docs)"), ("document", "documents"),
                   ("lowdoc", "archived / planning / changelog docs"), ("test", "test files"),
                   ("lowcode", "example / generated code"), ("agentdoc", "agent instructions"),
                   ("config", "configuration files")):
        if by_cat[c]:
            reasons[why] = len(by_cat[c])
    for why, n in reasons.most_common():
        doc.skipped.append(f"{n} {why} not read (limit of {slots} files per run)")
    doc.metadata["files_left"] = sum(reasons.values())

    sections: List[Section] = []
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
        add_edges(e.path, text, [t for r, t in links if r == "imports"])
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
    if root:
        units = Counter(unit(p) for p in code_paths if p.startswith(root))
        others = Counter({f: c for f, c in folders.items() if not root.startswith(f + "/")})
        doc.diagram = unit_diagram(title, root, units, folder_edges,
                                   sorted(ep_paths), unit, others)
    else:
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
          doc: Document, quiet: bool = False) -> Optional[str]:
    if not budget.take_bytes(max(e.size, 1)):
        if not quiet:
            doc.skipped.append(f"{e.path} (size limit of this run)")
        return None
    try:
        return read(e.path)
    except Exception as ex:  # one unreadable file must not stop the others
        if not quiet:
            doc.errors.append(f"{e.path}: {ex}")
        return None


def code_root(code_paths: List[str]) -> str:
    """The folder that holds (almost) all the code, e.g. ``src/memopad/`` ('' if none).

    2026-10-24 (KB phase 5, review): with a src/ layout or one main package,
    imports between top-level folders show nothing — the diagram then shows
    the packages inside that folder.
    """
    prefix = ""
    total = len(code_paths)
    if total < 4:
        return ""
    while True:
        under = [p[len(prefix):] for p in code_paths if p.startswith(prefix)]
        dirs = Counter(x.split("/", 1)[0] for x in under if "/" in x)
        if not dirs:
            return prefix
        d, n = dirs.most_common(1)[0]
        if n < 0.8 * total:
            return prefix
        prefix += d + "/"


def unit_diagram(title: str, root: str, units: Counter, edges: Set[Tuple[str, str]],
                 entry_points: List[str], unit: Callable[[str], str],
                 others: Optional[Counter] = None) -> str:
    """``graph TD``: the code root's packages and the other top folders, imports
    dashed, entry points as rounded nodes."""
    lines = ["graph TD", f'  repo["{mermaid_label(title, 60)}"]',
             f'  repo --> r0["{mermaid_label(root, 48)}"]']
    ids: Dict[str, str] = {root.rstrip("/"): "r0"}
    for i, (f, c) in enumerate((others or Counter()).most_common(10)):
        ids[f] = f"o{i}"
        lines.append(f'  repo --> o{i}["{mermaid_label(f)}/ ({c})"]')
    for i, (u, c) in enumerate(units.most_common(25)):
        if u == root.rstrip("/"):
            lines[2] = f'  repo --> r0["{mermaid_label(root, 40)} ({c} files)"]'
            continue
        ids[u] = f"u{i}"
        label = u[len(root):] if u.startswith(root) else u
        lines.append(f'  r0 --> u{i}["{mermaid_label(label)}/ ({c})"]')
    for k, p in enumerate(entry_points[:6]):
        parent = ids.get(unit(p), "repo")
        lines.append(f'  {parent} --> e{k}(["▶ {mermaid_label(p.rsplit("/", 1)[-1])}"])')
    for a, b in sorted(edges)[:40]:
        if a in ids and b in ids:
            lines.append(f"  {ids[a]} -.->|imports| {ids[b]}")
    return "\n".join(lines)


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
