"""Updater: rollback pipeline, backups, migrations, adoption, pruning.

These tests never touch the network or pip; the full end-to-end flow
(real venv build, web UI build) is exercised manually / in CI scripts.
"""
import json
import os
import shutil
import subprocess
import tarfile
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


@pytest.mark.parametrize("action", ["continue", "skip", "abort"])
def test_interactive_optional_step_records_real_outcome_and_rollback(tmp_path, action):
    from updater.progress import capture

    calls, events = [], []
    control = tmp_path / "decisions"
    control.mkdir()
    optional = _Rec("optional", calls)
    optional.skippable = True

    def record(event):
        events.append(event)
        if event["status"] == "waiting":
            (control / f"{event['request']}.json").write_text(json.dumps({"action": action}))

    with capture(record):
        with pytest.raises(UpdateError):
            run_pipeline([_Rec("mandatory", calls), optional, _Rec("failure", calls, fail=True)],
                         _ctx(_layout(tmp_path), interactive=True, control_dir=str(control)))
    assert events[0]["step"] == "mandatory" and events[0]["status"] == "running"
    assert sum(event["status"] == "waiting" for event in events) == 1
    assert not list(control.iterdir())  # the decision is consumed, not reused
    if action == "continue":
        assert calls == ["run:mandatory", "run:optional", "run:failure",
                         "undo:failure", "undo:optional", "undo:mandatory"]
    elif action == "skip":
        assert calls == ["run:mandatory", "run:failure", "undo:failure", "undo:mandatory"]
        assert any(e["step"] == "optional" and e["status"] == "skipped" for e in events)
    else:
        assert calls == ["run:mandatory", "undo:mandatory"]
        assert any(e["step"] == "optional" and e["status"] == "failed" for e in events)


def test_interactive_without_terminal_aborts_before_optional_work(tmp_path, monkeypatch):
    import updater.pipeline as pipeline

    monkeypatch.setattr(pipeline.sys.stdin, "isatty", lambda: False)
    calls = []
    step = _Rec("optional", calls)
    step.skippable = True
    with pytest.raises(UpdateError, match="requires a terminal"):
        run_pipeline([_Rec("mandatory", calls), step], _ctx(_layout(tmp_path), interactive=True))
    assert calls == ["run:mandatory", "undo:mandatory"]


def test_handoff_is_successful_transfer_without_rollback(tmp_path):
    from updater.pipeline import Handoff
    from updater.progress import capture

    calls, events = [], []

    class Transfer(_Rec):
        def run(self, ctx):
            raise Handoff(tmp_path / "new")

    with capture(events.append), pytest.raises(Handoff):
        run_pipeline([_Rec("fetch", calls), Transfer("handoff", calls)], _ctx(_layout(tmp_path)))
    assert calls == ["run:fetch"]
    assert [(e["step"], e["status"]) for e in events] == [
        ("fetch", "running"), ("fetch", "ok"), ("handoff", "running"), ("handoff", "ok")]


def test_failed_rollback_is_recorded_without_hiding_original_failure(tmp_path):
    from updater.progress import capture

    class BrokenRollback(_Rec):
        def rollback(self, ctx):
            raise OSError("cannot restore old folder")

    events = []
    ctx = _ctx(_layout(tmp_path))
    with capture(events.append), pytest.raises(UpdateError, match="boom"):
        run_pipeline([BrokenRollback("backup-app", []), _Rec("build", [], fail=True)], ctx)
    assert ctx.extra["rollback_failed"]
    assert events[-1]["status"] == "rollback-failed"
    assert "cannot restore old folder" in events[-1]["text"]


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


def test_no_backup_requires_force_before_any_work(tmp_path):
    calls = []
    with pytest.raises(UpdateError, match="--no-backup requires --force"):
        run_pipeline([_Rec("work", calls)], _ctx(_layout(tmp_path), no_backup=True))
    assert calls == []


@pytest.mark.parametrize("no_backup", [False, True])
def test_no_backup_excludes_archives_but_keeps_release_user_files(tmp_path, no_backup):
    from updater import app_backup
    from updater.progress import capture

    lay = _layout(tmp_path)
    lay.data_dir.mkdir(parents=True)
    (lay.data_dir / "config.json").write_text('{"saved": true}')
    old = _release(lay.releases / "old", "0.8.5")
    (old / ".env").write_text("LOCAL_SETTING=keep")
    new = _release(lay.releases / "new", "0.8.5")
    ctx = _ctx(lay, force=True, no_backup=no_backup)
    ctx.current_dir, ctx.new_dir = old, new
    ctx.current_version = ctx.new_version = "0.8.5"
    events = []
    with capture(events.append):
        run_pipeline([steps.BackupUserData(), steps.BackupAppFolder(),
                      steps.CarryOverUserFiles(), steps.RestoreUserData()], ctx)
    assert (new / ".env").read_text() == "LOCAL_SETTING=keep"
    assert (lay.data_dir / "config.json").read_text() == '{"saved": true}'
    if no_backup:
        assert ctx.backup_dir is None and not list(lay.backups.iterdir())
        assert {e["step"] for e in events if e["status"] == "skipped"} == {
            "backup", "backup-app", "restore-data"}
    else:
        assert (ctx.backup_dir / "user-data.tar.gz").is_file()
        assert (ctx.backup_dir / app_backup.ARCHIVE).is_file()


@pytest.mark.parametrize("no_backup", [False, True])
def test_no_backup_failed_migration_reports_unrecoverable_data(tmp_path, no_backup):
    from updater.progress import capture

    lay = _layout(tmp_path)
    lay.data_dir.mkdir(parents=True)
    config = lay.data_dir / "config.json"
    config.write_text('{"before": true}')
    ctx = _ctx(lay, force=True, no_backup=no_backup)
    ctx.current_dir = _release(lay.releases / "old", "0.8.5")
    ctx.current_version = ctx.new_version = "0.8.5"

    class PartialMigration(steps.MigrateUserData):
        def run(self, ctx):
            ctx.extra["migrate_attempted"] = True
            config.write_text('{"partially_migrated": true}')
            raise UpdateError("migration failed")

    events = []
    with capture(events.append), pytest.raises(UpdateError, match="migration failed"):
        run_pipeline([steps.BackupUserData(), PartialMigration()], ctx)
    if no_backup:
        assert config.read_text() == '{"partially_migrated": true}'
        assert ctx.extra["rollback_failed"]
        assert any(e["step"] == "migrate" and e["status"] == "rollback-failed"
                   and "disabled recovery" in e["text"] for e in events)
    else:
        assert config.read_text() == '{"before": true}'
        assert not ctx.extra.get("rollback_failed")


@pytest.mark.parametrize("applied, completed, fails", [([], True, False), (["change"], True, True), ([], False, True)])
def test_no_backup_migration_rollback_distinguishes_noop_from_changes(tmp_path, applied, completed, fails):
    ctx = _ctx(_layout(tmp_path), force=True, no_backup=True)
    ctx.extra.update(migrate_attempted=True, migrate_completed=completed)
    ctx.migrations_applied = applied
    if fails:
        with pytest.raises(UpdateError, match="--no-backup disabled recovery"):
            steps.MigrateUserData().rollback(ctx)
    else:
        steps.MigrateUserData().rollback(ctx)


def test_no_backup_cli_option_is_explicit_and_survives_handoff(monkeypatch, capsys):
    import sys
    import types

    if os.name == "nt":
        monkeypatch.setitem(sys.modules, "fcntl", types.ModuleType("fcntl"))
    from updater import __main__ as cli

    args = cli.build_parser().parse_args(["update", "--force", "--no-backup"])
    assert "--no-backup" in cli._passthrough(args) and "--force" in cli._passthrough(args)
    assert not cli.build_parser().parse_args(["update", "--force"]).no_backup
    monkeypatch.setattr(cli, "_layout", lambda args: pytest.fail("Invalid flags must not touch the installation"))
    assert cli.main(["--no-backup"]) == 1
    assert "--no-backup requires --force" in capsys.readouterr().err


@pytest.mark.parametrize("supported, update_code", [(True, 0), (False, 0), (True, 7)])
def test_no_backup_script_downloads_main_and_refuses_unsupported_updater(tmp_path, supported, update_code):
    bash = shutil.which("bash")
    if os.name == "nt":
        git_bash = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Git" / "bin" / "bash.exe"
        bash = str(git_bash) if git_bash.exists() else None
    if not bash:
        pytest.skip("Bash is required for the Linux bootstrap fixture")
    base = tmp_path / "install base"
    (base / "source").mkdir(parents=True)
    source = tmp_path / "downloaded"
    (source / "updater").mkdir(parents=True)
    (source / "updater" / "steps.py").write_text("# fixture")
    args_file = tmp_path / "arguments"
    (source / "update.sh").write_text(
        '#!/usr/bin/env bash\n'
        'if [[ "$*" == "update --help" ]]; then\n'
        f'  echo "options: {"--no-backup" if supported else "--force"}"; exit 0\n'
        'fi\n'
        'printf "%s\\n" "$@" > "$TEST_ARGUMENTS"\n'
        f'exit {update_code}\n')
    archive = tmp_path / "source.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="zensynora-main")
    script = Path(__file__).resolve().parents[1] / "force-update-no-backup.sh"
    launcher = (
        'uname() { printf "Linux\\n"; }; export -f uname\n'
        'curl() {\n'
        '  [[ "$*" == *"https://codeload.github.com/adrianx26/zensynora/tar.gz/main"* ]] || return 9\n'
        '  while [[ $# -gt 0 ]]; do\n'
        '    if [[ "$1" == --output ]]; then cp "$TEST_ARCHIVE" "$2"; return; fi\n'
        '    shift\n'
        '  done\n'
        '  return 8\n'
        '}; export -f curl\n'
        'bash "$1" "$2"\n')
    env = dict(os.environ, TEST_ARCHIVE=str(archive), TEST_ARGUMENTS=str(args_file),
               ZENSYNORA_DATA_DIR=str(tmp_path / "user data"))
    result = subprocess.run([bash, "-c", launcher, "fixture", str(script), str(base)],
                            env=env, capture_output=True, text=True, timeout=30)
    if supported:
        assert result.returncode == update_code, result.stderr
        args = args_file.read_text().splitlines()
        assert "--force" in args and "--no-backup" in args
        assert args[args.index("--base") + 1] == str(base)
        assert args[args.index("--data-dir") + 1] == str(tmp_path / "user data")
        assert not Path(args[args.index("--from-dir") + 1]).exists()
    else:
        assert result.returncode != 0 and "does not yet support --no-backup" in result.stderr
        assert not args_file.exists()
