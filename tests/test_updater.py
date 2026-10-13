"""Updater: rollback pipeline, backups, migrations, adoption, pruning.

These tests never touch the network or pip; the full end-to-end flow
(real venv build, web UI build) is exercised manually / in CI scripts.
"""
import json
import os
from pathlib import Path

import pytest

from updater.backup import create_backup, restore_backup
from updater.layout import Layout, State, read_version, version_tuple
from updater.pipeline import Context, NothingToDo, Options, Step, UpdateError, run_pipeline
from updater import steps


def _layout(tmp_path) -> Layout:
    lay = Layout(base=tmp_path / "base", data_dir=tmp_path / "home" / ".myclaw")
    lay.ensure_dirs()
    return lay


def _release(path: Path, version: str) -> Path:
    (path / "myclaw").mkdir(parents=True)
    (path / "pyproject.toml").write_text(f'[project]\nname = "zensynora"\nversion = "{version}"\n')
    (path / "myclaw" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return path


def _ctx(lay, **opts) -> Context:
    return Context(layout=lay, state=State.load(lay.state_file), opts=Options(**opts), stamp="20260101T000000")


# ── pipeline ─────────────────────────────────────────────────────────────────
class _Rec(Step):
    def __init__(self, name, log, fail=False):
        self.name, self.log, self.fail = name, log, fail

    def run(self, ctx):
        self.log.append(f"run:{self.name}")
        if self.fail:
            raise UpdateError("boom")

    def rollback(self, ctx):
        self.log.append(f"undo:{self.name}")


def test_pipeline_rolls_back_completed_steps_in_reverse(tmp_path):
    log = []
    with pytest.raises(UpdateError):
        run_pipeline([_Rec("a", log), _Rec("b", log), _Rec("c", log, fail=True)], _ctx(_layout(tmp_path)))
    assert log == ["run:a", "run:b", "run:c", "undo:c", "undo:b", "undo:a"]


# ── versions ─────────────────────────────────────────────────────────────────
def test_version_helpers(tmp_path):
    assert read_version(_release(tmp_path / "r", "1.2.3")) == "1.2.3"
    assert version_tuple("0.10.0") > version_tuple("0.9.9")
    assert version_tuple("0.5.0") == version_tuple("0.5.0")


# ── backups ──────────────────────────────────────────────────────────────────
def test_backup_and_restore_roundtrip(tmp_path):
    lay = _layout(tmp_path)
    lay.data_dir.mkdir(parents=True)
    (lay.data_dir / "config.json").write_text('{"a": 1}')
    (lay.data_dir / "semantic_cache").mkdir()
    (lay.data_dir / "semantic_cache" / "big.bin").write_text("cache")
    bdir = create_backup(lay, "t")
    manifest = json.loads((bdir / "manifest.json").read_text())
    assert "user-data.tar.gz" in manifest["files"]
    assert oct(bdir.stat().st_mode & 0o777) == "0o700"

    (lay.data_dir / "config.json").write_text("{CORRUPT")
    restore_backup(lay, bdir, take_safety_copy=False)
    assert (lay.data_dir / "config.json").read_text() == '{"a": 1}'
    # excluded cache folder is kept from the live data, not lost
    assert (lay.data_dir / "semantic_cache" / "big.bin").exists()


def test_restore_refuses_corrupted_backup(tmp_path):
    lay = _layout(tmp_path)
    lay.data_dir.mkdir(parents=True)
    (lay.data_dir / "x").write_text("1")
    bdir = create_backup(lay, "t")
    with open(bdir / "user-data.tar.gz", "ab") as f:
        f.write(b"garbage")
    with pytest.raises(RuntimeError, match="corrupted"):
        restore_backup(lay, bdir)
    assert (lay.data_dir / "x").read_text() == "1"


# ── migrations ───────────────────────────────────────────────────────────────
def test_migrations_run_once_and_are_recorded(tmp_path):
    from updater.migrations import pending, read_ledger, run_pending

    data = tmp_path / ".myclaw"
    data.mkdir()
    (data / "config.json").write_text("{}")
    os.chmod(data / "config.json", 0o644)
    first = run_pending(data, "0.6.0")
    assert "0001_data_layout" in first
    assert (data / "knowledge").is_dir()
    assert oct((data / "config.json").stat().st_mode & 0o777) == "0o600"
    assert run_pending(data, "0.6.0") == []
    assert pending(data) == []
    assert read_ledger(data)["applied"][0]["app_version"] == "0.6.0"


# ── fetch / version policy ───────────────────────────────────────────────────
def test_fetch_from_dir_and_version_policy(tmp_path):
    lay = _layout(tmp_path)
    _release(lay.source, "0.5.0")
    src_same = _release(tmp_path / "same", "0.5.0")
    src_old = _release(tmp_path / "old", "0.4.0")
    src_new = _release(tmp_path / "new", "0.6.0")

    for src, exc in ((src_same, NothingToDo), (src_old, UpdateError)):
        ctx = _ctx(lay, source_kind="dir", source_value=str(src))
        ctx.current_dir, ctx.current_version = lay.source, "0.5.0"
        with pytest.raises(exc):
            run_pipeline([steps.FetchRelease()], ctx)
        assert not any(p.name.startswith("0.") for p in lay.releases.iterdir())  # cleaned up

    ctx = _ctx(lay, source_kind="dir", source_value=str(src_new))
    ctx.current_dir, ctx.current_version = lay.source, "0.5.0"
    run_pipeline([steps.FetchRelease()], ctx)
    assert ctx.new_version == "0.6.0" and (ctx.new_dir / "pyproject.toml").exists()


# ── activation / adoption ────────────────────────────────────────────────────
def test_adopts_legacy_install_and_rollback_restores_it(tmp_path):
    lay = _layout(tmp_path)
    _release(lay.source, "0.5.0")
    (lay.source / ".env").write_text("X=1")
    new = _release(lay.releases / "0.6.0-x", "0.6.0")

    ctx = _ctx(lay)
    ctx.current_dir, ctx.current_version, ctx.new_dir = lay.source, "0.5.0", new
    act = steps.ActivateRelease()
    act.run(ctx)
    assert lay.source.is_symlink() and lay.source.resolve() == new.resolve()
    assert (lay.releases / ctx.adopted_legacy_as / ".env").exists()

    act.rollback(ctx)
    assert not lay.source.is_symlink() and (lay.source / ".env").read_text() == "X=1"


def test_switch_between_releases_is_atomic_symlink(tmp_path):
    lay = _layout(tmp_path)
    a = _release(lay.releases / "a", "1.0.0")
    b = _release(lay.releases / "b", "1.1.0")
    steps.point_source_to(lay, a)
    steps.point_source_to(lay, b)
    assert os.readlink(lay.source) == "releases/b"


# ── pruning ──────────────────────────────────────────────────────────────────
def test_prune_keeps_current_and_rollback_target(tmp_path):
    lay = _layout(tmp_path)
    rels = []
    for i in range(5):
        r = _release(lay.releases / f"0.{i}.0-x", f"0.{i}.0")
        os.utime(r, (1000 + i, 1000 + i))
        rels.append(r)
    steps.point_source_to(lay, rels[4])
    st = State.load(lay.state_file)
    st.current = rels[4].name
    st.record({"action": "update", "id": rels[4].name, "from_id": rels[0].name})
    removed = steps.prune(lay, st, keep_releases=2, keep_backups=5)
    left = {p.name for p in lay.releases.iterdir()}
    assert rels[4].name in left and rels[3].name in left   # newest two
    assert rels[0].name in left                            # rollback target protected
    assert "releases/" + rels[1].name in removed
