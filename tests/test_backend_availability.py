import subprocess

import pytest

from myclaw.backends.docker import DockerBackend
from myclaw.backends.wsl2 import WSL2Backend


def test_docker_availability_uses_a_bounded_sync_probe(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("myclaw.backends.docker.subprocess.run", run)

    assert DockerBackend()._check_availability()
    assert calls == [(["docker", "--version"], {"capture_output": True, "timeout": 5})]


@pytest.mark.parametrize(
    ("stdout", "returncode", "expected"),
    [(b"Ubuntu", 0, True), (b"Debian", 0, False), (b"Ubuntu", 1, False)],
)
def test_wsl_availability_uses_a_bounded_sync_probe(monkeypatch, stdout, returncode, expected):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, returncode, stdout=stdout)

    monkeypatch.setattr("myclaw.backends.wsl2.subprocess.run", run)

    assert WSL2Backend()._check_availability() is expected
    assert calls == [(["wsl", "--list"], {"capture_output": True, "timeout": 5})]
