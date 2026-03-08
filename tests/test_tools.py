import pytest
import os
from pathlib import Path
from myclaw import tools

@pytest.fixture
def workspace(tmp_path):
    import unittest.mock
    with unittest.mock.patch("pathlib.Path.home", return_value=tmp_path):
        tools.WORKSPACE = tmp_path / "workspace"
        tools.WORKSPACE.mkdir(parents=True, exist_ok=True)
        yield tools.WORKSPACE

def test_validate_path(workspace):
    # Valid paths
    valid = tools.validate_path("test.txt")
    assert str(workspace) in str(valid)

    # Path traversal attempt
    with pytest.raises(ValueError):
        tools.validate_path("../outside.txt")

def test_write_and_read_file(workspace):
    tools.write_file("hello.txt", "Hello World!")
    content = tools.read_file("hello.txt")
    assert content == "Hello World!"

def test_shell_allowed_commands(workspace):
    # test writing a file with python? Wait, python is removed from allowed commands
    res = tools.shell("echo test")
    # echo is not in allowed commands!
    assert "Error: 'echo' not allowed" in res

    # ls is in allowed commands
    res = tools.shell("ls")
    assert "Error:" not in res or "Command timed out" not in res
