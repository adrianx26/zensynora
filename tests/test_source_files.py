"""2026-11-05: agents could not find tool_executor.py.

list_source_files("tool_executor.py"), ("*executor*") and ("*tool_executor*") all
answered "No files match" because the glob only looked in the top program folder,
and read_source_file("tool_executor.py") said it does not exist. A file name or a
wildcard now searches every program folder; a path is matched at any depth; a miss
suggests close names; read_source_file finds a file by its name.
"""
import time

from myclaw.selfupdate import list_source_files, read_source_file

TE = "myclaw/agent_internals/tool_executor.py"


def test_file_names_and_wildcards_search_all_folders():
    for pattern in ("tool_executor.py", "*executor*", "*tool_executor*", "agent_internals/tool_executor.py",
                    "myclaw/**/*.py", "myclaw/agent_internals/*.py"):
        assert TE in list_source_files(pattern, limit=1000), pattern
    tools = list_source_files("myclaw/tools/*.py", limit=1000)
    assert "myclaw/tools/jobs.py" in tools and "myclaw/evolve.py" not in tools
    top = list_source_files("myclaw/**/*.py", limit=1000)
    assert "myclaw/evolve.py" in top          # files directly in myclaw/ too


def test_hidden_and_secret_files_stay_out():
    out = list_source_files("*", limit=1000)
    assert ".git/" not in out and "node_modules" not in out and ".env" not in out.split()


def test_a_miss_suggests_close_names():
    out = list_source_files("tool_executr.py")
    assert out.startswith("No files match") and TE in out and "file name" in out


def test_read_source_file_by_name():
    out = read_source_file("tool_executor.py", max_lines=5)
    assert out.startswith(TE) and "lines 1-5" in out
    many = read_source_file("__init__.py")
    assert "more than one" in many and "myclaw/__init__.py" in many
    assert "does not exist" in read_source_file("no_such_file_anywhere.py")
    assert read_source_file("../../etc/passwd").startswith("Error")


def test_fast():
    t = time.time()
    list_source_files("*.py", limit=1000)
    assert time.time() - t < 3
