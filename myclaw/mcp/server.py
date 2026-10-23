"""ZenSynora's own tools as an MCP server (``zensynora mcp-server``, stdio).

2026-10-23 (KB phase 0): every parameter used to be declared as a string, so
MCP clients sent ``limit="5"`` to ``search_knowledge`` and the tool failed when
slicing with it. The input schema is now built from the type annotations
(integer, number, boolean, array, object, string), defaults are included, and
incoming arguments are converted to the annotated type before the call.
``user_id`` (and other internal parameters) are not offered to clients: the
knowledge tools pick their knowledge base themselves
(``myclaw.knowledge.scope.for_tool`` — outside an agent turn that is the
shared ``default`` base).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import types
import typing
from typing import Any, Callable, Dict, List, Mapping, Optional

from mcp.server import Server
from mcp.types import TextContent, Tool

import myclaw.tools as m_tools

logger = logging.getLogger(__name__)

# 2026-10-23 (KB phase 0): parameters MCP clients never see (and cannot set).
# ``user_id``: the tools choose the knowledge base / owner themselves; a client
# could otherwise pick another (empty) knowledge base with a made-up id.
HIDDEN_PARAMS = frozenset({"user_id", "_depth", "context", "self"})

_TRUE = {"true", "1", "yes", "y", "on"}
_FALSE = {"false", "0", "no", "n", "off", ""}


class ToolArgumentError(ValueError):
    """An argument from the MCP client cannot be converted to the parameter's type."""


def _target(func: Any) -> Any:
    """The real function behind registry wrappers (lazy loaders, decorators)."""
    loader = getattr(func, "_load", None)
    if callable(loader) and not inspect.isfunction(func):
        try:
            func = loader()
        except Exception:  # pragma: no cover - a broken lazy module
            return func
    try:
        return inspect.unwrap(func)
    except Exception:  # pragma: no cover
        return func


def _type_hints(func: Any) -> Dict[str, Any]:
    """Resolved annotations (also for ``from __future__ import annotations``)."""
    target = _target(func)
    try:
        return typing.get_type_hints(target)
    except Exception:
        pass
    # Fallback: raw annotations; string annotations are resolved one by one
    # against the function's module, unknown names stay strings.
    raw = dict(getattr(target, "__annotations__", {}) or {})
    ns = dict(getattr(target, "__globals__", {}) or {})
    ns.update({k: getattr(typing, k) for k in ("List", "Dict", "Optional", "Union", "Any")})
    out: Dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, str):
            try:
                v = eval(v, ns)  # noqa: S307 - annotation text from our own code
            except Exception:
                pass
        out[k] = v
    return out


def _strip_optional(annotation: Any) -> Any:
    """``Optional[int]`` / ``int | None`` → ``int``; other unions stay as they are."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union or (hasattr(types, "UnionType") and origin is types.UnionType):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _kind(annotation: Any) -> str:
    """JSON schema type name for a Python annotation."""
    a = _strip_optional(annotation)
    if isinstance(a, str):  # unresolved string annotation
        s = a.replace(" ", "").replace("typing.", "")
        if s.lower().startswith("optional[") and s.endswith("]"):
            s = s[len("optional["):-1]
        parts = [x for x in s.split("|") if x.lower() != "none"] or ["str"]
        name = parts[0].split("[")[0].lower()
        return {"int": "integer", "float": "number", "bool": "boolean", "list": "array",
                "tuple": "array", "set": "array", "dict": "object"}.get(name, "string")
    origin = typing.get_origin(a) or a
    if origin is bool:
        return "boolean"
    if origin is int:
        return "integer"
    if origin is float:
        return "number"
    if origin in (list, tuple, set, frozenset):
        return "array"
    if origin is dict:
        return "object"
    return "string"


def _json_default(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return None


def _visible_params(func: Any) -> List[inspect.Parameter]:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return []
    return [
        p for name, p in sig.parameters.items()
        if name not in HIDDEN_PARAMS
        and p.kind not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]


# 2026-10-23 (KB phase 0): typed schema instead of "string" for everything.
def build_input_schema(func: Callable[..., Any]) -> Dict[str, Any]:
    """JSON schema of a tool's parameters, typed from its annotations."""
    hints = _type_hints(func)
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for p in _visible_params(func):
        ann = hints.get(p.name, p.annotation)
        if ann is inspect.Parameter.empty:
            ann = type(p.default) if p.default not in (inspect.Parameter.empty, None) else str
        prop: Dict[str, Any] = {"type": _kind(ann)}
        if p.default is inspect.Parameter.empty:
            required.append(p.name)
        elif p.default is not None and _json_default(p.default) is not None:
            prop["default"] = p.default
        properties[p.name] = prop
    return {"type": "object", "properties": properties, "required": required}


def _coerce(name: str, value: Any, kind: str) -> Any:
    if value is None:
        return None
    try:
        if kind == "boolean":
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)) and value in (0, 1):
                return bool(value)
            s = str(value).strip().lower()
            if s in _TRUE:
                return True
            if s in _FALSE:
                return False
            raise ValueError(value)
        if kind == "integer":
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int):
                return value
            if isinstance(value, float):
                if value.is_integer():
                    return int(value)
                raise ValueError(value)
            s = str(value).strip()
            try:
                return int(s)
            except ValueError:
                f = float(s)
                if f.is_integer():
                    return int(f)
                raise
        if kind == "number":
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return value
            return float(str(value).strip())
        if kind == "array":
            if isinstance(value, (list, tuple, set)):
                return list(value)
            s = str(value).strip()
            if s.startswith("["):
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return parsed
            return [x.strip() for x in s.split(",") if x.strip()]
        if kind == "object":
            if isinstance(value, dict):
                return value
            parsed = json.loads(str(value))
            if isinstance(parsed, dict):
                return parsed
            raise ValueError(value)
        # string
        if isinstance(value, str):
            return value
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)
    except (TypeError, ValueError) as e:
        word = {"boolean": "true or false", "integer": "a whole number", "number": "a number",
                "array": "a list (JSON array or comma-separated)", "object": "a JSON object"}.get(kind, kind)
        raise ToolArgumentError(f"Parameter '{name}' must be {word}, got {value!r}.") from e


# 2026-10-23 (KB phase 0): MCP clients send "5" / "true"; convert before the call.
def coerce_arguments(func: Callable[..., Any], arguments: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """Convert the client's arguments to the types the tool's annotations ask for.

    Hidden parameters (``user_id``…) are dropped; arguments the tool does not
    know are passed on unchanged (the tool may take ``**kwargs``).
    """
    hints = _type_hints(func)
    params = {p.name: p for p in _visible_params(func)}
    out: Dict[str, Any] = {}
    for key, value in dict(arguments or {}).items():
        if key in HIDDEN_PARAMS:
            continue
        p = params.get(key)
        if p is None:
            out[key] = value
            continue
        ann = hints.get(key, p.annotation)
        if ann is inspect.Parameter.empty:
            if p.default in (inspect.Parameter.empty, None):
                out[key] = value
                continue
            ann = type(p.default)
        out[key] = _coerce(key, value, _kind(ann))
    return out


def list_tool_definitions() -> List[Tool]:
    """The MCP tool list for every tool in ZenSynora's registry."""
    tools_list = []
    for name, data in m_tools.TOOLS.items():
        desc = data.get("desc", "No description")
        if isinstance(data.get("parameters"), dict):  # tools from other MCP servers bring their own schema
            input_schema = data["parameters"]
        else:
            input_schema = build_input_schema(data["func"])
        tools_list.append(Tool(name=name, description=desc, inputSchema=input_schema))
    return tools_list


async def call_registered_tool(name: str, arguments: Optional[Mapping[str, Any]]) -> List[TextContent]:
    """Run a registry tool for an MCP client and return its result as text."""
    if name not in m_tools.TOOLS:
        raise ValueError(f"Tool {name} not found in ZenSynora registry.")

    data = m_tools.TOOLS[name]
    func = data["func"]
    try:
        if isinstance(data.get("parameters"), dict):
            kwargs = dict(arguments or {})
        else:
            kwargs = coerce_arguments(func, arguments)
    except ToolArgumentError as e:
        # Bad input from the client: say what is wrong (INFO, not an error of ours).
        logger.info("MCP server: bad arguments for '%s': %s", name, e)
        return [TextContent(type="text", text=f"Invalid arguments: {e}")]

    try:
        inspect.signature(func).bind(**kwargs)
    except TypeError as e:  # missing or unknown parameter
        logger.info("MCP server: '%s' called with wrong arguments: %s", name, e)
        return [TextContent(type="text", text=(
            f"Invalid arguments for '{name}': {e}. Check the tool's input schema for the "
            "parameter names."))]
    except ValueError:  # no signature available: let the call decide
        pass

    try:
        if inspect.iscoroutinefunction(func):
            result = await func(**kwargs)
        else:
            result = await asyncio.to_thread(func, **kwargs)
        if inspect.isawaitable(result):  # lazily loaded async tools
            result = await result
        return [TextContent(type="text", text=str(result))]
    except Exception as e:
        # SECURITY FIX: Do not leak internal exception details to MCP clients.
        logger.error(f"MCP server execution error for '{name}': {e}", exc_info=True)
        return [
            TextContent(
                type="text", text="Execution Error: Tool execution failed. Check server logs."
            )
        ]


def _logs_to_stderr() -> None:
    """2026-10-23 (KB phase 0): stdout carries the MCP protocol; log lines printed
    there (e.g. "Knowledge DB connection closed") broke the client's JSON-RPC
    parsing. Every console log handler on stdout is moved to stderr."""
    import sys

    loggers: List[Any] = [logging.getLogger()]
    loggers += [lg for lg in logging.Logger.manager.loggerDict.values() if isinstance(lg, logging.Logger)]
    for lg in loggers:
        for h in lg.handlers:
            if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) in (
                    sys.stdout, sys.__stdout__):
                h.setStream(sys.stderr)


async def start_mcp_server():
    """Starts the MCP server over stdio, exposing MyClaw's native tools."""
    _logs_to_stderr()
    server = Server("zensynora")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return list_tool_definitions()

    # 2026-10-23 (KB phase 0): newer MCP SDKs validate the arguments against the
    # (now typed) schema and reject "5" for an integer before we can convert it;
    # call_registered_tool converts and reports bad values itself.
    try:
        register_call = server.call_tool(validate_input=False)
    except TypeError:  # older mcp versions: no validation, no keyword
        register_call = server.call_tool()

    @register_call
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        return await call_registered_tool(name, arguments)

    # Run the server loop indefinitely over stdin/stdout
    from mcp.server.stdio import stdio_server

    logger.info("Initializing MCP Stdio transport layer...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
