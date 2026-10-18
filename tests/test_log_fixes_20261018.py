"""Errors found in a user's zensynora.log (2026-10-18) — regression tests.

* relative imports inside myclaw/tools/ pointed at myclaw/tools/<x> instead of
  myclaw/<x> (swarm tools, extract_user_preferences, ssh tools, medic diagnosis,
  admin dashboard, `zensynora memory list|clear`)
* SQLitePool called a lock helper that only the async pool has
* rate limits / timeouts were logged as crashes and started a medic diagnosis
* configuration and MFA warnings were repeated hundreds of times
* a disabled TOOLBOX tool whose file was removed still warned
"""
import ast
import asyncio
import json
import logging
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent / "myclaw"


def _defined_names(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(n.name)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                for el in (t.elts if isinstance(t, ast.Tuple) else [t]):
                    if isinstance(el, ast.Name):
                        names.add(el.id)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def _lazy_names(path: Path) -> set:
    """Names a module only provides through a lazy registry (myclaw.tools)."""
    text = path.read_text(encoding="utf-8")
    return set() if "def __getattr__" not in text else {"*lazy*"}


def test_every_relative_import_points_at_a_real_module_and_name():
    problems = []
    for f in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.ImportFrom) and node.level > 0):
                continue
            base = f.parent
            for _ in range(node.level - 1):
                base = base.parent
            target_dir = base.joinpath(*node.module.split(".")) if node.module else base
            if target_dir.with_suffix(".py").exists():
                target = target_dir.with_suffix(".py")
            elif (target_dir / "__init__.py").exists():
                target = target_dir / "__init__.py"
            else:
                problems.append(f"{f.relative_to(ROOT.parent)}:{node.lineno} no module {'.' * node.level}{node.module}")
                continue
            if not target.resolve().is_relative_to(ROOT):
                problems.append(f"{f.relative_to(ROOT.parent)}:{node.lineno} import goes outside myclaw/")
                continue
            if node.module and target.resolve() == f.resolve():
                problems.append(f"{f.relative_to(ROOT.parent)}:{node.lineno} imports from itself")
                continue
            have = _defined_names(target)
            for a in node.names:
                sub = target.parent / a.name
                if a.name == "*" or a.name in have or sub.with_suffix(".py").exists() or (sub / "__init__.py").exists():
                    continue
                if _lazy_names(target):
                    # myclaw.tools: lazy names cannot be imported with "from .tools import x"
                    problems.append(f"{f.relative_to(ROOT.parent)}:{node.lineno} '{a.name}' is lazy in {target.parent.name}")
                    continue
                problems.append(f"{f.relative_to(ROOT.parent)}:{node.lineno} '{a.name}' not in {'.' * node.level}{node.module}")
    assert not problems, "\n".join(problems)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def test_swarm_tools_work(home):
    import myclaw.tools.swarm as swarm_tools

    monkey_orch = swarm_tools._swarm_orchestrator
    swarm_tools._swarm_orchestrator = None
    try:
        assert swarm_tools.swarm_list() == "No swarms found."
        assert swarm_tools.swarm_status("nope").startswith("Swarm not found")
    finally:
        swarm_tools._swarm_orchestrator = monkey_orch


def test_extract_user_preferences_imports_memory(home):
    from myclaw.tools.session import extract_user_preferences

    out = asyncio.run(extract_user_preferences("pytest-user"))
    assert "Not enough conversation history" in out


def test_sqlite_pool_get_and_release(tmp_path):
    from myclaw.memory import SQLitePool

    db = tmp_path / "p.db"
    conn = SQLitePool.get_connection(db)
    conn.execute("create table t(x)")
    SQLitePool.release_connection(db)
    assert SQLitePool.get_connection(db) is conn
    SQLitePool.release_connection(db)


def test_medic_diagnosis_runs(home):
    from myclaw.agent import Agent

    agent = Agent.__new__(Agent)
    summary, detail = asyncio.run(Agent._run_medic_diagnosis(agent, RuntimeError("boom"), "default"))
    assert "Medic Agent Diagnosis" in detail and "boom" in detail
    assert "failed" not in detail.lower().split("system health status")[0]


@pytest.mark.parametrize("msg, provider, rate", [
    ("Error code: 429 - google/gemma-4-31b-it:free is temporarily rate-limited upstream", True, True),
    ("Request timed out.", True, False),
    ("Error code: 400 - Muse v1 uncensored is not a valid model ID", True, False),
    ("KeyError: 'choices'", False, False),
])
def test_provider_error_classification(msg, provider, rate):
    from myclaw.llm_errors import is_provider_error, is_rate_limit

    assert is_provider_error(msg) is provider
    assert is_rate_limit(msg) is rate


def test_rate_limit_retried_once(monkeypatch):
    import myclaw.agent as agent_mod
    from myclaw.agent import Agent

    calls = []

    class P:
        async def chat(self, messages, model, stream=False):
            calls.append(model)
            if len(calls) == 1:
                raise RuntimeError("Error code: 429 - temporarily rate-limited upstream")
            return ("ok", None)

    agent = Agent.__new__(Agent)
    agent._provider = P()
    agent._provider_name = "openrouter"
    agent.name = "default"
    agent._provider_breaker = None
    agent._offline_mode = False
    agent._rotation_candidates = lambda: []
    agent._key_fallback_on = lambda: False
    monkeypatch.setattr(agent_mod, "RATE_LIMIT_RETRY_SECONDS", 0)
    assert asyncio.run(Agent._provider_chat(agent, [{"role": "user", "content": "hi"}], "m")) == ("ok", None)
    assert calls == ["m", "m"]


def test_config_warnings_logged_once(caplog):
    import myclaw.config as cfg

    c = cfg.AppConfig()
    c.security.allowed_commands = ["ls", "curl"]
    cfg._last_config_warnings = ()
    with caplog.at_level(logging.WARNING, logger="myclaw.config"):
        cfg._validate_config(c)
        cfg._validate_config(c)
        cfg._validate_config(c)
    warned = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warned) == 1 and "curl" in warned[0].getMessage()
    cfg._last_config_warnings = ()


def test_disabled_missing_toolbox_tool_does_not_warn(tmp_path, monkeypatch, caplog):
    import myclaw.tools.toolbox as tb

    reg = tmp_path / "toolbox_registry.json"
    reg.write_text(json.dumps({"gone": {"path": str(tmp_path / "gone.py"), "enabled": False}}))
    monkeypatch.setattr(tb, "TOOLBOX_REG", reg)
    with caplog.at_level(logging.WARNING):
        tb.load_custom_tools()
    assert not [r for r in caplog.records if "missing" in r.getMessage()]
