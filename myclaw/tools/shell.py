"""
Tools — Shell Execution

Commands from the allow-list run in ~/.myclaw/workspace **without a shell**
(no shell interpretation, so nothing can be injected).

2026-10-21: chains of allowed commands work — ``ps aux | grep zensynora``,
``pwd && ls -la``, ``ls ; git status``, ``a || b``. Every command in the chain
must be allowed; pipes are connected between the processes directly. Still
refused: ``$``, backticks, ``\\``, ``{}``, newlines, redirection (``<``/``>``),
subshells/grouping ``( )`` and background ``&``. Before, any ``|`` or ``&&``
was refused, and agents spent many tool steps working around it.
"""

import asyncio
import shlex
import subprocess
import logging
import re
import time
from typing import List, Tuple

from . import core as _core
from .core import (
    WORKSPACE,
    _rate_limiter,
    _tool_audit_logger,
)

logger = logging.getLogger(__name__)

TIMEOUT = 30
MAX_COMMANDS = 8
OPERATORS = ("|", "&&", "||", ";")
# Never allowed anywhere (also not inside quotes): variable / command
# substitution, escapes, brace expansion, multi-line input.
FORBIDDEN = re.compile(r"[\n\r`$\\{}]")
# Arguments that turn an allowed command into one that runs or deletes things.
FORBIDDEN_ARGS = {
    "find": {"-delete", "-exec", "-execdir", "-ok", "-okdir", "-fprint", "-fprintf", "-fls"},
}
HINT = ("Allowed between commands: | && || ; — not allowed: $ ` \\ { } < > ( ) "
        "background & or new lines. Run one simple command or a chain of allowed commands.")


class ShellError(ValueError):
    """A command that is refused; the message is shown to the agent."""


def _allowed() -> frozenset:
    # Read at call time: update_allowlist()/the settings replace the set in core.
    return _core.ALLOWED_COMMANDS


def parse(cmd: str) -> List[Tuple[str, List[List[str]]]]:
    """Split into [(op_before, pipeline)], where pipeline = [argv, argv …].

    op_before is "" for the first pipeline, else "&&", "||" or ";".
    Raises ShellError with a readable reason when the command is refused.
    """
    if not cmd or not cmd.strip():
        raise ShellError("Error: Empty command")
    if FORBIDDEN.search(cmd):
        raise ShellError(f"Error: Command contains dangerous characters. {HINT}")
    # Non-POSIX mode keeps the quotes on tokens, so a quoted "|" or "a;b" is an
    # argument and never an operator; the quotes are removed afterwards.
    lex = shlex.shlex(cmd, posix=False, punctuation_chars=";&|()<>")
    lex.whitespace_split = True
    try:
        tokens = list(lex)
    except ValueError as e:  # unbalanced quotes
        raise ShellError(f"Error: Could not read the command ({e}).")
    groups: List[Tuple[str, List[List[str]]]] = []
    pipeline: List[List[str]] = []
    argv: List[str] = []
    op_before = ""
    count = 0

    def close_argv():
        nonlocal argv, count
        if not argv:
            raise ShellError(f"Error: Missing command around an operator. {HINT}")
        pipeline.append(argv)
        count += 1
        argv = []

    for tok in tokens:
        if tok and set(tok) <= set(";&|()<>"):
            if tok not in OPERATORS:
                raise ShellError(f"Error: '{tok}' is not allowed. {HINT}")
            close_argv()
            if tok != "|":
                groups.append((op_before, pipeline))
                pipeline = []
                op_before = tok
            continue
        try:
            argv.append("".join(shlex.split(tok)))
        except ValueError as e:
            raise ShellError(f"Error: Could not read the command ({e}).")
    if not argv and not pipeline and not groups:
        raise ShellError("Error: Empty command")
    close_argv()
    groups.append((op_before, pipeline))
    if count > MAX_COMMANDS:
        raise ShellError(f"Error: At most {MAX_COMMANDS} commands in one call.")

    allowed = _allowed()
    for _, pl in groups:
        for args in pl:
            name = args[0].lower()
            if name in _core.BLOCKED_COMMANDS:
                logger.warning(f"Blocked command attempted: {name}")
                raise ShellError(f"Error: Command '{name}' is blocked for security")
            if name not in allowed:
                raise ShellError(f"Error: '{name}' not allowed. Allowed: {', '.join(sorted(allowed))}")
            bad = FORBIDDEN_ARGS.get(name, set()) & set(args[1:])
            if bad:
                raise ShellError(f"Error: '{name} {sorted(bad)[0]}' is not allowed.")
    return groups


def _run_pipeline(pipeline: List[List[str]], deadline: float) -> Tuple[int, str]:
    """Run argv1 | argv2 | … without a shell; returns (exit code of the last, output)."""
    if len(pipeline) == 1:
        r = subprocess.run(pipeline[0], shell=False, cwd=WORKSPACE, capture_output=True, text=True,
                           timeout=max(1.0, deadline - time.time()))
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    procs: List[subprocess.Popen] = []
    prev = None
    try:
        for i, argv in enumerate(pipeline):
            p = subprocess.Popen(argv, shell=False, cwd=WORKSPACE, stdin=prev,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if prev is not None:
                prev.close()  # let the earlier command get SIGPIPE when this one exits
            prev = p.stdout
            procs.append(p)
        out, err_last = procs[-1].communicate(timeout=max(1.0, deadline - time.time()))
        errs = []
        for p in procs[:-1]:
            p.wait(timeout=max(1.0, deadline - time.time()))
            if p.stderr:
                errs.append(p.stderr.read().decode(errors="replace"))
        text = out.decode(errors="replace") + "".join(errs) + err_last.decode(errors="replace")
        return procs[-1].returncode, text
    finally:
        for p in procs:
            if p.poll() is None:
                p.kill()


def _execute(groups: List[Tuple[str, List[List[str]]]]) -> str:
    deadline = time.time() + TIMEOUT
    out: List[str] = []
    code = 0
    for op, pipeline in groups:
        if op == "&&" and code != 0:
            continue
        if op == "||" and code == 0:
            continue
        code, text = _run_pipeline(pipeline, deadline)
        out.append(text)
    return "".join(out)


def shell(cmd: str) -> str:
    """Execute allowed commands in the workspace directory (~/.myclaw/workspace).

    cmd: one command (e.g. 'ls -la', 'grep -rn TODO .') or a chain of allowed
         commands joined with | && || ; (e.g. 'ps aux | grep zensynora',
         'pwd && ls -la'). Not allowed: $ ` \\ { } < > ( ), background &,
         new lines, and find -delete / -exec.

    Returns combined stdout+stderr, or an 'Error: …' that says what to change.
    """
    start_time = time.time()
    try:
        if not _rate_limiter.check("shell", max_calls=10, window=60):
            _tool_audit_logger.log("shell", "", 0, False, "Rate limit exceeded")
            return ("Error: Rate limit exceeded for shell tool (10 calls/minute) — "
                    "combine steps with | or && in one call.")
        try:
            groups = parse(cmd)
        except ShellError as e:
            if "dangerous" in str(e) or "not allowed" in str(e) or "blocked" in str(e):
                logger.info(f"Shell command refused: {cmd[:200]} — {str(e)[:120]}")
                _tool_audit_logger.log("shell", "", 0, False, str(e)[:120])
            return str(e)
        result = _execute(groups)
        _tool_audit_logger.log("shell", "", (time.time() - start_time) * 1000, True)
        return result
    except subprocess.TimeoutExpired:
        _tool_audit_logger.log("shell", "", (time.time() - start_time) * 1000, False, "Command timed out")
        return f"Error: Command timed out after {TIMEOUT} seconds"
    except FileNotFoundError as e:
        return f"Error: command not found on this computer: {e.filename or e}"
    except Exception as e:
        _tool_audit_logger.log("shell", "", (time.time() - start_time) * 1000, False, str(e))
        # SECURITY: Do not leak internal exception details to the caller.
        logger.error(f"Shell error (internal): {e}", exc_info=True)
        return "Error: Shell execution failed. Check server logs."


async def shell_async(cmd: str, timeout: int = 30) -> str:
    """Async version of shell() (same rules; runs in a worker thread)."""
    if not await _rate_limiter.acheck("shell", max_calls=10, window=60):
        _tool_audit_logger.log("shell_async", "", 0, False, "Rate limit exceeded")
        return "Error: Rate limit exceeded for shell tool (10 calls/minute)"
    try:
        groups = parse(cmd)
    except ShellError as e:
        return str(e)
    start_time = time.time()
    try:
        result = await asyncio.wait_for(asyncio.to_thread(_execute, groups), timeout=timeout + 5)
        _tool_audit_logger.log("shell_async", "", (time.time() - start_time) * 1000, True)
        return result
    except (asyncio.TimeoutError, subprocess.TimeoutExpired):
        _tool_audit_logger.log("shell_async", "", (time.time() - start_time) * 1000, False, "Command timed out")
        return f"Error: Command timed out after {timeout} seconds"
    except FileNotFoundError as e:
        return f"Error: command not found on this computer: {e.filename or e}"
    except Exception as e:
        _tool_audit_logger.log("shell_async", "", (time.time() - start_time) * 1000, False, str(e))
        logger.error(f"Shell async error (internal): {e}", exc_info=True)
        return "Error: Shell execution failed. Check server logs."
