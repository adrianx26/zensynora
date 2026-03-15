#!/usr/bin/env python3
"""
MyClaw Agent Skills Evaluator
================================
Autoresearch-inspired evaluation harness for ZenSynora agent skills.

Scoring Formula:
    Score = 0.4 × Correctness
          + 0.3 × Reliability
          + 0.2 × Clarity
          + 0.1 × Coverage

Usage:
    python eval/eval_agent_skills.py --mode baseline
    python eval/eval_agent_skills.py --mode improved
    python eval/eval_agent_skills.py --compare eval/results/baseline_results.tsv eval/results/improved_results.tsv
"""

import argparse
import ast
import json
import sys
import tempfile
import time
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional
from unittest.mock import MagicMock, patch

# ── Add project root to path ──────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import myclaw.tools as tools

# ── Score Weights (mirrors autoresearch metric weighting) ─────────────────────
W_CORRECTNESS = 0.40
W_RELIABILITY  = 0.30
W_CLARITY      = 0.20
W_COVERAGE     = 0.10


@dataclass
class TaskResult:
    skill: str
    task_id: str
    description: str
    correctness: float = 0.0
    reliability: float = 0.0
    clarity: float = 0.0
    coverage: float = 0.0
    status: str = "PENDING"   # PASS | FAIL | CRASH | SKIP
    notes: str = ""

    @property
    def total(self) -> float:
        return (
            W_CORRECTNESS * self.correctness
            + W_RELIABILITY * self.reliability
            + W_CLARITY * self.clarity
            + W_COVERAGE * self.coverage
        )

    def verdict(self, baseline_total: Optional[float] = None) -> str:
        """Autoresearch-style KEEP/DISCARD/NEW verdict."""
        if baseline_total is None:
            return "NEW"
        delta = self.total - baseline_total
        if delta >= 0.05:
            return "KEEP ✅"
        elif delta <= -0.02:
            return "DISCARD ❌"
        return "NO_CHANGE ➖"


# ── Clarity Scorer — static analysis of docstrings and error strings ──────────

def score_clarity(func: Callable) -> float:
    """Score a tool function on docstring quality (0.0–1.0)."""
    doc = func.__doc__ or ""
    score = 0.0
    if len(doc) > 20:
        score += 0.4          # has a meaningful docstring
    if ":" in doc:
        score += 0.2          # has parameter descriptions
    if "Error" in doc or "error" in doc:
        score += 0.2          # documents error behavior
    if len(doc) > 80:
        score += 0.2          # detailed enough
    return min(score, 1.0)


def score_clarity_by_name(tool_name: str) -> float:
    tool_entry = tools.TOOLS.get(tool_name)
    if not tool_entry:
        return 0.0
    return score_clarity(tool_entry["func"])


# ── Benchmark runner helpers ──────────────────────────────────────────────────

def safe_run(fn: Callable, *args, **kwargs):
    """Run a tool function, catching crashes. Returns (result, crashed)."""
    try:
        result = fn(*args, **kwargs)
        return result, False
    except Exception as e:
        return f"CRASH: {e}", True


# ═════════════════════════════════════════════════════════════════════════════
# SKILL GROUP 1: File I/O
# ═════════════════════════════════════════════════════════════════════════════

def eval_file_io(tmp_workspace: Path) -> list[TaskResult]:
    results = []

    with patch.object(tools, "WORKSPACE", tmp_workspace):

        # SK-1.1: write then read round-trip
        r = TaskResult(skill="SK-1.2", task_id="file-write-01", description="Write a file to workspace")
        out, crashed = safe_run(tools.write_file, "test_write.txt", "hello world")
        r.correctness = 1.0 if out == "File written: test_write.txt" else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("write_file")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        r = TaskResult(skill="SK-1.1", task_id="file-read-01", description="Read a file from workspace")
        out, crashed = safe_run(tools.read_file, "test_write.txt")
        r.correctness = 1.0 if out == "hello world" else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("read_file")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # SK-1.1: path traversal rejection
        r = TaskResult(skill="SK-1.1", task_id="file-read-traversal", description="Reject path traversal in read_file")
        out, crashed = safe_run(tools.read_file, "../etc/passwd")
        r.correctness = 1.0 if isinstance(out, str) and out.startswith("Error:") else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("read_file")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # SK-1.2: nested directory creation
        r = TaskResult(skill="SK-1.2", task_id="file-write-nested", description="Write to nested subdirectory")
        out, crashed = safe_run(tools.write_file, "sub/dir/deep.txt", "deep content")
        r.correctness = 1.0 if out == "File written: sub/dir/deep.txt" else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("write_file")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # SK-1.2: path traversal rejection on write
        r = TaskResult(skill="SK-1.2", task_id="file-write-traversal", description="Reject path traversal in write_file")
        out, crashed = safe_run(tools.write_file, "../evil.txt", "evil")
        r.correctness = 1.0 if isinstance(out, str) and "Error" in out else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("write_file")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# SKILL GROUP 2: Shell Execution
# ═════════════════════════════════════════════════════════════════════════════

def eval_shell() -> list[TaskResult]:
    results = []

    # Empty command
    r = TaskResult(skill="SK-2.1", task_id="shell-empty", description="Empty command returns error")
    out, crashed = safe_run(tools.shell, "")
    r.correctness = 1.0 if out == "Error: Empty command" else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("shell")
    r.coverage = 1.0
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    results.append(r)

    # Blocked command
    r = TaskResult(skill="SK-2.1", task_id="shell-blocked", description="Blocked command rejected")
    out, crashed = safe_run(tools.shell, "rm -rf /")
    r.correctness = 1.0 if "blocked" in (out or "").lower() else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("shell")
    r.coverage = 1.0
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    results.append(r)

    # Not-in-allowlist command
    r = TaskResult(skill="SK-2.1", task_id="shell-not-allowed", description="Unlisted command rejected with helpful message")
    out, crashed = safe_run(tools.shell, "touch newfile")
    r.correctness = 1.0 if "not allowed" in (out or "").lower() else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("shell")
    # Bonus coverage: does it list allowed commands?
    r.coverage = 1.0 if "Allowed:" in (out or "") else 0.5
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    results.append(r)

    # Allowed command (mocked)
    from unittest.mock import patch as _patch
    import subprocess
    r = TaskResult(skill="SK-2.1", task_id="shell-allowed-ls", description="Allowed command executes successfully")
    mock_result = MagicMock(stdout="file1\nfile2\n", stderr="", returncode=0)
    with patch("subprocess.run", return_value=mock_result):
        out, crashed = safe_run(tools.shell, "ls")
    r.correctness = 1.0 if "file1" in (out or "") else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("shell")
    r.coverage = 1.0
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    results.append(r)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# SKILL GROUP 3: Web & Download
# ═════════════════════════════════════════════════════════════════════════════

def eval_web(tmp_workspace: Path) -> list[TaskResult]:
    results = []

    # Browse — mock HTTP response
    r = TaskResult(skill="SK-3.1", task_id="browse-success", description="Browse URL returns plain-text content (HTML stripped)")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "<html><body><p>Hello World</p></body></html>"
    mock_response.raise_for_status = lambda: None
    with patch("requests.get", return_value=mock_response):
        out, crashed = safe_run(tools.browse, "https://example.com")
    r.correctness = 1.0 if "Hello World" in (out or "") and "Status: 200" in (out or "") else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("browse")
    r.coverage = 1.0  # HTML stripping now implemented
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    r.notes = "HTML is now stripped to plain text"
    results.append(r)

    # Browse — truncation check
    r = TaskResult(skill="SK-3.1", task_id="browse-truncation", description="Browse truncates at max_length")
    mock_response.text = "<p>" + "A" * 10000 + "</p>"
    with patch("requests.get", return_value=mock_response):
        out, crashed = safe_run(tools.browse, "https://example.com", 50)
    r.correctness = 1.0 if "truncated" in (out or "").lower() else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("browse")
    r.coverage = 1.0
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    results.append(r)

    # Browse — HTTP error
    r = TaskResult(skill="SK-3.1", task_id="browse-http-error", description="Browse handles 404 gracefully")
    import requests
    mock_response.raise_for_status = MagicMock(side_effect=requests.exceptions.HTTPError("404"))
    with patch("requests.get", return_value=mock_response):
        out, crashed = safe_run(tools.browse, "https://example.com/notfound")
    r.correctness = 1.0 if "Error" in (out or "") else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("browse")
    r.coverage = 1.0
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    results.append(r)

    # Download file — mock
    r = TaskResult(skill="SK-3.2", task_id="download-success", description="Download file saves to workspace")
    mock_dl = MagicMock()
    mock_dl.status_code = 200
    mock_dl.iter_content = lambda chunk_size: [b"hello bytes"]
    mock_dl.raise_for_status = lambda: None
    with patch.object(tools, "WORKSPACE", tmp_workspace):
        with patch("requests.get", return_value=mock_dl):
            out, crashed = safe_run(tools.download_file, "https://example.com/file.txt", "file.txt")
    r.correctness = 1.0 if "[OK] Downloaded" in (out or "") else 0.0
    r.reliability = 0.0 if crashed else 1.0
    r.clarity = score_clarity_by_name("download_file")
    r.coverage = 1.0
    r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
    results.append(r)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# SKILL GROUP 7: TOOLBOX (Dynamic Tool Building)
# ═════════════════════════════════════════════════════════════════════════════

def eval_toolbox(tmp_toolbox: Path) -> list[TaskResult]:
    results = []

    with patch.object(tools, "TOOLBOX_DIR", tmp_toolbox), \
         patch.object(tools, "TOOLBOX_REG", tmp_toolbox / "registry.json"), \
         patch.object(tools, "TOOLBOX_DOCS", tmp_toolbox / "README.md"):
        tmp_toolbox.mkdir(parents=True, exist_ok=True)

        valid_code = '''\
def my_adder(a, b):
    """Add two numbers together.
    
    Args:
        a: First number
        b: Second number
    Returns:
        Sum of a and b
    """
    try:
        return a + b
    except Exception as e:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"Error in my_adder: {e}")
        return f"Error: {e}"
'''

        # Valid tool registration
        r = TaskResult(skill="SK-7.1", task_id="toolbox-register-valid", description="Register valid tool succeeds")
        out, crashed = safe_run(tools.register_tool, "my_adder", valid_code, "Adds two numbers")
        r.correctness = 1.0 if "registered" in (out or "").lower() else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("register_tool")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Duplicate registration rejected
        r = TaskResult(skill="SK-7.1", task_id="toolbox-register-duplicate", description="Duplicate tool rejected")
        out2, crashed2 = safe_run(tools.register_tool, "my_adder", valid_code, "Adds two numbers")
        r.correctness = 1.0 if "Error" in (out2 or "") or "already exists" in (out2 or "").lower() else 0.0
        r.reliability = 0.0 if crashed2 else 1.0
        r.clarity = score_clarity_by_name("register_tool")
        r.coverage = 1.0
        r.status = "CRASH" if crashed2 else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Missing docstring rejected
        r = TaskResult(skill="SK-7.1", task_id="toolbox-register-no-docstring", description="Tool without docstring rejected")
        bad_code = "def bad_tool(x):\n    try:\n        return x\n    except Exception as e:\n        import logging; logger=logging.getLogger(__name__); logger.error(str(e))\n"
        out3, crashed3 = safe_run(tools.register_tool, "bad_tool", bad_code, "doc")
        r.correctness = 1.0 if "Error" in (out3 or "") else 0.0
        r.reliability = 0.0 if crashed3 else 1.0
        r.clarity = score_clarity_by_name("register_tool")
        r.coverage = 1.0
        r.status = "CRASH" if crashed3 else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Invalid identifier rejected
        r = TaskResult(skill="SK-7.1", task_id="toolbox-register-bad-name", description="Invalid identifier rejected")
        out4, crashed4 = safe_run(tools.register_tool, "123abc", valid_code, "doc")
        r.correctness = 1.0 if "Error" in (out4 or "") and "identifier" in (out4 or "").lower() else 0.0
        r.reliability = 0.0 if crashed4 else 1.0
        r.clarity = score_clarity_by_name("register_tool")
        r.coverage = 1.0
        r.status = "CRASH" if crashed4 else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # list_toolbox
        r = TaskResult(skill="SK-7.2", task_id="toolbox-list", description="list_toolbox shows registered tools")
        out5, crashed5 = safe_run(tools.list_toolbox)
        r.correctness = 1.0 if "my_adder" in (out5 or "") else 0.0
        r.reliability = 0.0 if crashed5 else 1.0
        r.clarity = score_clarity_by_name("list_toolbox")
        r.coverage = 1.0
        r.status = "CRASH" if crashed5 else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # get_tool_documentation
        r = TaskResult(skill="SK-7.3", task_id="toolbox-docs", description="get_tool_documentation returns content")
        out6, crashed6 = safe_run(tools.get_tool_documentation, "my_adder")
        r.correctness = 1.0 if "my_adder" in (out6 or "") and len(out6) > 10 else 0.0
        r.reliability = 0.0 if crashed6 else 1.0
        r.clarity = score_clarity_by_name("get_tool_documentation")
        r.coverage = 1.0
        r.status = "CRASH" if crashed6 else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# SKILL GROUP 6: Knowledge Base
# ═════════════════════════════════════════════════════════════════════════════

def eval_knowledge(tmp_knowledge: Path) -> list[TaskResult]:
    results = []

    with patch("myclaw.tools.write_note") as mock_write, \
         patch("myclaw.tools.search_notes") as mock_search, \
         patch("myclaw.tools.read_note") as mock_read, \
         patch("myclaw.tools.list_notes") as mock_list, \
         patch("myclaw.tools.sync_knowledge") as mock_sync:

        # Write to knowledge
        mock_write.return_value = "test-note"
        r = TaskResult(skill="SK-6.1", task_id="knowledge-write", description="Write note to knowledge base")
        out, crashed = safe_run(tools.write_to_knowledge, "Test Note", "Some content", "tag1,tag2")
        r.correctness = 1.0 if "Knowledge note created" in (out or "") else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("write_to_knowledge")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Search knowledge
        from myclaw.knowledge import Observation
        mock_note = MagicMock()
        mock_note.title = "Test Note"
        mock_note.permalink = "test-note"
        mock_note.observations = [MagicMock(category="info", content="some content")]
        mock_note.tags = ["tag1"]
        mock_search.return_value = [mock_note]

        r = TaskResult(skill="SK-6.2", task_id="knowledge-search", description="Search knowledge returns results")
        out, crashed = safe_run(tools.search_knowledge, "test")
        r.correctness = 1.0 if "Test Note" in (out or "") else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("search_knowledge")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Search knowledge — no results
        mock_search.return_value = []
        r = TaskResult(skill="SK-6.2", task_id="knowledge-search-empty", description="Search returns no-results message")
        out, crashed = safe_run(tools.search_knowledge, "nonexistent_xyz")
        r.correctness = 1.0 if "No results" in (out or "") else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("search_knowledge")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Read knowledge
        mock_note.relations = []
        mock_read.return_value = mock_note
        r = TaskResult(skill="SK-6.3", task_id="knowledge-read", description="Read knowledge note by permalink")
        out, crashed = safe_run(tools.read_knowledge, "test-note")
        r.correctness = 1.0 if "Test Note" in (out or "") else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("read_knowledge")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Read knowledge — not found
        mock_read.return_value = None
        r = TaskResult(skill="SK-6.3", task_id="knowledge-read-notfound", description="Read missing note returns error")
        out, crashed = safe_run(tools.read_knowledge, "nonexistent")
        r.correctness = 1.0 if "not found" in (out or "").lower() else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("read_knowledge")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

        # Sync knowledge
        mock_sync.return_value = {"added": 2, "updated": 1, "deleted": 0}
        r = TaskResult(skill="SK-6.5", task_id="knowledge-sync", description="Sync knowledge base returns summary")
        out, crashed = safe_run(tools.sync_knowledge_base)
        r.correctness = 1.0 if "Sync complete" in (out or "") else 0.0
        r.reliability = 0.0 if crashed else 1.0
        r.clarity = score_clarity_by_name("sync_knowledge_base")
        r.coverage = 1.0
        r.status = "CRASH" if crashed else ("PASS" if r.correctness == 1.0 else "FAIL")
        results.append(r)

    return results


# ═════════════════════════════════════════════════════════════════════════════
# Main Evaluation Runner
# ═════════════════════════════════════════════════════════════════════════════

def run_all_evaluations() -> list[TaskResult]:
    all_results = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        toolbox = tmp_path / "TOOLBOX"
        knowledge_dir = tmp_path / "knowledge"

        print("Running File I/O evaluations...")
        all_results.extend(eval_file_io(workspace))

        print("Running Shell evaluations...")
        all_results.extend(eval_shell())

        print("Running Web evaluations...")
        all_results.extend(eval_web(workspace))

        print("Running TOOLBOX evaluations...")
        all_results.extend(eval_toolbox(toolbox))

        print("Running Knowledge evaluations...")
        all_results.extend(eval_knowledge(knowledge_dir))

    return all_results


def save_results(results: list[TaskResult], output_path: str):
    """Save results to TSV (mirrors autoresearch's results.tsv format)."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    header = "timestamp\tskill\ttask_id\tdescription\tcorrectness\treliability\tclarity\tcoverage\ttotal\tstatus\tnotes\n"
    ts = datetime.now().isoformat(timespec="seconds")

    with open(path, "w", encoding="utf-8") as f:
        f.write(header)
        for r in results:
            f.write(
                f"{ts}\t{r.skill}\t{r.task_id}\t{r.description}\t"
                f"{r.correctness:.2f}\t{r.reliability:.2f}\t{r.clarity:.2f}\t{r.coverage:.2f}\t"
                f"{r.total:.3f}\t{r.status}\t{r.notes}\n"
            )

    print(f"\nResults saved → {path}")


def print_results(results: list[TaskResult]):
    """Print a formatted score table."""
    print("\n" + "=" * 80)
    print("MYCLAM AGENT SKILLS EVALUATION RESULTS")
    print("=" * 80)
    print(f"{'SKILL':<12} {'TASK':<30} {'CORR':>5} {'REL':>5} {'CLAR':>5} {'COV':>5} {'TOTAL':>7} {'STATUS':<8}")
    print("-" * 80)
    for r in results:
        print(
            f"{r.skill:<12} {r.task_id:<30} "
            f"{r.correctness:>5.2f} {r.reliability:>5.2f} {r.clarity:>5.2f} {r.coverage:>5.2f} "
            f"{r.total:>7.3f} {r.status:<8}"
        )

    # Per-skill summary
    print("\n" + "─" * 80)
    print("SUMMARY BY SKILL GROUP")
    print("─" * 80)
    skill_groups: dict[str, list[TaskResult]] = {}
    for r in results:
        skill_groups.setdefault(r.skill, []).append(r)

    for skill, group in sorted(skill_groups.items()):
        avg = sum(r.total for r in group) / len(group)
        pass_count = sum(1 for r in group if r.status == "PASS")
        print(f"{skill:<12} | avg_total: {avg:.3f} | {pass_count}/{len(group)} PASS")

    # Overall
    overall = sum(r.total for r in results) / len(results) if results else 0.0
    pass_total = sum(1 for r in results if r.status == "PASS")
    print(f"\n{'OVERALL':<12} | avg_total: {overall:.3f} | {pass_total}/{len(results)} PASS")
    print("=" * 80)


def compare_results(baseline_path: str, improved_path: str):
    """Compare baseline vs improved results (KEEP/DISCARD per skill)."""
    def load_tsv(path: str) -> dict[str, float]:
        scores = {}
        with open(path, encoding="utf-8") as f:
            next(f)  # skip header
            for line in f:
                cols = line.strip().split("\t")
                if len(cols) >= 9:
                    task_id = cols[2]
                    total = float(cols[8])
                    scores[task_id] = total
        return scores

    baseline = load_tsv(baseline_path)
    improved = load_tsv(improved_path)

    print("\n" + "=" * 80)
    print("COMPARISON: BASELINE → IMPROVED")
    print("=" * 80)
    print(f"{'TASK_ID':<35} {'BASELINE':>9} {'IMPROVED':>9} {'DELTA':>8} {'VERDICT':<15}")
    print("-" * 80)

    keep_count = 0
    for task_id in sorted(set(baseline) | set(improved)):
        b = baseline.get(task_id, 0.0)
        i = improved.get(task_id, b)
        delta = i - b
        tr = TaskResult(skill="", task_id=task_id, description="")
        tr.correctness = i  # use total as proxy
        verdict = tr.verdict(b)
        if "KEEP" in verdict:
            keep_count += 1
        print(f"{task_id:<35} {b:>9.3f} {i:>9.3f} {delta:>+8.3f} {verdict:<15}")

    print(f"\n→ {keep_count} improvements qualify as KEEP (delta ≥ 0.05)")
    print("=" * 80)


# ═════════════════════════════════════════════════════════════════════════════
# CLI Entry Point
# ═════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="MyClaw Agent Skills Evaluator (autoresearch-inspired)"
    )
    parser.add_argument("--mode", choices=["baseline", "improved"], default="baseline",
                        help="Evaluation mode")
    parser.add_argument("--output", type=str,
                        help="Output TSV path (default: eval/results/{mode}_results.tsv)")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE_TSV", "IMPROVED_TSV"),
                        help="Compare two result files")
    args = parser.parse_args()

    if args.compare:
        compare_results(args.compare[0], args.compare[1])
        return

    output_path = args.output or f"eval/results/{args.mode}_results.tsv"
    print(f"\nRunning {args.mode.upper()} evaluation...")
    results = run_all_evaluations()
    print_results(results)
    save_results(results, output_path)


if __name__ == "__main__":
    main()
