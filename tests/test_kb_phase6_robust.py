"""KB phase 6 (2026-10-25): robustness and live sync.

The shared write budget (background writers one at a time per base, in
priority order; foreground writes win), the file watcher (polling scan driven
directly: create / edit / move / delete, own writes, .trash and temporary files
ignored, debounce, no thread while off, a live thread end to end), the doctor
checks on crafted states (drift, waiting links, conflicts, embeddings,
distillation, watcher, import errors, trash) and their speed, and the health route.
"""
import json
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".myclaw" / "knowledge" / "default").mkdir(parents=True)
    from myclaw.knowledge import budget, distill, semantic, watch

    budget._reset_for_tests()
    monkeypatch.setattr(distill._SCHED, "wanted", False)
    monkeypatch.setattr(distill, "_OPTIONS", dict(distill._OPTIONS, distill=False, kb_enabled=True))
    monkeypatch.setattr(semantic, "_OPTIONS", dict(semantic._OPTIONS, semantic_search=False, kb_enabled=True))
    monkeypatch.setattr(watch, "_OPTIONS", dict(watch._OPTIONS, watch_files=False, kb_enabled=True))
    yield tmp_path
    watch.stop_background()
    budget._reset_for_tests()


def _kdir(home):
    return home / ".myclaw" / "knowledge" / "default"


def _db(home):
    return home / ".myclaw" / "knowledge_default.db"


def _entity(permalink):
    from myclaw.knowledge.db import KnowledgeDB

    with KnowledgeDB("default") as db:
        return db.get_entity_by_permalink(permalink)


def _note(title, body="", links=()):
    rel = "".join(f"- relates_to [[{t}]]\n" for t in links)
    return f"---\ntitle: {title}\n---\n\n# {title}\n\n{body}\n\n## Relations\n\n{rel}"


# ── 1. write budget ─────────────────────────────────────────────────────────

def _hold(key, who, prio, started, release, log=None):
    from myclaw.knowledge import budget

    def run():
        with budget.background(key, who, prio, on_timeout="raise"):
            if log is not None:
                log.append(who)
            started.set()
            release.wait(10)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    assert started.wait(5)
    return t


def test_budget_one_background_writer_per_base(home):
    from myclaw.knowledge import budget

    key = budget.key_for("default")
    started, release = threading.Event(), threading.Event()
    t = _hold(key, "sync", budget.PRIO_SYNC, started, release)
    assert budget.busy("default") and budget.status("default")["bases"]["knowledge_default.db"]["writer"] == "sync"
    assert not budget.acquire(key, "embeddings", budget.PRIO_SEMANTIC, timeout=0.2)    # serialised
    with pytest.raises(budget.Busy):
        with budget.background(key, "distillation", budget.PRIO_DISTILL, timeout=0.1, on_timeout="raise"):
            pass
    # another base is independent
    assert budget.acquire(budget.key_for("other"), "embeddings", budget.PRIO_SEMANTIC, timeout=0.5)
    budget.release(budget.key_for("other"))
    release.set()
    t.join(5)
    assert budget.acquire(key, "embeddings", budget.PRIO_SEMANTIC, timeout=1)
    budget.release(key)
    assert not budget.busy("default")


def test_budget_priority_order_and_reentrancy(home):
    from myclaw.knowledge import budget

    key = budget.key_for("default")
    started, release = threading.Event(), threading.Event()
    log = []
    holder = _hold(key, "holder", budget.PRIO_INGEST, started, release, log)
    threads = []
    for who, prio in (("distillation", budget.PRIO_DISTILL), ("embeddings", budget.PRIO_SEMANTIC),
                      ("watcher", budget.PRIO_SYNC)):
        ev = threading.Event()

        def run(who=who, prio=prio, ev=ev):
            ev.set()
            with budget.background(key, who, prio):
                log.append(who)
        th = threading.Thread(target=run, daemon=True)
        th.start()
        ev.wait(2)
        time.sleep(0.05)          # queued in this order
        threads.append(th)
    assert budget.status("default")["bases"]["knowledge_default.db"]["waiting"] == \
        ["watcher", "embeddings", "distillation"]
    release.set()
    for th in [holder, *threads]:
        th.join(5)
    assert log == ["holder", "watcher", "embeddings", "distillation"]
    # re-entrant in one thread; a foreground write inside a slot is not marked
    with budget.background(key, "outer"):
        with budget.background("default", "inner"):
            with budget.foreground(key):
                assert budget.status("default")["bases"]["knowledge_default.db"]["foreground"] == 0
        assert budget.holds(key)
    assert not budget.holds(key)


def test_budget_foreground_wins(home):
    """A background writer does not start while a foreground write is marked, and
    a running one hands the base over at its next checkpoint."""
    from myclaw.knowledge import budget

    key = budget.key_for("default")
    with budget.foreground(key):
        assert not budget.acquire(key, "sync", budget.PRIO_SYNC, timeout=0.3)
    assert budget.acquire(key, "sync", budget.PRIO_SYNC, timeout=0.3)
    budget.release(key)

    order = []
    in_fg, fg_go = threading.Event(), threading.Event()
    bg_has_slot = threading.Event()

    def background():
        with budget.background(key, "distillation", budget.PRIO_DISTILL):
            order.append("bg batch 1")
            bg_has_slot.set()
            in_fg.wait(5)
            assert budget.checkpoint()          # waits for the foreground write
            order.append("bg batch 2")

    def foreground():
        bg_has_slot.wait(5)
        with budget.foreground(key):
            in_fg.set()
            fg_go.wait(0.3)
            order.append("foreground write")

    a = threading.Thread(target=background, daemon=True)
    b = threading.Thread(target=foreground, daemon=True)
    a.start()
    b.start()
    a.join(5)
    b.join(5)
    assert order == ["bg batch 1", "foreground write", "bg batch 2"]
    assert budget.status("default")["bases"]["knowledge_default.db"]["yielded"] == 1


def test_foreground_write_note_is_never_held_by_the_budget(home):
    from myclaw.knowledge import budget, write_note

    write_note("First", content="x")
    started, release = threading.Event(), threading.Event()
    t = _hold(budget.key_for("default"), "distillation", budget.PRIO_DISTILL, started, release)
    t0 = time.monotonic()
    write_note("Second", content="written while a background writer holds the base")
    assert time.monotonic() - t0 < 2.0 and _entity("second") is not None
    release.set()
    t.join(5)


def test_distill_and_scheduler_use_the_budget(home, monkeypatch):
    from myclaw.knowledge import budget, distill, write_note

    monkeypatch.setitem(distill._OPTIONS, "distill", True)
    write_note("A", content="I prefer quiet fans.")
    started, release = threading.Event(), threading.Event()
    t = _hold(budget.key_for("default"), "import", budget.PRIO_INGEST, started, release)
    r = distill.distill(wait_lock=0.2)
    assert not r["ok"] and r["busy"] and "try again" in r["message"]
    s = distill._Scheduler(clock=lambda: 1e6, runner=lambda p: {"ok": True})      # default busy check
    s.bases[str(_db(home))] = distill._BaseState(changed=50, last_change=0.0)
    assert s.tick() == []                                    # another writer: postponed
    release.set()
    t.join(5)
    assert distill.distill(wait_lock=1)["ok"]
    s.bases[str(_db(home))].not_before = 0.0
    assert s.tick() == [str(_db(home))]
    assert not budget.busy("default")


def test_retry_locked_only_on_locked(home):
    from myclaw.knowledge import budget

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError("database is locked")
        return "done"

    assert budget.retry_locked(flaky, "test", delay=0.01) == "done" and len(calls) == 3
    calls.clear()

    def broken():
        calls.append(1)
        raise sqlite3.OperationalError("no such table: x")

    with pytest.raises(sqlite3.OperationalError):
        budget.retry_locked(broken, "test", delay=0.01)
    assert len(calls) == 1

    def always():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        budget.retry_locked(always, "test", attempts=3, delay=0.01)


def test_background_sync_and_embeddings_hold_the_slot(home, monkeypatch):
    from myclaw.knowledge import budget, write_note
    from myclaw.knowledge.sync import background_sync

    write_note("A", content="x")
    seen = []
    real = budget.acquire

    def spy(key, who, prio=budget.PRIO_SYNC, timeout=budget.DEFAULT_TIMEOUT):
        seen.append(who)
        return real(key, who, prio, timeout)

    monkeypatch.setattr(budget, "acquire", spy)
    (_kdir(home) / "hand.md").write_text(_note("Hand", "by hand"))
    assert background_sync("default")["added"] == 1
    assert seen == ["background sync"]


# ── 2. file watcher (polling scan, driven directly) ─────────────────────────

class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def watcher(home):
    from myclaw.knowledge import watch, write_note

    write_note("Hub", content="links here", relations=None)   # creates the index
    calls = []

    def reindex(base, paths):
        calls.append((base, paths))
        return watch._default_reindex(base, paths)

    clock = _Clock()
    w = watch.Watcher(clock=clock, reindex=reindex, use_watchfiles=False)
    w.scan()                 # snapshot
    w.calls = calls          # type: ignore[attr-defined]
    w.fake_clock = clock     # type: ignore[attr-defined]
    return w


def _bump(path: Path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 2_000_000_000))


def test_watcher_reindexes_create_edit_move_delete(home, watcher):
    from myclaw.knowledge.db import KnowledgeDB

    k = _kdir(home)
    (k / "car.md").write_text(_note("Car", "red automobile", links=["Hub", "Garage"]))
    assert watcher.scan() == 1
    st = watcher.flush(force=True)["default"]
    assert st["added"] == 1 and _entity("car") is not None
    # only that file was handed over (plus unknown paths found by the sync itself)
    assert watcher.calls[-1] == ("default", [os.path.normpath(str(k / "car.md"))])

    (k / "car.md").write_text(_note("Car", "blue automobile now", links=["Hub", "Garage"]))
    _bump(k / "car.md")
    assert watcher.scan() == 1 and watcher.flush(force=True)["default"]["updated"] == 1
    with KnowledgeDB("default") as db:
        assert [e.permalink for e in db.search_fts("blue")] == ["car"]
    eid = _entity("car").id

    (k / "cars").mkdir()
    os.replace(k / "car.md", k / "cars" / "car.md")       # a move keeps the id and links
    assert watcher.scan() == 2
    watcher.flush(force=True)
    e = _entity("car")
    assert e.id == eid and e.file_path.endswith(os.path.join("cars", "car.md"))

    (k / "garage.md").write_text(_note("Garage", "where the car sleeps"))
    watcher.scan()
    watcher.flush(force=True)
    with KnowledgeDB("default") as db:
        assert [r for r in db.get_unresolved_relations(eid)] == []
    os.remove(k / "garage.md")                              # delete → waiting link again
    assert watcher.scan() == 1
    assert watcher.flush(force=True)["default"]["deleted"] == 1
    assert _entity("garage") is None
    with KnowledgeDB("default") as db:
        assert ("relates_to", "Garage") in [tuple(r) for r in db.get_unresolved_relations(eid)]


def test_watcher_ignores_own_writes_trash_and_temp_files(home, watcher):
    from myclaw.knowledge import delete_note, write_note

    write_note("Written by ZenSynora", content="own write")
    write_note("Hub", content="updated by an agent")
    delete_note("hub")                              # → .trash (own delete)
    k = _kdir(home)
    (k / ".trash").mkdir(exist_ok=True)
    (k / ".trash" / "old-20260101.md").write_text(_note("Old"))
    for name in ("draft.md.tmp", ".#draft.md", "draft.md~", ".draft.md.swp", "notes.txt", "image.png"):
        (k / name).write_text("x")
    (k / ".obsidian").mkdir()
    (k / ".obsidian" / "workspace.md").write_text("x")
    (home / ".myclaw" / "knowledge" / "status.md").write_text("root-level file, not a base")
    assert watcher.scan() == 0
    assert watcher.stats["ignored_self"] >= 2
    assert watcher.flush(force=True) == {} and watcher.calls == []
    # a hand edit of a file ZenSynora wrote is not "own" any more
    f = k / "written-by-zensynora.md"
    f.write_text(f.read_text() + "\nadded by hand\n")
    _bump(f)
    assert watcher.scan() == 1 and watcher.flush(force=True)["default"]["updated"] == 1


def test_watcher_classify_and_db_files(home):
    from myclaw.knowledge.watch import classify

    root = home / ".myclaw" / "knowledge"
    assert classify(root / "default" / "a.md", root) == ("default", "note")
    assert classify(root / "default" / "sub" / "a.md", root) == ("default", "note")
    assert classify(root / "default" / ".bmignore", root) == ("default", "rescan")
    assert classify(root / "default" / "gone-folder", root) == ("default", "rescan")
    for p in (root / "a.md", root / "knowledge_default.db-wal", root / "default" / "x.log",
              root / "default" / ".trash" / "a.md", root / ".git" / "a.md", root / "default" / "a.md.tmp",
              root / "default" / "#a.md#", home / "elsewhere" / "a.md", root / "default" / "watch-status.json"):
        assert classify(p, root) is None, p
    # knowledge folder = the data folder (memopad's self-loop): logs, databases ignored
    data = home / ".myclaw"
    assert classify(data / "logs" / "zensynora.log", data) is None
    assert classify(data / "logs" / "knowledge_ingest.jsonl", data) is None
    assert classify(data / "knowledge_default.db", data) is None


def test_watcher_debounce_and_batching(home, watcher):
    from myclaw.knowledge import watch

    k = _kdir(home)
    clock = watcher.fake_clock
    (k / "a.md").write_text(_note("A"))
    watcher.scan()
    assert not watcher.due()                                   # just changed
    clock.t += watch.DEBOUNCE_SECONDS / 2
    (k / "b.md").write_text(_note("B"))
    watcher.scan()
    clock.t += watch.DEBOUNCE_SECONDS / 2
    assert not watcher.due()                                   # quiet time restarted
    clock.t += watch.DEBOUNCE_SECONDS
    assert watcher.due()
    watcher.flush()
    assert len(watcher.calls) == 1 and len(watcher.calls[0][1]) == 2      # one batch
    # a steady stream of changes is still flushed after MAX_DELAY_SECONDS
    for i in range(10):
        (k / f"s{i}.md").write_text(_note(f"S{i}"))
        watcher.scan()
        if watcher.due():
            break
        clock.t += watch.DEBOUNCE_SECONDS * 0.8
    assert watcher.due() and clock.t - watcher.first_event >= watch.MAX_DELAY_SECONDS


def test_watcher_skips_bases_without_index_and_never_raises(home, watcher):
    from myclaw.knowledge import watch

    other = home / ".myclaw" / "knowledge" / "new-base"
    other.mkdir()
    (other / "a.md").write_text(_note("A"))
    assert watcher.scan() == 1
    assert watcher.flush(force=True) == {}                    # no index database yet

    def boom(base, paths):
        raise RuntimeError("disk on fire")

    w = watch.Watcher(clock=_Clock(), reindex=boom, use_watchfiles=False)
    w.push([_kdir(home) / "x.md"])
    assert w.flush(force=True) == {} and w.stats["errors"] == 1 and "disk on fire" in w.stats["last_error"]


def test_no_watcher_thread_while_off(home, monkeypatch):
    from myclaw.knowledge import watch

    monkeypatch.setattr(watch._W, "use_watchfiles", False)
    names = lambda: [t.name for t in threading.enumerate()  # noqa: E731
                     if t.name.startswith("kb-file-watcher") and t.is_alive()]
    watch.start_background()
    assert not watch._W.running() and names() == []
    watch.set_options(watch_files=True)
    assert watch._W.running() and watch.status()["mode"] == "polling"
    watch.set_options(kb_enabled=False)                      # knowledge base off → stopped
    assert not watch._W.running() and names() == []
    watch.set_options(kb_enabled=True)
    assert watch._W.running()
    watch.set_options(watch_files=False)
    assert not watch._W.running() and names() == []
    # a process that never asked (CLI) never starts one
    watch.stop_background()
    watch.set_options(watch_files=True)
    assert not watch._W.running()


def test_watcher_follows_the_knowledge_folder(home, monkeypatch):
    from myclaw.knowledge import storage, watch

    monkeypatch.setattr(watch._W, "use_watchfiles", False)
    watch.set_options(watch_files=True)
    watch.start_background()
    assert watch.status()["watching"] == str(home / ".myclaw" / "knowledge")
    custom = home / "notes"
    custom.mkdir()
    storage.set_knowledge_root(custom)
    try:
        watch.set_options(watch_files=True)                  # a settings save re-applies
        assert watch.status()["watching"] == str(custom) and watch._W.running()
    finally:
        storage.set_knowledge_root(None)
        watch.stop_background()


def _wait_for(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_live_polling_thread_end_to_end(home, monkeypatch):
    from myclaw.knowledge import watch, write_note

    write_note("Hub", content="x")
    monkeypatch.setattr(watch, "POLL_SECONDS", 0.2)
    monkeypatch.setattr(watch._W, "use_watchfiles", False)
    watch.set_options(watch_files=True)
    watch.start_background()
    assert _wait_for(lambda: watch._W.snapshot is not None)
    (_kdir(home) / "live.md").write_text(_note("Live", "edited in an editor"))
    assert _wait_for(lambda: _entity("live") is not None)
    assert watch.status()["last_reindex"] and watch.status()["running"]


def test_live_watchfiles_end_to_end(home, monkeypatch):
    pytest.importorskip("watchfiles")
    from myclaw.knowledge import watch, write_note

    write_note("Hub", content="x")
    monkeypatch.setattr(watch._W, "use_watchfiles", True)
    watch.set_options(watch_files=True)
    watch.start_background()
    assert watch.status()["mode"] == "watchfiles"
    time.sleep(0.5)                                         # the watch is set up
    t0 = time.time()
    (_kdir(home) / "evented.md").write_text(_note("Evented", "saved by Obsidian"))
    assert _wait_for(lambda: _entity("evented") is not None, 10)
    assert time.time() - t0 < 5
    os.remove(_kdir(home) / "evented.md")
    assert _wait_for(lambda: _entity("evented") is None, 10)


def test_paths_limited_sync_is_the_normal_sync(home):
    """sync(paths=…) gives the same index as a full sync for the changed files."""
    from myclaw.knowledge import write_note
    from myclaw.knowledge.sync import sync_knowledge

    write_note("A", content="alpha")
    k = _kdir(home)
    (k / "b.md").write_text(_note("B", "beta", links=["A"]))
    (k / "a.md").write_text(_note("A", "alpha changed by hand"))
    _bump(k / "a.md")
    st = sync_knowledge("default", paths=[k / "b.md"])     # a.md not listed: not re-read
    assert st["added"] == 1 and st["updated"] == 0
    st = sync_knowledge("default", paths=[str(k / "a.md")])
    assert st["updated"] == 1


# ── 3. doctor checks ────────────────────────────────────────────────────────

def _check(id_):
    from myclaw.doctor import run_checks

    return {r.id: r for r in run_checks(offline=True, only=[id_]).results}[id_]


def test_doctor_all_ok_on_a_clean_base(home):
    from myclaw.doctor import run_checks
    from myclaw.knowledge import write_note

    write_note("A", content="x", relations=None)
    rep = run_checks(offline=True, categories=["knowledge"])
    assert {r.id for r in rep.results} == {"kb_links", "kb_conflicts", "kb_embeddings", "kb_distill",
                                          "kb_watcher", "kb_ingest", "kb_trash"}
    assert all(r.status == "ok" for r in rep.results), [(r.id, r.detail) for r in rep.results]
    assert _check("knowledge_index").status == "ok"


def test_doctor_index_drift_and_fix(home):
    from myclaw.doctor import apply_fixes, run_checks
    from myclaw.knowledge import write_note

    for t in ("A", "B", "C"):
        write_note(t, content=f"note {t}")
    k = _kdir(home)
    (k / "a.md").write_text(_note("A", "changed by hand"))
    _bump(k / "a.md")
    os.remove(k / "b.md")
    (k / "loose.md").write_text(_note("Loose"))
    r = _check("knowledge_index")
    assert r.status == "warn"
    for part in ("1 not indexed", "1 index entry without a file", "1 changed since indexing"):
        assert part in r.detail, r.detail
    assert "knowledge sync" in r.hint and "--force" in r.hint
    rep = run_checks(offline=True, only=["knowledge_index"])
    assert rep.results[0].fixable
    assert "knowledge synced" in apply_fixes(rep)[0]
    assert _check("knowledge_index").status == "ok"


def test_doctor_waiting_links_conflicts(home, monkeypatch):
    from myclaw.knowledge import health, write_note
    from myclaw.knowledge.parser import Relation

    monkeypatch.setattr(health, "PENDING_WARN", 3)
    write_note("Hub", content="x", relations=[Relation("relates_to", t) for t in ("Ghost", "Phantom", "Spirit")])
    write_note("Hub2", content="x", relations=[Relation("relates_to", "Ghost")])
    r = _check("kb_links")
    assert r.status == "warn" and "4 links" in r.detail and "Ghost (2)" in r.detail and "Create" in r.hint
    assert _check("kb_conflicts").status == "ok"
    con = sqlite3.connect(_db(home))
    con.execute("INSERT INTO conflicts (permalink, category, obs_a, obs_b, kind, status, created_at, pair_hash) "
                "VALUES ('hub', 'spec', '16 GB RAM', '32 GB RAM', 'value', 'open', '2026-10-25', 'h1')")
    con.commit()
    con.close()
    r = _check("kb_conflicts")
    assert r.status == "warn" and "1 open conflict" in r.detail and "keep one" in r.hint


def test_doctor_embeddings(home, monkeypatch):
    from myclaw.knowledge import semantic, write_note

    write_note("A", content="x")
    write_note("B", content="y")
    assert "off" in _check("kb_embeddings").detail
    monkeypatch.setitem(semantic._OPTIONS, "semantic_search", True)
    monkeypatch.setattr(semantic, "library_installed", lambda: False)
    r = _check("kb_embeddings")
    assert r.status == "warn" and "not installed" in r.detail and "Install" in r.hint
    monkeypatch.setattr(semantic, "library_installed", lambda: True)
    monkeypatch.setattr(semantic, "embedder_state", lambda: ("idle", None))
    r = _check("kb_embeddings")
    assert r.status == "warn" and "2 of 2 note(s) waiting" in r.detail and "Backfill" in r.hint
    monkeypatch.setattr(semantic, "embedder_state", lambda: ("error", "could not download the model"))
    r = _check("kb_embeddings")
    assert r.status == "warn" and "could not download" in r.detail


def test_doctor_distillation(home, monkeypatch):
    from myclaw.knowledge import distill, write_note

    write_note("A", content="I prefer quiet fans.")
    assert "off" in _check("kb_distill").detail
    monkeypatch.setitem(distill._OPTIONS, "distill", True)
    r = _check("kb_distill")
    assert r.status == "warn" and "not distilled for more than a day" in r.detail
    assert distill.distill(wait_lock=1)["ok"]
    r = _check("kb_distill")
    assert r.status == "ok" and "0 note(s) pending" in r.detail
    con = sqlite3.connect(_db(home))
    con.execute("INSERT OR REPLACE INTO distill_state (key, value) VALUES ('lease', ?)",
                (f"{distill._HOST}|{os.getpid()}|{time.time()}",))
    con.commit()
    con.close()
    assert "lease held" in _check("kb_distill").detail
    monkeypatch.setitem(distill._LAST, str(_db(home)), {"last_error": "OperationalError: disk I/O error"})
    r = _check("kb_distill")
    assert r.status == "warn" and "disk I/O error" in r.detail


def test_doctor_watcher(home, monkeypatch):
    from myclaw.knowledge import watch

    assert "off" in _check("kb_watcher").detail
    watch.set_options(watch_files=True)
    r = _check("kb_watcher")
    assert r.status == "ok" and "runs in the Web UI" in r.detail      # the CLI doctor
    monkeypatch.setattr(watch._W, "wanted", True)                     # the Web UI, thread died
    r = _check("kb_watcher")
    assert r.status == "warn" and "not running" in r.detail
    monkeypatch.setattr(watch._W, "use_watchfiles", False)
    watch.start_background()
    assert _check("kb_watcher").status == "ok"


def test_doctor_ingest_errors_and_trash(home, monkeypatch):
    from myclaw.knowledge import health

    log = home / ".myclaw" / "logs" / "knowledge_ingest.jsonl"
    log.parent.mkdir(parents=True)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    old = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - 3 * 86400))
    lines = [{"time": old, "base": "default", "source": "https://old.example", "errors": ["old failure"]},
             {"time": now, "base": "default", "source": "https://ok.example", "errors": []},
             "not json"]
    log.write_text("\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines) + "\n")
    r = _check("kb_ingest")
    assert r.status == "ok" and "1 import(s)" in r.detail
    with open(log, "a") as f:
        f.write(json.dumps({"time": now, "base": "default", "source": "https://bad.example",
                            "errors": ["HTTP 404"]}) + "\n")
    r = _check("kb_ingest")
    assert r.status == "warn" and "bad.example" in r.detail and "HTTP 404" in r.detail
    assert "knowledge_ingest.jsonl" in r.hint

    trash = _kdir(home) / ".trash"
    trash.mkdir()
    for i in range(3):
        (trash / f"n{i}.md").write_text("x" * 100)
    r = _check("kb_trash")
    assert r.status == "ok" and "3 earlier version" in r.detail
    monkeypatch.setattr(health, "TRASH_WARN_FILES", 3)
    r = _check("kb_trash")
    assert r.status == "warn" and ".trash holds 3 file(s)" in r.detail and "never empties" in r.hint


def test_doctor_knowledge_off_and_other_bases(home):
    from myclaw.knowledge import write_note

    write_note("A", content="x")
    write_note("B", content="y", user_id="work")
    (home / ".myclaw" / "knowledge" / "work" / "loose.md").write_text(_note("Loose"))
    r = _check("knowledge_index")
    assert r.status == "warn" and "work: " in r.detail and "1 not indexed" in r.detail


def test_doctor_checks_never_raise(home, monkeypatch):
    from myclaw.knowledge import health

    def boom(*a, **k):
        raise RuntimeError("broken")

    monkeypatch.setattr(health, "links", boom)
    h = health.health("default")
    assert h["links"]["error"].startswith("RuntimeError")
    # an unreadable index database
    _db(home).write_bytes(b"not a database")
    for id_ in ("knowledge_index", "kb_links", "kb_conflicts", "kb_embeddings", "kb_distill", "kb_trash"):
        assert _check(id_).status in ("ok", "warn"), id_


def test_doctor_knowledge_checks_are_fast(home):
    """< 1 s per check on 2000 notes (5000 in the plan; the cost is linear)."""
    from myclaw.doctor import run_checks
    from myclaw.knowledge.sync import sync_knowledge

    k = _kdir(home)
    for i in range(2000):
        (k / f"n{i:04d}.md").write_text(_note(f"N{i}", f"note number {i}", links=[f"N{(i + 1) % 2000}"]))
    sync_knowledge("default", force=True)
    for i in range(0, 2000, 7):
        _bump(k / f"n{i:04d}.md")                     # time changed, content not: checksums sampled
    t0 = time.monotonic()
    rep = run_checks(offline=True, categories=["knowledge", "data"])
    total = time.monotonic() - t0
    for r in rep.results:
        assert r.duration_ms < 1000, (r.id, r.duration_ms)
    idx = next(r for r in rep.results if r.id == "knowledge_index")
    assert idx.status == "ok", idx.detail
    assert total < 5


# ── 4. health route ─────────────────────────────────────────────────────────

def test_health_route(home):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from myclaw.knowledge import write_note
    from myclaw.web import knowledge_health_api

    write_note("A", content="x")
    app = FastAPI()
    app.include_router(knowledge_health_api.router)
    c = TestClient(app)
    h = c.get("/api/knowledge/health").json()
    assert h["ok"] and h["warnings"] == [] and h["index"]["files"] == 1 and h["index"]["indexed"] == 1
    for part in ("links", "conflicts", "embeddings", "distill", "watcher", "ingest", "trash", "writers"):
        assert part in h
    (_kdir(home) / "loose.md").write_text(_note("Loose"))
    h = c.get("/api/knowledge/health?base=default").json()
    assert not h["ok"] and h["warnings"][0]["id"] == "index" and "sync" in h["warnings"][0]["fix"]
    assert c.get("/api/knowledge/health?base=../etc").status_code == 400
    # the app wires the route
    from myclaw.web.api import app as real_app

    assert "/api/knowledge/health" in real_app.openapi()["paths"]


def test_settings_switch_and_config(home):
    from myclaw.config import KnowledgeConfig

    assert KnowledgeConfig().watch_files is False
    src = (Path(__file__).resolve().parent.parent / "webui" / "src" / "components" / "SettingsView.tsx").read_text()
    assert "knowledge.watch_files" in src
