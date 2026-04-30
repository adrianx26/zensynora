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

if TYPE_CHECKING:
    from ..agent import Agent
    from ..memory import Memory

logger = logging.getLogger(__name__)


async def execute_tools(
    agent: "Agent",
    tool_calls: list,
    messages: list,
    user_message: str,
    user_id: str,
    mem: "Memory",
    _depth: int,
    had_kb_results: bool,
) -> str:
    """Execute tool calls (parallel + sequential) and return final response."""
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

        try:
            func = TOOLS[tool_name]["func"]
            if inspect.iscoroutinefunction(func):
                result = await func(**args)
            else:
                result = await asyncio.to_thread(func, **args)

            tool_output = str(result)

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
            tool_results_by_id[tool_call_id] = content
            await mem.add("tool", content)

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
        final_response, _ = await agent._provider_chat(followup, agent.model)

        trigger_hook("post_llm_call", final_response, None)

        # Empty-response recovery after tool-use followup.
        if agent._is_empty_response(final_response):
            final_response = await agent._recover_empty_response(
                followup, user_message, user_id, had_kb_results
            )

        await mem.add("assistant", final_response)
        return final_response
    except Exception as e:
        logger.error(f"LLM second call error: {e}")
        return f"Tool executed but error getting response: {e}"
