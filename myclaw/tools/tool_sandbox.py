"""Agent-made tools (TOOLBOX) run in a separate, locked-down process (2026-10-28).

The rules re-review showed that checking the source of an agent's tool is not
enough: Python offers too many ways around any list of forbidden names. So a
tool made with ``register_tool`` (or imported with ``register_external_skill``)
no longer runs inside ZenSynora. Each call starts
``python -I -B -X utf8 _tool_runner.py``:

* working folder = the agent workspace, ``HOME`` = ``<workspace>/.tool-home``
  (``~`` never points at ``~/.myclaw``), no proxy settings, a timeout
  (``sandbox.max_time_seconds``);
* arguments go in and the result comes out as JSON (other results become text);
* the child locks itself down before the tool's code runs — Landlock on Linux
  (write only in the workspace, no home folder, TCP only to
  ``sandbox.tool_ports``), plus an audit hook everywhere (no programs, no
  ctypes, no ~/.myclaw, no connections to this computer or the local network).

The code that runs is the code that was checked: it is read once when the tool
is loaded and sent to the child, so changing the file later has no effect until
the tool is loaded (and checked) again. Built-in tools keep running in-process.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

RUNNER = Path(__file__).with_name("_tool_runner.py")
MARK = "@@ZENSYNORA-TOOL-RESULT@@ "
DEFAULT_TIMEOUT = 30
DEFAULT_PORTS = (80, 443)
_TYPES = {"int": int, "str": str, "bool": bool, "float": float}
_KEEP_ENV = ("PATH", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE",
             "CURL_CA_BUNDLE", "SYSTEMROOT", "COMSPEC")


def workspace() -> Path:
    from . import core

    return Path(core.WORKSPACE)


def sandbox_home() -> Path:
    return workspace() / ".tool-home"


def _settings() -> tuple[int, list[int]]:
    try:
        from ..config import load_config

        sb = load_config().sandbox
        timeout = int(getattr(sb, "max_time_seconds", DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT)
        ports = [int(p) for p in (getattr(sb, "tool_ports", None) or DEFAULT_PORTS)]
        return max(1, min(timeout, 3600)), ports
    except Exception:
        return DEFAULT_TIMEOUT, list(DEFAULT_PORTS)


# ── what the tool looks like (from its source, without running it here) ─────
def describe(code: str, name: str) -> tuple[inspect.Signature, str]:
    """Signature and docstring of function ``name`` in ``code`` (ValueError if missing)."""
    tree = ast.parse(code)
    fn = next((n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name),
              None)
    if fn is None:
        raise ValueError(f"code must define a function named '{name}'.")

    def ann(node: Optional[ast.expr]) -> Any:
        if isinstance(node, ast.Name) and node.id in _TYPES:
            return _TYPES[node.id]
        if isinstance(node, ast.Constant) and node.value in _TYPES:
            return _TYPES[node.value]
        return inspect.Parameter.empty

    def default(node: Optional[ast.expr]) -> Any:
        if node is None:
            return inspect.Parameter.empty
        try:
            return ast.literal_eval(node)
        except (ValueError, SyntaxError):
            return None

    a = fn.args
    pos = list(a.posonlyargs) + list(a.args)
    defaults: list[Optional[ast.expr]] = [None] * (len(pos) - len(a.defaults)) + list(a.defaults)
    params = [inspect.Parameter(p.arg, inspect.Parameter.POSITIONAL_OR_KEYWORD, default=default(d),
                                annotation=ann(p.annotation)) for p, d in zip(pos, defaults)]
    params += [inspect.Parameter(p.arg, inspect.Parameter.KEYWORD_ONLY, default=default(d), annotation=ann(p.annotation))
               for p, d in zip(a.kwonlyargs, a.kw_defaults)]
    return inspect.Signature(params), ast.get_docstring(fn) or ""


class SandboxedTool:
    """Callable stand-in for an agent-made tool; each call runs it in the sandbox."""

    def __init__(self, name: str, code: str, path: str = ""):
        self.name = name
        self.code = code
        self.path = str(path)
        sig, doc = describe(code, name)
        self.__signature__ = sig
        self.__doc__ = doc or None
        self.__name__ = name
        self.sandboxed = True

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        try:
            bound = self.__signature__.bind(*args, **kwargs)
        except TypeError as e:
            return f"Error: {e}. Arguments of {self.name}: {self.__signature__}"
        return run(self.name, self.code, dict(bound.arguments), path=self.path)

    def __repr__(self) -> str:
        return f"<sandboxed tool {self.name}>"


def _request(name: str, code: str, args: dict, path: str, mode: str) -> tuple[list[str], str, dict, int]:
    ws = workspace()
    home = sandbox_home()
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    timeout, ports = _settings()
    payload = json.dumps({"name": name, "code": code, "args": args, "file": path or f"{name}.py",
                          "workspace": str(ws), "real_home": str(Path.home()), "ports": ports,
                          "timeout": timeout, "mode": mode}, default=str)
    env = {k: os.environ[k] for k in _KEEP_ENV if k in os.environ}
    env.update(HOME=str(home), TMPDIR=str(home / "tmp"), TEMP=str(home / "tmp"), TMP=str(home / "tmp"),
               USERPROFILE=str(home))
    cmd = [sys.executable, "-I", "-B", "-X", "utf8", str(RUNNER)]
    return cmd, payload, env, timeout


def _run(name: str, code: str, args: dict, path: str = "", mode: str = "call") -> dict:
    cmd, payload, env, timeout = _request(name, code, args, path, mode)
    try:
        r = subprocess.run(cmd, input=payload, capture_output=True, text=True, timeout=timeout + 5,
                           cwd=str(workspace()), env=env)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"the tool '{name}' did not finish within {timeout} s "
                                      "(Settings → Features → sandbox.max_time_seconds)."}
    except OSError as e:
        return {"ok": False, "error": f"the tool sandbox could not start ({e})."}
    for line in reversed((r.stdout or "").split("\n")):
        if line.startswith(MARK):
            try:
                return json.loads(line[len(MARK):])
            except ValueError:
                break
    tail = ((r.stderr or "") + (r.stdout or ""))[-800:].strip()
    return {"ok": False, "error": f"the tool '{name}' stopped without a result (exit {r.returncode}). {tail}"}


def run(name: str, code: str, args: dict, path: str = "") -> Any:
    """Call tool ``name`` in the sandbox; its result, or an 'Error: …' text."""
    data = _run(name, code, args, path)
    if data.get("ok"):
        return data.get("result")
    logger.info("Sandboxed tool %s failed: %s", name, str(data.get("error"))[:200])
    return f"Error: {data.get('error')}"


def check(name: str, code: str, path: str = "") -> Optional[str]:
    """Load the tool in the sandbox without calling it; an error message or None."""
    try:
        describe(code, name)
    except (ValueError, SyntaxError) as e:
        return str(e)
    data = _run(name, code, {}, path, mode="check")
    return None if data.get("ok") else str(data.get("error"))


def status() -> dict:
    """Whether the kernel lock-down works here (for the doctor)."""
    data = _run("probe", "def probe():\n    return 1\n", {}, mode="check")
    return {"ok": bool(data.get("ok")), "landlock": data.get("landlock", "unknown"),
            "error": data.get("error", "")}


def load(name: str, path: Path | str) -> SandboxedTool:
    """Read a TOOLBOX file once and return its sandboxed stand-in (ValueError/OSError if unusable)."""
    code = Path(path).read_text(encoding="utf-8")
    return SandboxedTool(name, code, str(path))
