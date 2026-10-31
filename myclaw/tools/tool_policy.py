"""What code agents may put in their own tools (register_tool, improve_skill).

2026-10-28 (rules re-review): the old check listed *forbidden* imports, and an
allowed module could still hand out ``os`` (``logging.os``, ``random._os``),
``operator.attrgetter('__glo' + 'bals__')`` read anything, and ``o = open``
aliased the blocked builtin. Now:

* only the modules in :data:`ALLOWED_MODULES` can be imported;
* attribute names that lead to the operating system or around the checks
  (:data:`DENIED_ATTRS`, e.g. ``.os``, ``.system``, ``.attrgetter``,
  ``.create_subprocess_shell``, ``.open``) are refused on any object;
* ``eval``/``exec``/``open``/``compile``/``globals``/``vars``/… cannot be used at
  all, and ``getattr`` only with a fixed, harmless attribute name;
* dunder names are refused except a small list of everyday ones
  (``__name__``, ``__init__``, ``if __name__ == "__main__":``,
  ``x.__class__.__name__``, reading ``__dict__`` / ``__file__`` / ``__version__``).

This is the first line. The second is that agent-made tools run in a separate,
locked-down process (``tool_sandbox``): even code that slipped past this check
cannot write outside the workspace, read ZenSynora's data, start programs or
connect to this computer or the local network.
"""

from __future__ import annotations

import ast
from typing import Optional

ALLOWED_MODULES = frozenset({
    # standard library: data, text, time, maths
    "json", "re", "datetime", "time", "math", "random", "statistics", "collections", "collections.abc",
    "itertools", "functools", "string", "textwrap", "typing", "dataclasses", "enum", "decimal", "fractions",
    "hashlib", "base64", "uuid", "html", "html.parser", "csv", "io", "urllib.parse", "xml.etree.ElementTree",
    "zoneinfo", "logging", "asyncio", "copy", "pprint", "heapq", "bisect", "unicodedata", "difflib",
    "calendar", "secrets", "struct", "binascii", "tempfile", "operator", "ipaddress", "numbers", "abc",
    "contextlib", "email.utils", "locale", "array", "colorsys", "gzip", "zlib",
    # web clients (their sub-modules too)
    "requests", "httpx", "bs4",
})
_PACKAGES = ("requests.", "httpx.", "bs4.")

# Attribute names refused on ANY object (allowed modules re-export these).
DENIED_ATTRS = frozenset({
    "os", "_os", "sys", "_sys", "subprocess", "shutil", "socket", "builtins", "importlib", "ctypes", "signal",
    "threading", "multiprocessing", "codecs", "posix", "nt", "pty", "platform",
    "system", "popen", "spawnl", "spawnle", "spawnlp", "spawnv", "spawnve", "spawnvp", "posix_spawn",
    "posix_spawnp", "execl", "execle", "execlp", "execlpe", "execv", "execve", "execvp", "execvpe", "fork",
    "forkpty", "kill", "killpg", "startfile",
    "create_subprocess_shell", "create_subprocess_exec", "subprocess_shell", "subprocess_exec",
    "attrgetter", "methodcaller", "open", "modules", "loader",
    "f_globals", "f_locals", "f_back", "f_builtins", "f_code", "gi_frame", "gi_code", "cr_frame", "cr_code",
    "ag_frame", "tb_frame", "tb_next", "co_code", "func_globals",
})

# Names that cannot be used at all (also not assigned to another name).
FORBIDDEN_NAMES = frozenset({
    "eval", "exec", "compile", "__import__", "globals", "locals", "vars", "breakpoint", "open", "input",
    "setattr", "delattr", "__build_class__", "memoryview", "help", "exit", "quit",
})

# Strings that name a way out (``x.__dict__['os']``, ``getattr(m, 'system')``).
DENIED_STRINGS = frozenset({"os", "sys", "subprocess", "builtins", "ctypes", "importlib", "socket", "shutil",
                            "posix", "nt", "system", "popen"})

ALLOWED_DUNDERS = frozenset({
    "__name__", "__doc__", "__init__", "__post_init__", "__call__", "__len__", "__iter__", "__next__",
    "__aiter__", "__anext__", "__enter__", "__exit__", "__aenter__", "__aexit__", "__str__", "__repr__",
    "__eq__", "__ne__", "__lt__", "__le__", "__gt__", "__ge__", "__hash__", "__contains__", "__getitem__",
    "__setitem__", "__bool__", "__version__", "__file__", "__main__", "__all__", "__slots__",
})
_READ_ONLY_DUNDERS = frozenset({"__dict__"})  # reading is fine, assigning is not


def _module_allowed(name: str) -> bool:
    return name in ALLOWED_MODULES or name.startswith(_PACKAGES)


def _dunder(name: str) -> bool:
    return len(name) > 4 and name.startswith("__") and name.endswith("__")


def validate_tool_code(code: str) -> Optional[str]:
    """None when the code may be used as a tool, else an 'Error: …' that says what to change."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return f"Syntax error in tool code: {e}"
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    allowed = ", ".join(sorted(m for m in ALLOWED_MODULES if "." not in m))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_allowed(alias.name):
                    return (f"Error: Importing '{alias.name}' is forbidden for security reasons. Tools may "
                            f"import: {allowed}.")
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return "Error: Relative imports are forbidden in tools (they would reach ZenSynora's own code)."
            mod = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    return "Error: 'from … import *' is not allowed in tools; import the names you need."
                full = f"{mod}.{alias.name}"
                if not (_module_allowed(mod) or _module_allowed(full)):
                    return (f"Error: Importing from '{mod}' is forbidden for security reasons. Tools may "
                            f"import: {allowed}.")
                if alias.name in DENIED_ATTRS or _dunder(alias.name) and alias.name not in ALLOWED_DUNDERS:
                    return f"Error: Importing '{alias.name}' from '{mod}' is forbidden for security reasons."
        elif isinstance(node, ast.Attribute):
            attr = node.attr
            if attr in DENIED_ATTRS:
                return f"Error: Accessing '.{attr}' is forbidden in tools (it leads to the operating system)."
            if _dunder(attr) and attr not in ALLOWED_DUNDERS:
                parent = parents.get(id(node))
                if attr == "__class__" and isinstance(parent, ast.Attribute) and parent.attr == "__name__":
                    continue  # type(x).__name__ style: x.__class__.__name__
                if attr in _READ_ONLY_DUNDERS and isinstance(node.ctx, ast.Load):
                    continue
                return f"Error: Accessing '{attr}' is forbidden for security reasons."
        elif isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES:
                return f"Error: Using '{node.id}' is forbidden for security reasons."
            if node.id == "getattr":
                call = parents.get(id(node))
                if not (isinstance(call, ast.Call) and call.func is node):
                    return "Error: 'getattr' can only be called directly, with a fixed attribute name."
                if len(call.args) < 2 or not isinstance(call.args[1], ast.Constant) \
                        or not isinstance(call.args[1].value, str):
                    return "Error: getattr() needs a fixed attribute name in quotes, e.g. getattr(obj, 'name', None)."
                name = call.args[1].value
                if name in DENIED_ATTRS or (_dunder(name) and name not in ALLOWED_DUNDERS):
                    return f"Error: getattr(…, '{name}') is forbidden for security reasons."
            elif _dunder(node.id) and node.id not in ALLOWED_DUNDERS:
                return f"Error: Using '{node.id}' is forbidden for security reasons."
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            text = node.value.strip()
            if _dunder(text) and text not in ALLOWED_DUNDERS:
                return "Error: Dunder names in strings are forbidden for security reasons."
            if text in DENIED_STRINGS:
                return f"Error: The string '{text}' is not allowed in tools (it names a way to the operating system)."
    return None
