"""Tool calls written as text (added in v0.7.2).

ZenSynora's system prompt tells the model it can call a tool by answering
with JSON — ``{"tool": "<name>", "args": {...}}`` — and providers also send
the tools as native function definitions. Many models use the native way,
but others (small or free models, e.g. ``cohere/north-mini-code`` on
OpenRouter) follow the prompt literally and *write* the JSON. Until 0.7.2
nothing read that text: the user saw ``{"tool": "search_knowledge", …}``
as the answer and no tool ran.

``parse_text_tool_calls`` recognises such an answer and turns it into the
same structure as a native tool call, marked ``text_call=True`` so the
executor can send the result back in plain messages (no ``tool`` role,
which models without native tool support may reject).
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, List, Optional

_FENCE = re.compile(r"^```(?:json|JSON)?\s*(.*?)\s*```$", re.DOTALL)
_NAME_KEYS = ("tool", "name", "tool_name", "function")
_ARG_KEYS = ("args", "arguments", "parameters", "params", "input")
MAX_TEXT_CALLS = 5


def _known_tools() -> Iterable[str]:
    from myclaw.tools import TOOLS

    return TOOLS.keys()


def _one(obj: Any, known: set) -> Optional[Dict[str, Any]]:
    if not isinstance(obj, dict):
        return None
    name = next((obj[k] for k in _NAME_KEYS if isinstance(obj.get(k), str)), None)
    if isinstance(obj.get("function"), dict):  # {"function": {"name":…, "arguments":…}}
        return _one(obj["function"], known)
    if not name or name not in known:
        return None
    args = next((obj[k] for k in _ARG_KEYS if k in obj), {})
    if isinstance(args, str):
        try:
            args = json.loads(args) if args.strip() else {}
        except ValueError:
            return None
    if not isinstance(args, dict):
        return None
    return {"name": name, "arguments": args}


def parse_text_tool_calls(text: Optional[str], known: Optional[Iterable[str]] = None) -> Optional[List[Dict]]:
    """Tool calls in *text* if the whole answer is one (or a list of them), else None.

    Only an answer that consists of the JSON (optionally in a ```json fence)
    counts, and only names of real tools — normal answers that merely
    *contain* JSON are left alone.
    """
    if not text:
        return None
    body = text.strip()
    if not body or body[0] not in "{[`":
        return None
    m = _FENCE.match(body)
    if m:
        body = m.group(1).strip()
    try:
        data = json.loads(body)
    except ValueError:
        return None
    names = set(known if known is not None else _known_tools())
    items = data if isinstance(data, list) else [data]
    calls = [_one(x, names) for x in items[:MAX_TEXT_CALLS]]
    if not calls or any(c is None for c in calls):
        return None
    return [
        {
            "id": f"text_call_{i}",
            "type": "function",
            "text_call": True,
            "raw_text": text.strip(),
            "function": {
                "name": c["name"],
                "arguments": c["arguments"],
                "arguments_str": json.dumps(c["arguments"]),
            },
        }
        for i, c in enumerate(calls)
    ]


def looks_like_text_tool_call(prefix: str) -> bool:
    """While streaming: could this beginning still become a text tool call?"""
    head = prefix.lstrip()
    return head.startswith("{") or head.startswith("[{") or head.startswith("```")
