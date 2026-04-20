from myclaw.tools import shell, WORKSPACE, _rate_limiter
import pytest

# Ensure workspace exists
WORKSPACE.mkdir(parents=True, exist_ok=True)

@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset rate limiter before each test to avoid rate limit exhaustion."""
    _rate_limiter._limits.clear()

@pytest.mark.parametrize("cmd", ['python', 'python3', 'pip', 'pip3', 'node', 'npm', 'awk'])
def test_disallowed_commands(cmd):
    """Verify that potentially dangerous commands are not allowed in the shell tool."""
    if cmd == 'awk':
        result = shell("awk 'BEGIN {system(\"echo vulnerable_awk\")}'")
    else:
        result = shell(f"{cmd} --version")

    assert "Error" in result
    assert ("not allowed" in result or "dangerous" in result.lower())
    # awk test uses dangerous chars which don't include 'awk' in the error message
    if cmd != 'awk':
        assert cmd in result

def test_allowed_commands():
    """Verify that safe commands are still allowed."""
    result = shell("git --version")
    assert "git" in result.lower()
    assert "Error" not in result
