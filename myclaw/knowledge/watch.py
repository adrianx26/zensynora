"""Live sync: re-index note files changed by hand (KB phase 6, 2026-10-25).

When a note is created, edited, moved or deleted outside ZenSynora (an
editor, Obsidian, ``git pull``), the Web UI re-indexes just that file within
about a second — through the normal incremental sync (moves keep the note's
id and links; a deleted note's incoming links become waiting links again).

* Opt-in: ``knowledge.watch_files`` (default off) and ``knowledge.enabled``.
  Only the Web UI process watches (``start_background``); no thread while off.
* Uses the optional package ``watchfiles`` (``pip install 'zensynora[watch]'``)
  for change events; without it, a cheap polling scan of modification times
  every ``POLL_SECONDS`` seconds (bounded to ``MAX_FILES`` files).
* Ignored (memopad's self-loop: its watcher re-indexed its own log and status
  files forever): anything that is not a ``.md`` note in a base folder,
  dot-folders and dot-files (``.trash``, ``.git``, ``.obsidian``, ``.ingest``),
  temporary and swap files (``*.tmp``, ``*~``, ``.#*``, ``*.swp``), the index
  databases, logs and status files — and ZenSynora's own writes: every note
  file ZenSynora writes is remembered with its checksum for a short time
  (``remember_self_write``) and an event for it is dropped.
* Changes are debounced (``DEBOUNCE_SECONDS`` after the last event, at most
  ``MAX_DELAY_SECONDS`` after the first) and batched per base; the re-index is a
  background writer of the shared write budget (``budget.py``).
* Only bases that already have an index database are re-indexed (a folder
  created by hand is picked up by its first ``zensynora knowledge sync``).
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

POLL_SECONDS = 5.0            # polling fallback: one scan of modification times
DEBOUNCE_SECONDS = 0.5        # quiet time after the last event before re-indexing
MAX_DELAY_SECONDS = 2.0       # …but never longer than this after the first event
MAX_FILES = 50_000            # polling: files tracked at most (bounded memory)
MAX_PENDING = 5_000           # more changed paths than this → one rescan of the base
SELF_WRITE_TTL = 30.0         # seconds an own write is remembered
SELF_WRITE_MAX = 4_000
INSTALL_SPEC = "watchfiles>=0.21,<2.0"
INSTALL_HINT = f"pip install '{INSTALL_SPEC}' (or pip install 'zensynora[watch]')"

_TEMP_SUFFIXES = (".tmp", "~", ".swp", ".swo", ".swx", ".swn", ".bak", ".part", ".crdownload", ".orig")

_OPTIONS: Dict[str, Any] = {"watch_files": False, "kb_enabled": True}


# ── Own writes ──────────────────────────────────────────────────────────────

_SELF: "OrderedDict[str, Tuple[Optional[str], float]]" = OrderedDict()
_SELF_LOCK = threading.Lock()


def _norm(path: Any) -> str:
    return os.path.normpath(str(path))


def _md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def remember_self_write(path: Any, data: Optional[Any] = None) -> None:
    """ZenSynora wrote (``data``: the bytes/text written) or removed (``None``)
    this note file: an event for it is not a change made by hand. Never raises."""
    try:
        if data is None:
            checksum: Optional[str] = None
        else:
            raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
            checksum = _md5(raw)
        now = time.monotonic()
        with _SELF_LOCK:
            key = _norm(path)
            _SELF.pop(key, None)
            _SELF[key] = (checksum, now)
            while _SELF and (len(_SELF) > SELF_WRITE_MAX or next(iter(_SELF.values()))[1] < now - SELF_WRITE_TTL):
                _SELF.popitem(last=False)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug("self-write bookkeeping: %s", e)


def _file_md5(path: str) -> Optional[str]:
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def is_self_write(path: Any) -> bool:
    """Is the file now exactly as ZenSynora wrote (or removed) it moments ago?"""
    key = _norm(path)
    with _SELF_LOCK:
        hit = _SELF.get(key)
    if hit is None or hit[1] < time.monotonic() - SELF_WRITE_TTL:
        return False
    checksum = hit[0]
    if checksum is None:
        return not os.path.exists(key)
    return _file_md5(key) == checksum


# ── What is watched ─────────────────────────────────────────────────────────

def _rel_parts(path: Any, root: Path) -> Optional[Tuple[str, ...]]:
    try:
        rel = os.path.relpath(_norm(path), _norm(root))
    except ValueError:      # another drive (Windows)
        return None
    if rel == "." or rel.startswith(".." + os.sep) or rel == "..":
        return None
    return tuple(Path(rel).parts)


def _ignored_name(name: str) -> bool:
    low = name.lower()
    return (name.startswith(".") or name.startswith("#") or name.startswith("~")
            or low.endswith(_TEMP_SUFFIXES))


def classify(path: Any, root: Path) -> Optional[Tuple[str, str]]:
    """``(base, kind)`` for a path worth reacting to, else None.

    kind ``"note"``: a ``.md`` note in a base folder; ``"rescan"``: something
    that can hide notes (a folder moved or deleted, ``.bmignore`` changed) —
    the base is synced as a whole (still incremental)."""
    parts = _rel_parts(path, root)
    if not parts or len(parts) < 2:
        return None                         # the root itself, or a file in it (DB, logs …)
    base = parts[0]
    if base.startswith("."):
        return None
    if parts[-1] == ".bmignore" and len(parts) == 2:
        return base, "rescan"
    if any(_ignored_name(p) for p in parts[1:]):
        return None
    name = parts[-1]
    if name.endswith(".md"):
        return base, "note"
    if "." not in name and not os.path.isfile(_norm(path)):
        return base, "rescan"               # a folder (moved / deleted / created)
    return None


def watchfiles_installed() -> bool:
    try:
        return importlib.util.find_spec("watchfiles") is not None
    except (ImportError, ValueError):
        return False


def _db_exists(base: str) -> bool:
    from .budget import key_for

    return os.path.isfile(key_for(base))


# ── The watcher ─────────────────────────────────────────────────────────────

def _default_reindex(base: str, paths: Optional[List[str]]) -> Dict[str, int]:
    from .sync import background_sync

    return background_sync(base, "file watcher", paths=paths)


class Watcher:
    """Collects changed paths, debounces them, re-indexes per base.

    ``scan()`` (polling) and ``push()`` (events) feed it; ``flush()`` re-indexes
    what is due. Tests call these directly (``clock`` / ``reindex`` replaceable)."""

    def __init__(self, root: Optional[Callable[[], Path]] = None,
                 clock: Callable[[], float] = time.monotonic,
                 reindex: Optional[Callable[[str, Optional[List[str]]], Dict[str, int]]] = None,
                 use_watchfiles: Optional[bool] = None,
                 only_indexed_bases: bool = True) -> None:
        if root is None:
            from .storage import knowledge_root

            root = knowledge_root
        self.root_fn = root
        self.clock = clock
        self.reindex = reindex or _default_reindex
        self.use_watchfiles = use_watchfiles
        self.only_indexed_bases = only_indexed_bases
        self.cv = threading.Condition()
        self.pending: Dict[str, Optional[Set[str]]] = {}   # base → paths (None = rescan)
        self.first_event = 0.0
        self.last_event = 0.0
        self.snapshot: Optional[Dict[str, Tuple[int, int]]] = None
        self.thread: Optional[threading.Thread] = None
        self.stop_event = threading.Event()
        self.wanted = False
        self.mode: Optional[str] = None
        self.watching: Optional[str] = None
        self.stats: Dict[str, Any] = {"events": 0, "ignored_self": 0, "ignored": 0, "reindexed": 0,
                                      "runs": 0, "errors": 0, "last_reindex": None, "last_base": None,
                                      "last_stats": None, "last_error": None, "truncated": False,
                                      "started": None}

    # -- feeding ---------------------------------------------------------
    def push(self, paths: Iterable[Any]) -> int:
        """Record changed paths (already filtered or not). Returns how many count."""
        root = Path(self.root_fn())
        n = 0
        now = self.clock()
        with self.cv:
            for p in paths:
                c = classify(p, root)
                if c is None:
                    self.stats["ignored"] += 1
                    continue
                base, kind = c
                if kind == "note" and is_self_write(p):
                    self.stats["ignored_self"] += 1
                    continue
                if not self.pending and n == 0:
                    self.first_event = now
                cur = self.pending.get(base, set())
                if kind == "rescan" or cur is None:
                    self.pending[base] = None
                else:
                    cur.add(_norm(p))
                    self.pending[base] = None if len(cur) > MAX_PENDING else cur
                n += 1
            if n:
                self.stats["events"] += n
                self.last_event = now
                self.cv.notify_all()
        return n

    def _listing(self, root: Path) -> Dict[str, Tuple[int, int]]:
        """mtime/size of every note file under the base folders (bounded)."""
        out: Dict[str, Tuple[int, int]] = {}
        truncated = False
        try:
            bases = sorted(e.name for e in os.scandir(root) if e.is_dir(follow_symlinks=False)
                           and not e.name.startswith("."))
        except OSError:
            return out
        for base in bases:
            for dirpath, dirs, names in os.walk(os.path.join(str(root), base), followlinks=False):
                dirs[:] = [d for d in dirs if not _ignored_name(d)]
                for name in names:
                    if not name.endswith(".md") or _ignored_name(name):
                        continue
                    full = os.path.join(dirpath, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        continue
                    out[_norm(full)] = (st.st_mtime_ns, st.st_size)
                    if len(out) >= MAX_FILES:
                        truncated = True
                        break
                if truncated:
                    break
            if truncated:
                break
        if truncated and not self.stats["truncated"]:
            logger.info("Knowledge watcher: more than %d note files; only the first are polled "
                        "(install watchfiles for events: %s)", MAX_FILES, INSTALL_HINT)
        self.stats["truncated"] = truncated
        return out

    def scan(self) -> int:
        """Polling: compare modification times with the previous scan and push
        the differences (the first scan only takes the snapshot)."""
        now = self._listing(Path(self.root_fn()))
        prev, self.snapshot = self.snapshot, now
        if prev is None:
            return 0
        changed = [p for p, v in now.items() if prev.get(p) != v]
        changed += [p for p in prev if p not in now]
        return self.push(changed) if changed else 0

    # -- re-indexing -----------------------------------------------------
    def due(self, now: Optional[float] = None) -> bool:
        now = self.clock() if now is None else now
        with self.cv:
            if not self.pending:
                return False
            return (now - self.last_event >= DEBOUNCE_SECONDS
                    or now - self.first_event >= MAX_DELAY_SECONDS)

    def flush(self, force: bool = False) -> Dict[str, Dict[str, int]]:
        """Re-index every base with pending changes (when due, or ``force``)."""
        if not force and not self.due():
            return {}
        with self.cv:
            batch, self.pending = self.pending, {}
        done: Dict[str, Dict[str, int]] = {}
        for base, paths in sorted(batch.items()):
            if self.only_indexed_bases and not _db_exists(base):
                logger.debug("Knowledge watcher: %s has no index yet; skipped", base)
                continue
            if paths is not None:
                # own writes that were recorded after the event arrived
                paths = {p for p in paths if not is_self_write(p)}
                if not paths:
                    continue
            try:
                st = self.reindex(base, sorted(paths) if paths is not None else None)
                done[base] = st
                self.stats["runs"] += 1
                self.stats["reindexed"] += int(st.get("added", 0)) + int(st.get("updated", 0)) + \
                    max(0, int(st.get("deleted", 0)))
                self.stats.update(last_reindex=time.time(), last_base=base, last_stats=st, last_error=None)
                if any(st.get(k) for k in ("added", "updated", "deleted")):
                    logger.info("Knowledge: re-indexed notes changed on disk in %s: %s", base, st)
            except Exception as e:  # never let the thread die
                self.stats["errors"] += 1
                self.stats["last_error"] = f"{type(e).__name__}: {e}"
                logger.warning("Knowledge watcher: re-indexing %s failed: %s", base, e)
        return done

    # -- thread ----------------------------------------------------------
    def _mode(self) -> str:
        use = self.use_watchfiles
        if use is None:
            use = watchfiles_installed()
        return "watchfiles" if use else "polling"

    def running(self) -> bool:
        t = self.thread
        return t is not None and t.is_alive()

    def start(self) -> bool:
        """Start the thread (if not running). True when started now."""
        with self.cv:
            if self.running():
                return False
            self.stop_event = threading.Event()
            self.mode = self._mode()
            self.watching = str(self.root_fn())
            self.snapshot = None
            self.stats["started"] = time.time()
            self.thread = threading.Thread(target=self._run, args=(self.stop_event,),
                                           name="kb-file-watcher", daemon=True)
            self.thread.start()
        logger.info("Knowledge: watching %s for notes changed by hand (%s)", self.watching, self.mode)
        return True

    def stop(self, timeout: float = 5.0) -> None:
        with self.cv:
            ev, t = self.stop_event, self.thread
            ev.set()
            self.cv.notify_all()
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout)
        with self.cv:
            if self.thread is t:
                self.thread = None
            self.pending = {}

    def _run(self, stop: threading.Event) -> None:
        """The watcher thread re-indexes; a second thread collects the changes
        (events or polling). 2026-10-25 (review): watchfiles yields nothing while
        every raw change is filtered out (a busy log or database in the watched
        folder), so the re-index must not wait for its next yield."""
        events = threading.Thread(target=self._collect, args=(stop,), name="kb-file-watcher-events",
                                  daemon=True)
        events.start()
        try:
            self._flush_loop(stop)
        except Exception as e:  # pragma: no cover - defensive
            self.stats["last_error"] = f"{type(e).__name__}: {e}"
            logger.warning("Knowledge watcher stopped: %s", e)
        finally:
            stop.set()
            events.join(2.0)

    def _flush_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self.cv:
                if not self.pending:
                    self.cv.wait(1.0)          # push() notifies
                    continue
                now = self.clock()
                wait = min(self.last_event + DEBOUNCE_SECONDS, self.first_event + MAX_DELAY_SECONDS) - now
            if wait > 0:
                stop.wait(min(wait, 1.0))
                continue
            self.flush()

    def _collect(self, stop: threading.Event) -> None:
        try:
            if self.mode == "watchfiles":
                try:
                    self._run_watchfiles(stop)
                    return
                except Exception as e:  # e.g. inotify limit reached: poll instead
                    if stop.is_set():
                        return
                    logger.warning("Knowledge watcher: change events unavailable (%s); "
                                   "polling every %.0f s instead", e, POLL_SECONDS)
                    self.mode = "polling"
            self._run_polling(stop)
        except Exception as e:  # pragma: no cover - defensive
            self.stats["last_error"] = f"{type(e).__name__}: {e}"
            logger.warning("Knowledge watcher stopped: %s", e)
            stop.set()

    def _run_polling(self, stop: threading.Event) -> None:
        self.scan()                               # snapshot
        while not stop.wait(POLL_SECONDS):
            self.scan()

    def _run_watchfiles(self, stop: threading.Event) -> None:
        import watchfiles  # optional dependency

        root = Path(self.root_fn())
        root.mkdir(parents=True, exist_ok=True)

        def keep(_change: Any, path: str) -> bool:     # cheap filter of the raw changes
            return classify(path, root) is not None

        for changes in watchfiles.watch(str(root), watch_filter=keep, stop_event=stop,
                                        debounce=400, step=50, rust_timeout=1000,
                                        yield_on_timeout=True, raise_interrupt=False):
            if changes:
                self.push(p for _, p in changes)
            if stop.is_set():
                break

    def status(self) -> Dict[str, Any]:
        with self.cv:
            pending = sum(len(v) if v is not None else 1 for v in self.pending.values())
            return {"enabled": enabled(), "wanted": self.wanted, "running": self.running(), "mode": self.mode,
                    "watching": self.watching, "pending": pending,
                    "watchfiles_installed": watchfiles_installed(), "install_hint": INSTALL_HINT,
                    "poll_seconds": POLL_SECONDS, **dict(self.stats)}


_W = Watcher()


# ── Options / lifecycle ─────────────────────────────────────────────────────

def enabled() -> bool:
    return bool(_OPTIONS["watch_files"]) and bool(_OPTIONS["kb_enabled"])


def set_options(watch_files: Optional[bool] = None, kb_enabled: Optional[bool] = None) -> None:
    """Apply ``knowledge.watch_files`` / ``knowledge.enabled``. In the process that
    asked for it (the Web UI), turning it on starts the watcher and turning it
    off stops it (no thread while off); a changed knowledge folder restarts it."""
    if watch_files is not None:
        _OPTIONS["watch_files"] = bool(watch_files)
    if kb_enabled is not None:
        _OPTIONS["kb_enabled"] = bool(kb_enabled)
    _apply()


def _apply() -> None:
    try:
        if not _W.wanted:
            return
        if not enabled():
            if _W.running():
                _W.stop()
                logger.info("Knowledge: file watcher stopped")
            return
        if _W.running() and _W.watching != str(_W.root_fn()):
            _W.stop()                    # knowledge.knowledge_dir changed
        _W.start()
    except Exception as e:  # never break a settings save
        logger.warning("Knowledge: file watcher not (re)started: %s", e)


def start_background() -> None:
    """The Web UI: watch while ``knowledge.watch_files`` is on."""
    _W.wanted = True
    _apply()


def stop_background() -> None:
    _W.wanted = False
    _W.stop()


def status() -> Dict[str, Any]:
    return _W.status()


def _stop() -> None:
    try:
        _W.stop(2.0)
    except Exception:
        pass


import atexit  # noqa: E402

atexit.register(_stop)
