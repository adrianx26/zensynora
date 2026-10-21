"""Updater / installer requirements (2026-10-21): Node.js 22 for the Web UI and
Python venv support. nodejs.org is simulated (no network)."""
import hashlib
import io
import os
import stat
import tarfile
from pathlib import Path

import pytest

from updater import nodejs


def _script(path: Path, body: str) -> None:
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _fake_node_dir(root: Path, version: str = "v22.11.0") -> Path:
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True)
    _script(bin_dir / "node", f"echo {version}")
    _script(bin_dir / "npm", "echo 10.9.0")
    return bin_dir


def _tarball(version: str, tag: str) -> bytes:
    name = f"node-{version}-{tag}"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for f, body in (("bin/node", f"#!/bin/sh\necho {version}\n"), ("bin/npm", "#!/bin/sh\necho 10.9.0\n")):
            data = body.encode()
            info = tarfile.TarInfo(f"{name}/{f}")
            info.size, info.mode = len(data), 0o755
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _site(version="v22.11.0", tag=None, corrupt=False):
    tag = tag or nodejs.platform_tag()
    blob = _tarball(version, tag)
    name = f"node-{version}-{tag}.tar.gz"
    digest = hashlib.sha256(blob if not corrupt else b"other").hexdigest()
    sums = f"{'0' * 64}  node-{version}-win-x64.zip\n{digest}  {name}\n{'1' * 64}  node-{version}-{tag}.pkg\n"
    calls = []

    def fetch(url):
        calls.append(url)
        if url.endswith("SHASUMS256.txt"):
            return sums.encode()
        assert url.endswith(name), url
        return blob
    return fetch, calls


@pytest.fixture
def no_path_node(monkeypatch, tmp_path):
    """PATH without node/npm (like mindy01 after removing Hermes)."""
    empty = tmp_path / "emptybin"
    empty.mkdir()
    monkeypatch.setenv("PATH", f"{empty}:/usr/bin:/bin")
    monkeypatch.setattr(nodejs.shutil, "which", lambda name: None)
    monkeypatch.setattr(nodejs, "_has_lzma", lambda: False)  # the fake site only has .tar.gz


@pytest.mark.skipif(nodejs.platform_tag() is None, reason="no Node.js builds for this platform")
def test_downloads_verifies_and_links_node(tmp_path, no_path_node):
    fetch, calls = _site()
    bin_dir, note = nodejs.ensure_node(tmp_path / "base", fetch=fetch)
    assert note == "" and bin_dir == tmp_path / "base" / "node" / "bin"
    assert (tmp_path / "base" / "node").is_symlink()
    assert nodejs._usable(bin_dir) == (22, 11, 0)
    assert calls[0].endswith("/latest-v22.x/SHASUMS256.txt")
    # second call: already there, nothing downloaded
    calls.clear()
    assert nodejs.ensure_node(tmp_path / "base", fetch=fetch)[0] == bin_dir and calls == []


@pytest.mark.skipif(nodejs.platform_tag() is None, reason="no Node.js builds for this platform")
def test_newer_release_replaces_the_old_copy(tmp_path, no_path_node):
    base = tmp_path / "base"
    nodejs.download(base, _site("v22.11.0")[0])
    nodejs.download(base, _site("v22.12.0")[0])
    assert nodejs._usable(base / "node" / "bin") == (22, 12, 0)
    assert [p.name for p in base.glob("node-v22.*")] == [f"node-v22.12.0-{nodejs.platform_tag()}"]


@pytest.mark.skipif(nodejs.platform_tag() is None, reason="no Node.js builds for this platform")
def test_damaged_download_is_refused(tmp_path, no_path_node):
    fetch, _ = _site(corrupt=True)
    bin_dir, note = nodejs.ensure_node(tmp_path / "base", fetch=fetch)
    assert bin_dir is None and "damaged" in note and "Node.js/npm not found" in note
    assert not (tmp_path / "base" / "node").exists()


def test_offline_gives_a_clear_note(tmp_path, no_path_node):
    def down(url):
        raise OSError("Tunnel connection failed: 403")
    bin_dir, note = nodejs.ensure_node(tmp_path / "base", fetch=down)
    assert bin_dir is None and "could not be downloaded" in note
    assert nodejs.ensure_node(tmp_path / "base", allow_download=False) == (None, "Node.js/npm not found")


def test_system_node_used_when_new_enough_and_too_old_is_reported(tmp_path, monkeypatch):
    good = _fake_node_dir(tmp_path / "good", "v22.3.0")
    monkeypatch.setattr(nodejs.shutil, "which", lambda name: str(good / name))
    assert nodejs.find_node(tmp_path / "base") == good.resolve()
    old = _fake_node_dir(tmp_path / "old", "v18.19.1")
    monkeypatch.setattr(nodejs.shutil, "which", lambda name: str(old / name))
    assert nodejs.find_node(tmp_path / "base") is None
    _, note = nodejs.ensure_node(tmp_path / "base", allow_download=False)
    assert "18.19.1 is too old" in note and "20.19.0+" in note


def test_own_copy_wins_over_path(tmp_path, monkeypatch):
    base = tmp_path / "base"
    own = _fake_node_dir(base / "node-v22.11.0-x")
    (base / "node").symlink_to("node-v22.11.0-x")
    other = _fake_node_dir(tmp_path / "sys", "v24.0.0")
    monkeypatch.setattr(nodejs.shutil, "which", lambda name: str(other / name))
    assert nodejs.find_node(base) == base / "node" / "bin"
    assert own.exists()


def test_latest_release_prefers_xz_when_available(monkeypatch):
    sums = ("a" * 64 + "  node-v22.11.0-linux-x64.tar.gz\n" + "b" * 64 + "  node-v22.11.0-linux-x64.tar.xz\n")
    monkeypatch.setattr(nodejs, "_has_lzma", lambda: True)
    assert nodejs.latest_release("linux-x64", lambda u: sums.encode())[1].endswith(".tar.xz")
    monkeypatch.setattr(nodejs, "_has_lzma", lambda: False)
    assert nodejs.latest_release("linux-x64", lambda u: sums.encode())[1].endswith(".tar.gz")
    with pytest.raises(RuntimeError):
        nodejs.latest_release("linux-riscv64", lambda u: sums.encode())


def test_node_env_puts_node_first():
    env = nodejs.node_env(Path("/x/node/bin"), {"PATH": "/usr/bin"})
    assert env["PATH"].split(os.pathsep)[0] == "/x/node/bin"


def test_build_step_uses_ensure_node(tmp_path, monkeypatch):
    """The updater builds the Web UI with the Node that ensure_node returns."""
    from updater import steps

    bin_dir = tmp_path / "nodebin"
    bin_dir.mkdir()
    # fake npm: "ci" does nothing, "run build" writes dist/index.html
    _script(bin_dir / "npm", 'if [ "$1" = "run" ]; then mkdir -p dist && echo ok > dist/index.html; fi')
    _script(bin_dir / "node", "echo v22.11.0")
    monkeypatch.setattr("updater.nodejs.ensure_node", lambda base, allow_download=True: (bin_dir, ""))

    new, cur = tmp_path / "new", tmp_path / "cur"
    (new / "webui").mkdir(parents=True)
    (new / "webui" / "package.json").write_text("{}")
    (new / "webui" / "node_modules").mkdir()

    class Ctx:
        new_dir, current_dir = new, cur
        notes = []

        class layout:
            base = tmp_path

        def note(self, t):
            self.notes.append(t)

    ctx = Ctx()
    steps.BuildRelease()._build_webui(ctx)
    assert (new / "webui" / "dist" / "index.html").exists() and ctx.notes == []
    assert not (new / "webui" / "node_modules").exists()


def test_build_step_without_node_keeps_old_ui_and_says_why(tmp_path, monkeypatch):
    from updater import steps

    monkeypatch.setattr("updater.nodejs.ensure_node",
                        lambda base, allow_download=True: (None, "Node.js/npm not found, and Node.js could not be downloaded: offline"))
    new, cur = tmp_path / "new", tmp_path / "cur"
    (new / "webui").mkdir(parents=True)
    (new / "webui" / "package.json").write_text("{}")
    (cur / "webui" / "dist").mkdir(parents=True)
    (cur / "webui" / "dist" / "index.html").write_text("old")

    class Ctx:
        new_dir, current_dir = new, cur
        notes = []

        class layout:
            base = tmp_path

        def note(self, t):
            self.notes.append(t)

    ctx = Ctx()
    steps.BuildRelease()._build_webui(ctx)
    assert (new / "webui" / "dist" / "index.html").read_text() == "old"
    assert "could not be downloaded" in ctx.notes[0] and "nodejs.org" in ctx.notes[0]


def test_preflight_explains_missing_venv(monkeypatch):
    from updater import steps
    from updater.pipeline import UpdateError

    class R:
        returncode = 1
        stdout = "The virtual environment was not created successfully because ensurepip is not available."

    monkeypatch.setattr(steps, "sh", lambda *a, **k: R())
    with pytest.raises(UpdateError) as e:
        steps.check_venv_support("/usr/bin/python3", "3.12")
    assert "python3.12-venv" in str(e.value)


def test_venv_check_passes_on_a_working_python():
    import sys

    from updater import steps

    steps.check_venv_support(getattr(sys, "_base_executable", sys.executable), "3")


def test_updater_protocol_bumped_so_old_installs_hand_over():
    import updater

    assert updater.UPDATER_PROTOCOL >= 3
