"""Tool-execution phase of Agent.think.

Owns: parallel vs sequential dispatch decision, per-tool error handling,
KB-gap recording for empty knowledge searches, fire-and-forget KB
auto-extraction for substantial tool outputs, the followup LLM call
(post tool execution), empty-response recovery.

Returns the final assistant string sent back to the user.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import re
import time
from typing import Dict, TYPE_CHECKING

from ..events import emit, short

if TYPE_CHECKING:
    from ..agent import Agent
    from ..memory import Memory

logger = logging.getLogger(__name__)

# Follow-up rounds after the first tool call (v0.7.2 text calls, v0.7.3 also native).
# 2026-10-21: chat uses agents.max_tool_rounds (default 12; it was a fixed 4).
MAX_TEXT_ROUNDS = 12
# v0.7.5: jobs set their own limit (job setting "max tool steps"); 0 = use the config.
from contextvars import ContextVar as _ContextVar  # noqa: E402

MAX_ROUNDS: "_ContextVar[int]" = _ContextVar("max_tool_rounds", default=0)


def max_rounds() -> int:
    """Tool rounds allowed for this message: the job's setting, else agents.max_tool_rounds."""
    if MAX_ROUNDS.get() > 0:
        return MAX_ROUNDS.get()
    try:
        from ..config import load_config

        return int(getattr(load_config().agents, "max_tool_rounds", MAX_TEXT_ROUNDS) or MAX_TEXT_ROUNDS)
    except Exception:
        return MAX_TEXT_ROUNDS


TOOL_LIMIT_MARK = "I needed more tool steps than this job allows. What I found so far:"
WRAP_UP = ("You have used all {n} tool steps allowed for this message. Do NOT call any more tools. "
           "Answer now with what you found so far. If the task is not finished, say plainly what is "
           "still missing and that the user can reply \"continue\" to go on.")


async def _wrap_up(agent: "Agent", followup: list, used: list) -> str:
    """At the tool-step limit: let the model answer from the results it already has.

    2026-10-21: the run used to end with a fixed "I needed more tool steps"
    message and everything the tools returned was thrown away.
    """
    from .text_tools import parse_text_tool_calls

    n = max_rounds() + 1
    try:
        text, calls = await agent._provider_chat(
            followup + [{"role": "user", "content": WRAP_UP.format(n=n)}], agent.model)
        if text and text.strip() and not parse_text_tool_calls(text):
            return text.strip()
    except Exception as e:
        logger.warning("Wrap-up after the tool-step limit failed: %s", e)
    names = ", ".join(dict.fromkeys(used)) or "tools"
    return (f"I used all {n} tool steps for this message ({names}) before finishing. "
            "Reply \"continue\" and I will go on from there, or ask for a smaller part. "
            "(The limit is agents.max_tool_rounds — Settings → Features.)")


def _usage_hint(tool_name: str, args: Dict) -> str:
    """'browse needs url (required) …' for a call with wrong or missing arguments."""
    try:
        from ..provider import _get_tool_schemas

        schema = next(s["function"]["parameters"] for s in _get_tool_schemas()
                      if s["function"]["name"] == tool_name)
    except Exception:
        return ""
    req = set(schema.get("required", []))
    params = ", ".join(f"{k} ({v.get('type', 'any')}{', required' if k in req else ''})"
                       for k, v in schema.get("properties", {}).items())
    sent = ", ".join(_public_args(args)) or "no arguments"
    return (f"\nHow to call {tool_name}: arguments {params or 'none'}. You sent {sent}. "
            f"Call {tool_name} again with the required arguments filled in.")


def _public_args(args: Dict) -> Dict:
    """Tool arguments without internal keys (``_depth``…) for display."""
    return {k: v for k, v in (args or {}).items() if not str(k).startswith("_")}


def _emit_tool(agent, name: str, args: Dict, ok: bool, output: str, duration) -> None:
    """Activity event for the Web UI chat (v0.7.1); no-op outside a chat channel."""
    emit({
        "type": "tool_end", "tool": name, "ok": bool(ok),
        "args": short(_public_args(args), 200), "result": short(output, 400),
        "ms": int(duration * 1000) if duration is not None else None,
    })


async def execute_tools(
    agent: "Agent",
    tool_calls: list,
    messages: list,
    user_message: str,
    user_id: str,
    mem: "Memory",
    _depth: int,
    had_kb_results: bool,
    _round: int = 0,
) -> str:
    """Execute tool calls (parallel + sequential) and return final response.

    ``_round`` counts follow-up rounds for tool calls written as text
    (v0.7.2): a model that answers a tool result with another text tool call
    gets up to ``MAX_TEXT_ROUNDS`` more rounds.
    """
    from ..tools import (
        TOOLS,
        trigger_hook,
        get_parallel_executor,
        is_tool_independent,
    )

    # Save assistant turn placeholder before tool messages get appended,
    # mirroring the OpenAI message-ordering contract.
    await mem.add("assistant", "")

    if agent._current_task_id:
        await agent._task_timer.update_step(
            agent._current_task_id, "executing_tools", 4, 5
        )

    # Per-tool result map keyed by tool_call_id (preserved for the followup
    # message ordering required by OpenAI compatibility).
    tool_results_by_id: Dict[str, str] = {}

    # Independent tools can run concurrently; dependent ones run in order
    # because their inputs may reference earlier outputs.
    independent_tools = [
        tc for tc in tool_calls
        if is_tool_independent(tc.get("function", {}).get("name", ""))
    ]
    dependent_tools = [
        tc for tc in tool_calls
        if not is_tool_independent(tc.get("function", {}).get("name", ""))
    ]

    if len(independent_tools) > 1:
        # Parallel path.
        logger.info(f"Executing {len(independent_tools)} tools in parallel")
        executor = get_parallel_executor()
        exec_results = await executor.execute_tools(independent_tools, user_id)

        for tc, r in zip(independent_tools, exec_results):
            tool_call_id = tc.get("id", "call_default")
            if r["success"]:
                tool_output = r["result"]
                if r["tool_name"] == "browse" and agent._detect_browse_failure(tool_output):
                    url_match = re.search(r"https?://\S+", tool_output)
                    url = url_match.group(0) if url_match else ""
                    tool_output += agent._browse_alternative_hint(url, user_message)
                content = f"Tool {r['tool_name']} returned: {tool_output}"
            else:
                content = f"Tool {r['tool_name']} error: {r['error']}"
            tool_results_by_id[tool_call_id] = content
            await mem.add("tool", content)
            _emit_tool(agent, r["tool_name"], tc.get("function", {}).get("arguments", {}),
                       r["success"], r["result"] if r["success"] else r["error"], None)

            # KB extraction for substantial parallel tool results
            # (fire-and-forget; never blocks the response path).
            if (
                r["success"]
                and agent._kb_auto_extract
                and agent._should_save_tool_result(r["tool_name"], r.get("result", ""))
            ):
                _t = asyncio.create_task(
                    agent._save_tool_result_to_kb(
                        r["tool_name"],
                        tc.get("function", {}).get("arguments", {}),
                        r["result"],
                        user_message,
                        user_id,
                    )
                )
                agent._track_preload(_t)
    elif independent_tools:
        # Single independent tool: run it through the sequential path
        # alongside any dependent tools — there's no parallelism win.
        dependent_tools = tool_calls

    # Sequential path (always runs for dependent tools).
    for tc in dependent_tools:
        tool_name = tc.get("function", {}).get("name", "")
        args = tc.get("function", {}).get("arguments", {})
        tool_call_id = tc.get("id", "call_default")

        if tool_name not in TOOLS:
            content = f"Unknown tool: {tool_name}"
            tool_results_by_id[tool_call_id] = content
            await mem.add("tool", content)
            logger.warning(f"Unknown tool called: {tool_name}")
            continue

        # Delegation calls track their own depth to defeat infinite loops.
        if tool_name == "delegate":
            args["_depth"] = _depth + 1

        start_time = time.time()
        logger.info(f"[AUDIT] Tool execution started: {tool_name} with args: {args}")
        if tool_name != "delegate":  # delegation emits its own, richer events
            emit({"type": "tool_start", "tool": tool_name, "args": short(_public_args(args), 200)})

        try:
            func = TOOLS[tool_name]["func"]
            # 2026-10-29: arguments the tool doesn't take are dropped (with a
            # note for the model); a missing required argument is answered
            # with how to call the tool — neither is an error in ZenSynora.
            from ..tools.core import fit_tool_arguments, ignored_arguments_note, missing_arguments_message
            sent_args = args
            args, ignored_args, missing_args = fit_tool_arguments(func, args)
            if missing_args:
                content = (f"Tool {tool_name} was not run: missing required argument(s) "
                           f"{', '.join(missing_args)}." + (_usage_hint(tool_name, sent_args)
                           or " " + missing_arguments_message(tool_name, func, missing_args, sent_args)))
                logger.info("Tool %s not run: missing %s", tool_name, ", ".join(missing_args))
                tool_results_by_id[tool_call_id] = content
                await mem.add("tool", content)
                _emit_tool(agent, tool_name, sent_args, False, content, time.time() - start_time)
                continue
            if ignored_args:
                logger.info("Tool %s: ignored arguments it does not take: %s", tool_name, ", ".join(ignored_args))
            if inspect.iscoroutinefunction(func):
                result = await func(**args)
            else:
                result = await asyncio.to_thread(func, **args)
            # 2026-10-27: lazily registered tools are wrappers, so an async tool
            # (e.g. send_telegram_message) is not seen as a coroutine function and
            # returned an un-awaited coroutine: nothing was sent and the agent got
            # "<coroutine object …>" as the result.
            if inspect.isawaitable(result):
                result = await result

            tool_output = str(result) + ignored_arguments_note(tool_name, func, ignored_args)

            # Browse-specific: detect failure and append an alternative hint.
            if tool_name == "browse" and agent._detect_browse_failure(tool_output):
                url = args.get("url", "")
                tool_output += agent._browse_alternative_hint(url, user_message)
                logger.info(f"Browse failure detected for {url}; alternative hint appended.")

            # KB-search-specific: empty results are tracked as gaps so the
            # next request can hint the model to write_to_knowledge().
            if tool_name == "search_knowledge":
                if "No results found" in tool_output or "Error" in tool_output:
                    query = args.get("query", user_message[:60])
                    agent._record_kb_gap(user_id, query)
                    tool_output += (
                        f"\n\n[Tip: No knowledge base entries matched '{query}'. "
                        "Use write_to_knowledge() to persist useful information for future use.]"
                    )

            content = f"Tool {tool_name} returned: {tool_output}"
            tool_results_by_id[tool_call_id] = content
            await mem.add("tool", content)
            duration = time.time() - start_time
            logger.info(
                f"[AUDIT] Tool executed successfully: {tool_name} (took {duration:.2f}s)"
            )
            if tool_name != "delegate":
                _emit_tool(agent, tool_name, args, True, tool_output, duration)

            # Fire-and-forget KB extraction for substantial outputs.
            if agent._kb_auto_extract and agent._should_save_tool_result(tool_name, tool_output):
                _t = asyncio.create_task(
                    agent._save_tool_result_to_kb(
                        tool_name, args, tool_output, user_message, user_id
                    )
                )
                agent._track_preload(_t)
        except Exception as e:
            logger.error(f"Tool execution error ({tool_name}): {e}")
            logger.error(f"[AUDIT] Tool execution failed: {tool_name} - {e}")
            content = f"Tool error: {e}"
            if isinstance(e, TypeError) and "argument" in str(e):
                # v0.7.3: tell the model exactly how to call the tool, so the
                # next round can retry instead of giving up.
                content += _usage_hint(tool_name, args)
            tool_results_by_id[tool_call_id] = content
            await mem.add("tool", content)
            _emit_tool(agent, tool_name, args, False, str(e), time.time() - start_time)

    # Build the followup turn for OpenAI compatibility: an assistant
    # message with all tool_calls, followed by one tool message per id
    # in the same order as the original tool_calls.
    openai_tool_calls = [
        {
            "id": tc.get("id", "call_default"),
            "type": tc.get("type", "function"),
            "function": {
                "name": tc["function"]["name"],
                "arguments": tc["function"].get("arguments_str", ""),
            },
        }
        for tc in tool_calls
    ]

    if all(tc.get("text_call") for tc in tool_calls):
        # v0.7.2: the model wrote the call as text (see text_tools.py). Answer
        # in plain messages — models without native tool support may reject
        # "tool" role messages.
        results = "\n\n".join(
            f"Result of {tc['function']['name']}:\n{tool_results_by_id.get(tc.get('id'), 'Tool was not executed.')}"
            for tc in tool_calls
        )
        followup = messages + [
            {"role": "assistant", "content": tool_calls[0].get("raw_text", "")},
            {"role": "user", "content": (
                f"{results}\n\nUse these results to answer my request. Reply in normal text; "
                "only if you really need another tool, reply with the tool JSON again.")},
        ]
    else:
        followup = messages + [
            {"role": "assistant", "content": "", "tool_calls": openai_tool_calls}
        ]
        for tc in tool_calls:
            tool_call_id = tc.get("id", "call_default")
            content = tool_results_by_id.get(tool_call_id, "Tool was not executed.")
            followup.append({"role": "tool", "tool_call_id": tool_call_id, "content": content})

    # Hooks may rewrite the followup before the second LLM call.
    hook_results = trigger_hook("pre_llm_call", followup, agent.model)
    for result in hook_results:
        if result and isinstance(result, list):
            followup = result

    try:
        final_response, native_calls = await agent._provider_chat(followup, agent.model)

        trigger_hook("post_llm_call", final_response, native_calls)

        # v0.7.3: the model may need another tool after seeing a result (search,
        # then read; retry a call with corrected arguments …). Native tool calls
        # in the follow-up used to be thrown away, the reply looked empty and
        # the run ended with "returned an empty answer" — no multi-step task
        # (most jobs) could finish. Both native and text tool calls now get
        # up to MAX_TEXT_ROUNDS more rounds.
        from .text_tools import parse_text_tool_calls

        more = native_calls or parse_text_tool_calls(final_response)
        if more and _round < max_rounds():
            return await execute_tools(agent, more, followup, user_message, user_id, mem,
                                       _depth, had_kb_results, _round=_round + 1)
        if more:
            used = [tc.get("function", {}).get("name", "") for m in followup
                    for tc in (m.get("tool_calls") or []) if m.get("role") == "assistant"]
            final_response = await _wrap_up(agent, followup, used)
            if MAX_ROUNDS.get() > 0:
                # A job: the answer is partial — mark it so the run counts as
                # failed and the medic suggests more "max tool steps".
                final_response = f"{TOOL_LIMIT_MARK}\n\n{final_response}"

        # Empty-response recovery after tool-use followup.
        if agent._is_empty_response(final_response):
            final_response = await agent._recover_empty_response(
                followup, user_message, user_id, had_kb_results
            )

        await mem.add("assistant", final_response)
        return final_response
    except Exception as e:
        logger.error(f"LLM second call error: {e}")
        from ..llm_errors import describe_llm_error

        return f"[Error] The tool ran, but the model could not continue: {describe_llm_error(e)}"
