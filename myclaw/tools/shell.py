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
from pathlib import Path
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
            if _core.refused_command(name):  # 2026-10-28: even when listed in the settings
                raise ShellError(f"Error: '{name}' is not allowed: interpreters and shells could run anything. "
                                 "Use the dedicated tools instead.")
            if name not in allowed:
                raise ShellError(f"Error: '{name}' not allowed. Allowed: {', '.join(sorted(allowed))}")
            bad = FORBIDDEN_ARGS.get(name, set()) & set(args[1:])
            if bad:
                raise ShellError(f"Error: '{name} {sorted(bad)[0]}' is not allowed.")
            check_arguments(name, args)
    return groups


# ── 2026-10-27 (rules review): arguments, not only command names ─────────────
# An allowed command could still write anywhere (``sort -o``, ``git
# --work-tree=…``, ``curl -o``) or reach ZenSynora's own API on localhost
# (``curl -X POST http://127.0.0.1:…/approve``). Every argument that can be a
# path is checked: commands that can write must stay in the workspace, and no
# command may read the files that hold secrets or decide what agents may do
# (settings, keys, the rules' manifest and proposals, backups). curl/wget may
# only fetch public web addresses.
# 2026-10-28 (re-review): reading the rest of ~/.myclaw (logs!) is fine again,
# git -C works inside the workspace, git config can read any key and set
# user.name / user.email for the repository.
WRITERS = {"git", "uniq", "curl", "wget"}
_RECURSIVE = {"grep": {"-r", "-R", "--recursive", "--dereference-recursive", "-d", "--directories"}}
_GIT_FORBIDDEN = {"-c", "--git-dir", "--work-tree", "--namespace", "--exec-path", "--config-env",
                  "--super-prefix", "--list-cmds"}
_GIT_CONFIG_WRITE_OPTS = {"--add", "--replace-all", "--unset", "--unset-all", "--rename-section",
                          "--remove-section", "-e", "--edit", "--global", "--system", "--file", "-f", "--blob",
                          "--worktree", "--type", "--bool", "--int", "--path"}
_GIT_CONFIG_READ_OPTS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l", "--show-origin",
                         "--show-scope", "--name-only", "--null", "-z", "--includes", "--no-includes", "--local",
                         "--default"}
_GIT_CONFIG_SETTABLE = {"user.name", "user.email"}
_CURL_FORBIDDEN = {"-K", "--config", "-L", "--location", "--location-trusted", "--resolve", "--connect-to",
                   "--unix-socket", "--abstract-unix-socket", "-x", "--proxy", "--preproxy", "--proxy1.0",
                   "--socks4", "--socks4a", "--socks5", "--socks5-hostname", "--interface", "--dns-servers",
                   "--doh-url", "-q", "--disable"}
_WGET_FORBIDDEN = {"-e", "--execute", "--config", "-i", "--input-file", "-B", "--base", "--input-metalink",
                   "--use-askpass", "--use-server-timestamps"}
# Options of curl/wget whose value is NOT a URL (so it is checked as a path / left alone).
_CURL_VALUE_OPTS = {"-o", "--output", "--output-dir", "-d", "--data", "--data-raw", "--data-binary",
                    "--data-urlencode", "--json", "-H", "--header", "-X", "--request", "-u", "--user", "-A",
                    "--user-agent", "-e", "--referer", "-b", "--cookie", "-c", "--cookie-jar", "-F", "--form",
                    "-m", "--max-time", "--connect-timeout", "-w", "--write-out", "-T", "--upload-file",
                    "-r", "--range", "-z", "--time-cond", "--retry", "--limit-rate", "-D", "--dump-header",
                    "--max-filesize", "--trace", "--trace-ascii", "--stderr", "--cacert", "--cert", "--key"}
_WGET_VALUE_OPTS = {"-O", "--output-document", "-o", "--output-file", "-a", "--append-output", "-P",
                    "--directory-prefix", "-U", "--user-agent", "--header", "-t", "--tries", "-T", "--timeout",
                    "-w", "--wait", "--post-data", "--post-file", "--body-data", "--body-file", "--method",
                    "--user", "--password", "-l", "--level", "-A", "--accept", "-R", "--reject",
                    "--load-cookies", "--save-cookies", "-Q", "--quota", "--limit-rate"}


def _home_data() -> Path:
    return (Path.home() / ".myclaw").resolve()


def _secret_paths() -> List[Path]:
    """Files no shell command may read: they hold keys or decide what agents may do."""
    data = _home_data()
    out = [data / "config.json", data / "config.json.bak", data / ".config_key", data / ".env", data / "mfa.db",
           data / "rules" / ".manifest.json", data / "rules" / ".proposals", data / "rules" / ".trash",
           data / "sandbox", Path(__file__).resolve().parents[2] / ".env", Path.home() / ".myclaw-backups"]
    try:
        from ..backup import backup_dir

        out.append(Path(backup_dir()))
    except Exception:  # pragma: no cover - settings unreadable
        pass
    try:
        out += [p for p in data.glob("config.json*")]
    except OSError:
        pass
    return [p.resolve() for p in out]


def _resolve_arg(value: str) -> Path:
    p = Path(value).expanduser()
    return (p if p.is_absolute() else (WORKSPACE / p)).resolve()


def _path_candidates(args: List[str]) -> List[str]:
    """Every string in the arguments that a program could use as a file path."""
    out: List[str] = []
    for a in args:
        if not a:
            continue
        if a.startswith("--"):
            if "=" in a:
                out.append(a.split("=", 1)[1])
        elif a.startswith("-") and len(a) > 2:
            out.append(a[2:])  # -o/path, -C/path
        else:
            out.append(a)
            if a.startswith("@"):
                out.append(a[1:])  # curl -d @file
            if "=" in a:
                out.append(a.split("=", 1)[1])  # name=value
    return [c for c in out if c and "://" not in c]


def _inside(target: Path, folder: Path) -> bool:
    return target == folder or folder in target.parents


def _check_paths(name: str, args: List[str]) -> None:
    workspace = WORKSPACE.resolve()
    secrets = _secret_paths()
    recursive = bool(_RECURSIVE.get(name, set()) & set(args[1:])) or (
        name in _RECURSIVE and any(a.startswith("-") and not a.startswith("--") and ("r" in a or "R" in a)
                                   for a in args[1:]))
    for c in _path_candidates(args[1:]):
        try:
            target = _resolve_arg(c)
        except (OSError, RuntimeError, ValueError):
            raise ShellError(f"Error: '{c}' cannot be checked as a path; use a simple file name in the workspace.")
        in_ws = _inside(target, workspace)
        for secret in secrets:
            if _inside(target, secret) or (recursive and _inside(secret, target) and not in_ws):
                raise ShellError(f"Error: '{c}' contains secrets (ZenSynora's settings, keys, rules approvals or "
                                 "backups) and cannot be used by shell commands. Logs are in ~/.myclaw/logs.")
        if name in WRITERS and not in_ws:
            raise ShellError(f"Error: '{name}' may only use files inside the workspace (~/.myclaw/workspace); "
                             f"'{c}' is outside. Use a relative path like out/file.txt.")


def _check_url(url: str) -> None:
    from ..knowledge.ingest.sources import IngestError, check_url

    if "[" in url or "]" in url:
        raise ShellError(f"Error: '{url}' — URL patterns ([…]) are not allowed.")
    full = url if "://" in url else "http://" + url
    try:
        check_url(full)
    except IngestError as e:
        why = str(e).replace(" Only public web pages can be imported.", "").replace(
            "; only public web pages can be imported.", ".")
        raise ShellError(f"Error: {why} curl/wget may only fetch public web addresses.") from None


def _url_args(name: str, args: List[str]) -> List[str]:
    """Arguments of curl/wget that are URLs: everything that is not an option or an option's value.

    The value of an option not listed as taking a value counts as a URL, so it
    is checked too (fail closed).
    """
    value_opts = _CURL_VALUE_OPTS if name == "curl" else _WGET_VALUE_OPTS
    rest, urls, i = args[1:], [], 0
    while i < len(rest):
        a = rest[i]
        if a == "--url":
            urls.append(rest[i + 1] if i + 1 < len(rest) else "")
            i += 2
            continue
        if a.startswith("--url="):
            urls.append(a.split("=", 1)[1])
        elif a.startswith("-"):
            if a in value_opts:
                i += 1  # skip its value
        else:
            urls.append(a)
        i += 1
    return [u for u in urls if u]


def _check_git_config(rest: List[str]) -> None:
    """git config: reading is fine; writing only user.name / user.email of this repository."""
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += 2 if rest[i] == "-C" else 1  # git's own options (-C <folder> takes a value)
    if i >= len(rest) or rest[i] != "config":
        return
    tail = rest[i + 1:]
    opts = [a.split("=", 1)[0] for a in tail if a.startswith("-")]
    words = [a for a in tail if not a.startswith("-")]
    if words and words[0] in ("get", "list"):
        return  # new-style read commands
    if words and words[0] in ("set", "unset", "rename-section", "remove-section", "edit"):
        verb, words = words[0], words[1:]
    else:
        verb = "set" if len(words) >= 2 else ""
    if not verb and not (set(opts) & {"--add", "--replace-all", "--unset", "--unset-all", "--rename-section",
                                      "--remove-section", "-e", "--edit"}):
        return  # 'git config user.name', 'git config --get …', 'git config --list'
    key = words[0].lower() if words else ""
    bad_opts = set(opts) & _GIT_CONFIG_WRITE_OPTS
    if verb == "set" and key in _GIT_CONFIG_SETTABLE and not bad_opts and len(words) == 2:
        return
    raise ShellError("Error: 'git config' may change only user.name and user.email of this repository "
                     "(e.g. git config user.name \"Agent\"); reading any setting is fine.")


def check_arguments(name: str, args: List[str]) -> None:
    """Refuse arguments that write outside the workspace, touch ~/.myclaw or reach local services."""
    rest = args[1:]
    if name == "git":
        for a in rest:  # git's own options come before the subcommand
            if not a.startswith("-"):
                break
            opt = a.split("=", 1)[0]
            if opt in _GIT_FORBIDDEN or (a[:2] == "-c" and not a.startswith("--")):
                shown = opt if opt.startswith("--") else opt[:2]
                raise ShellError(f"Error: 'git {shown}' is not allowed (it changes which files or settings git "
                                 "uses). Run git inside the workspace without it; 'git -C <folder>' works for "
                                 "folders in the workspace.")
        for a in rest:
            opt = a.split("=", 1)[0]
            if opt in ("--git-dir", "--work-tree", "--separate-git-dir", "--template", "--config-env"):
                raise ShellError(f"Error: 'git … {opt}' is not allowed (it points git at other folders).")
        _check_git_config(rest)
    elif name == "sort":
        for a in rest:
            if a == "-o" or (a.startswith("--o") and "--output".startswith(a.split("=", 1)[0])) \
                    or (a.startswith("-") and not a.startswith("--") and "o" in a[1:]):
                raise ShellError("Error: 'sort -o/--output' is not allowed (it writes files). Read the output "
                                 "instead, or save it with write_file.")
    elif name in ("curl", "wget"):
        forbidden = _CURL_FORBIDDEN if name == "curl" else _WGET_FORBIDDEN
        takes_value = {o[1] for o in (_CURL_VALUE_OPTS if name == "curl" else _WGET_VALUE_OPTS)
                       if len(o) == 2}
        bad_short = {o[1] for o in forbidden if len(o) == 2}
        for a in rest:
            opt = a.split("=", 1)[0]
            in_cluster = ""
            if a.startswith("-") and not a.startswith("--") and len(a) > 2:
                for ch in a[1:]:  # -sL: flags; -o<path>: the rest is the value
                    if ch in bad_short:
                        in_cluster = f"-{ch}"
                        break
                    if ch in takes_value:
                        break
            if opt in forbidden or in_cluster:
                opt = in_cluster or opt
                if opt in ("-L", "--location", "--location-trusted"):
                    raise ShellError("Error: 'curl -L' (follow redirects) is not allowed: a redirect could lead to "
                                     "this computer. Use the browse tool to read a page, or curl the final "
                                     "address without -L.")
                raise ShellError(f"Error: '{name} {opt}' is not allowed (redirects, proxies, config files and "
                                 "name overrides could reach local services).")
        if name == "wget" and any(a.split("=", 1)[0] == "--max-redirect" for a in rest):
            raise ShellError("Error: 'wget --max-redirect' is not allowed; redirects are not followed.")
        urls = _url_args(name, args)
        if not urls:
            raise ShellError(f"Error: give {name} a public web address (https://…).")
        for u in urls:
            _check_url(u)
        _check_paths(name, args)
        if name == "wget":
            args.insert(1, "--max-redirect=0")  # a redirect could lead to a local address
        return
    _check_paths(name, args)


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
