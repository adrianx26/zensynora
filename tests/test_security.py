from myclaw.tools import shell, WORKSPACE
import pytest

# Ensure workspace exists
WORKSPACE.mkdir(parents=True, exist_ok=True)

@pytest.mark.parametrize("cmd", ['python', 'python3', 'pip', 'pip3', 'node', 'npm', 'awk'])
def test_disallowed_commands(cmd):
    """Verify that potentially dangerous commands are not allowed in the shell tool."""
    if cmd == 'awk':
        result = shell("awk 'BEGIN {system(\"echo vulnerable_awk\")}'")
    else:
        result = shell(f"{cmd} --version")

    assert "Error" in result
    assert "not allowed" in result
    assert cmd in result

def test_allowed_commands():
    """Verify that safe commands are still allowed."""
    result = shell("ls --version")
    assert "ls" in result.lower()
    assert "Error" not in result
