import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
import subprocess
import myclaw.tools

@pytest.fixture
def mock_workspace(tmp_path, monkeypatch):
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    monkeypatch.setattr(myclaw.tools, "WORKSPACE", workspace)
    return workspace

def test_validate_path_valid(mock_workspace):
    path = "test.txt"
    result = myclaw.tools.validate_path(path)
    assert result == (mock_workspace / path).resolve()

def test_validate_path_traversal(mock_workspace):
    path = "../outside.txt"
    # Current implementation wraps Path traversal ValueError in "Invalid path" ValueError
    with pytest.raises(ValueError, match="Invalid path"):
        myclaw.tools.validate_path(path)

def test_shell_empty():
    assert myclaw.tools.shell("") == "Error: Empty command"
    assert myclaw.tools.shell("   ") == "Error: Empty command"

def test_shell_blocked():
    result = myclaw.tools.shell("rm -rf /")
    assert "Error: Command 'rm' is blocked for security" == result

def test_shell_not_allowed():
    result = myclaw.tools.shell("touch newfile")
    assert "Error: 'touch' not allowed." in result

def test_shell_allowed(mock_workspace):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="file1\nfile2\n", stderr="", returncode=0)
        result = myclaw.tools.shell("ls")
        assert "file1" in result
        mock_run.assert_called_once()

def test_shell_timeout():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="ls", timeout=30)):
        assert "Error: Command timed out after 30 seconds" == myclaw.tools.shell("ls")

def test_read_file_success(mock_workspace):
    test_file = mock_workspace / "hello.txt"
    test_file.write_text("hello world")
    assert myclaw.tools.read_file("hello.txt") == "hello world"

def test_read_file_not_found(mock_workspace):
    result = myclaw.tools.read_file("nonexistent.txt")
    assert result.startswith("Error:")
    assert "No such file or directory" in result

def test_read_file_traversal(mock_workspace):
    result = myclaw.tools.read_file("../traversal.txt")
    assert "Error: Invalid path" in result

def test_write_file_success(mock_workspace):
    result = myclaw.tools.write_file("new.txt", "content")
    assert result == "File written: new.txt"
    assert (mock_workspace / "new.txt").read_text() == "content"

def test_write_file_nested(mock_workspace):
    result = myclaw.tools.write_file("subdir/deep.txt", "deep content")
    assert result == "File written: subdir/deep.txt"
    assert (mock_workspace / "subdir" / "deep.txt").read_text() == "deep content"

def test_write_file_traversal(mock_workspace):
    result = myclaw.tools.write_file("../forbidden.txt", "evil")
    assert "Error: Invalid path" in result
