"""
Tools — TOOLBOX Skill Management
"""

import asyncio
from ..async_utils import run_async
import logging
from typing import Any, Dict, List, Optional

from .core import (
    WORKSPACE,
    TOOLBOX_DIR,
    TOOLBOX_REG,
    TOOLBOX_DOCS,
    ALLOWED_COMMANDS,
    BLOCKED_COMMANDS,
    _rate_limiter,
    _tool_audit_logger,
    _agent_registry,
    _job_queue,
    _user_chat_ids,
    _notification_callback,
    _runtime_config,
    TOOLS,
    TOOL_SCHEMAS,
    _generate_schemas,
    _get_security_sandbox,
    _is_untrusted_skill,
    _validate_skill_for_sandbox,
    validate_path,
    get_parallel_executor,
    is_tool_independent,
)

import importlib.util
import json
import time
import re
import asyncio
import inspect
from pathlib import Path
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ── Dynamic-tool checks (shared by register_tool & improve_skill) ────────────
# 2026-10-28 (rules re-review): an allow-list of modules and more; see
# tool_policy. Agent-made tools also run in a separate process (tool_sandbox).
from .tool_policy import ALLOWED_MODULES, validate_tool_code  # noqa: E402,F401


def _validate_tool_ast(code: str) -> Optional[str]:
    """Error message when agent-written tool code is not allowed, else None."""
    return validate_tool_code(code)


# Tools in the TOOLBOX registry that were not loaded, with the reason (doctor, Tools page).
SKIPPED_TOOLS: Dict[str, str] = {}


def load_toolbox_tool(name: str, tool_path: Path, check_code: bool = True) -> tuple[Any, Optional[str]]:
    """A TOOLBOX tool as a sandboxed callable: ``(func, None)`` or ``(None, reason)``.

    2026-10-28 (rules re-review): TOOLBOX tools are never imported into
    ZenSynora any more — the code is checked (unless the user marked the tool
    as theirs) and runs in a separate, locked-down process (tool_sandbox).
    """
    from . import tool_sandbox

    try:
        code = Path(tool_path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, f"unreadable ({e.__class__.__name__})"
    if check_code:
        problem = _validate_tool_ast(code)
        if problem:
            return None, problem.removeprefix("Error: ")
    try:
        return tool_sandbox.SandboxedTool(name, code, str(tool_path)), None
    except (ValueError, SyntaxError) as e:
        return None, str(e)


def _install(name: str, func: Any) -> None:
    TOOLS[name] = {"func": func, "desc": func.__doc__ or f"Custom tool: {name}"}
    SKIPPED_TOOLS.pop(name, None)


# ── Feature 4: Agent Builds Its Own Tools ────────────────────────────────────


def _tool_schema(name: str) -> dict:
    try:
        from . import TOOL_SCHEMAS, ensure_tool_schemas

        ensure_tool_schemas()
        for s in TOOL_SCHEMAS:
            fn = s.get("function", s)
            if fn.get("name") == name:
                return fn
    except Exception as e:  # pragma: no cover - schemas are optional here
        logger.debug("tool schema for %s unavailable: %s", name, e)
    return {}


def _signature(name: str) -> str:
    props = (_tool_schema(name).get("parameters") or {})
    req = set(props.get("required") or [])
    args = [p if p in req else f"{p}?" for p in (props.get("properties") or {})]
    return f"{name}({', '.join(args)})"


def list_tools(details: bool = False) -> str:
    """List all registered tools.

    details: False = names only; True = one line per tool with its arguments
             (? = optional) and what it does — use this to review or compare
             tools in ONE call instead of reading them one by one.
    """
    names = sorted(TOOLS.keys())
    if not details:
        return (f"Available tools ({len(names)}): " + ", ".join(names)
                + "\n(Call list_tools(details=True) for what each one does.)")
    lines = []
    for n in names:
        info = TOOLS.get(n) or {}
        desc = (info.get("desc") if isinstance(info, dict) else "") or _tool_schema(n).get("description", "")
        desc = " ".join(str(desc).split())[:160]
        lines.append(f"- {_signature(n)} — {desc}")
    return f"{len(names)} tools:\n" + "\n".join(lines)


def register_mcp_tool(name: str, server_name: str, func, documentation: str = "",
                      parameters: dict | None = None) -> str:
    """Register a remote tool retrieved via MCP (same as tools.core.register_mcp_tool)."""
    from .core import register_mcp_tool as _register

    return _register(name, server_name, func, documentation, parameters)


def register_tool(name: str, code: str, documentation: str = "") -> str:
    """Dynamically create a new tool from Python source code and store it in TOOLBOX.

    name: valid Python identifier — must match the function name defined in code
    code: full Python source for the function (use \\n for newlines)
    documentation: detailed documentation explaining what the tool does, its parameters, return values, and usage examples

    IMPORTANT: Before creating a tool, you must check if a similar tool already exists in TOOLBOX.
    Use list_toolbox() to see existing tools first.

    The tool must include:
    1. A proper docstring explaining its purpose and usage
    2. Error handling with try-except blocks
    3. Logging of errors using logger.error()

    Example:
        register_tool("greet", "def greet(who='world'):\\n    \\"\\"\\"Greet someone.\\"\\"\\"\\n    try:\\n        return f'Hello {who}!'\\n    except Exception as e:\\n        logger.error(f'Error in greet: {e}')\\n        return f'Error: {e}'\\n", "Tool to greet someone with their name")
    """
    if not name.isidentifier():
        return f"Error: '{name}' is not a valid Python identifier."

    # Check if tool already exists in TOOLBOX or is a core tool
    if name in TOOLS or name in [
        "shell",
        "read_file",
        "write_file",
        "browse",
        "download_file",
        "delegate",
        "list_tools",
        "register_tool",
        "schedule",
        "edit_schedule",
        "split_schedule",
        "suspend_schedule",
        "resume_schedule",
        "cancel_schedule",
        "list_schedules",
        "write_to_knowledge",
        "search_knowledge",
        "read_knowledge",
        "list_knowledge",
        "get_knowledge_context",
        "get_related_knowledge",
        "sync_knowledge_base",
        "list_knowledge_tags",
        "swarm_create",
        "swarm_assign",
        "swarm_status",
        "swarm_result",
        "swarm_terminate",
        "swarm_list",
        "swarm_stats",
    ]:
        return f"Error: Tool '{name}' already exists or is a protected core tool. Use list_tools() to see all available tools."

    # Check if file already exists in TOOLBOX directory
    TOOLBOX_DIR.mkdir(parents=True, exist_ok=True)
    tool_path = TOOLBOX_DIR / f"{name}.py"
    if tool_path.exists():
        return f"Error: Tool file '{name}.py' already exists in TOOLBOX. Please choose a different name or modify the existing tool."

    # Check for similar tools based on name similarity
    similar_tools = [
        t for t in TOOLS.keys() if name.lower() in t.lower() or t.lower() in name.lower()
    ]
    if similar_tools:
        return f"Error: Similar tool(s) already exist in TOOLBOX: {', '.join(similar_tools)}. Please check if an existing tool meets your needs using list_tools() or choose a more specific name."

    # Syntax validation before anything hits disk
    try:
        compile(code, "<agent-tool>", "exec")
    except SyntaxError as e:
        return f"Syntax error in tool code: {e}"

    # AST validation to prevent dangerous operations.
    # Uses the shared _validate_tool_ast helper (single policy across register_tool
    # and improve_skill).
    ast_error = _validate_tool_ast(code)
    if ast_error:
        return ast_error

    # Validate that the code has a docstring and error handling
    if '"""' not in code and "'''" not in code:
        return "Error: Tool code must include a docstring explaining its purpose and usage."

    if "try:" not in code or "except" not in code:
        return "Error: Tool code must include error handling with try-except blocks."

    if "logger.error" not in code:
        return "Error: Tool code must include error logging using logger.error()."

    # Write to disk
    tool_path.write_text(code, encoding="utf-8")

    # Create documentation file
    if documentation:
        doc_path = TOOLBOX_DIR / f"{name}_README.md"
        doc_content = f"""# {name}

## Description
{documentation}

## Code
```python
{code}
```

## Created
{datetime.now().isoformat()}

## Error Logging
Errors are logged to the standard logging system and can be found in the application logs.
"""
        doc_path.write_text(doc_content, encoding="utf-8")

    # Update main TOOLBOX README
    _update_toolbox_readme()

    # Load it in the sandbox once (catches import errors), then register the
    # sandboxed stand-in — the code never runs inside ZenSynora (2026-10-28).
    from . import tool_sandbox

    func, problem = load_toolbox_tool(name, tool_path)
    if func is None:
        tool_path.unlink(missing_ok=True)
        return f"Error: {problem}"
    problem = tool_sandbox.check(name, code, str(tool_path))
    if problem:
        tool_path.unlink(missing_ok=True)
        return f"Error loading tool: {problem}"

    _install(name, func)

    # Update dynamic schemas for LLMs
    TOOL_SCHEMAS.clear()
    TOOL_SCHEMAS.extend(_generate_schemas())

    # Persist registry so tool survives restarts (with full metadata)
    registry = {}
    if TOOLBOX_REG.exists():
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
        except Exception:
            pass

    registry[name] = {
        "path": str(tool_path),
        "name": name,
        "version": "1.0.0",
        "description": documentation,
        "tags": [],
        "author": "agent",
        "created": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
        "eval_score": None,
        "eval_count": 0,
        "enabled": True,
        "errors": [],
    }
    TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

    logger.info(f"Tool registered in TOOLBOX: {name}")
    return f"Tool '{name}' registered in TOOLBOX and available immediately. Documentation saved to {name}_README.md"


def _update_toolbox_readme():
    """Update the main TOOLBOX README with a list of all tools."""
    readme_content = """# TOOLBOX

This directory contains custom tools created by agents.

## Tools

"""
    if TOOLBOX_REG.exists():
        try:
            registry = json.loads(TOOLBOX_REG.read_text())
            for name, info in sorted(registry.items()):
                readme_content += f"### {name}\n"
                readme_content += f"- Created: {info.get('created', 'Unknown')}\n"
                readme_content += f"- Documentation: {name}_README.md\n"
                readme_content += f"- Description: {info.get('documentation', 'No documentation provided')[:100]}...\n\n"
        except Exception:
            readme_content += "No tools registered yet.\n"
    else:
        readme_content += "No tools registered yet.\n"

    readme_content += """
## Creating New Tools

When creating a new tool, the agent must:
1. Check if a similar tool already exists (use list_tools())
2. Provide comprehensive documentation
3. Include error handling with try-except blocks
4. Log errors using logger.error()
5. Include a proper docstring explaining usage

## Error Logging

All tools in the TOOLBOX use the standard Python logging system. Errors are logged and can be reviewed to improve tools.
"""

    TOOLBOX_DOCS.write_text(readme_content, encoding="utf-8")


def list_toolbox() -> str:
    """List all custom tools stored in the TOOLBOX with metadata.

    Reads the TOOLBOX registry and returns a formatted list of all
    agent-created tools, including creation date and documentation preview.
    Use this before register_tool() to check for existing similar tools.

    Returns:
        Formatted list of tool names, creation dates, and doc previews.
        'TOOLBOX is empty.' if no custom tools have been created.
        'Error listing TOOLBOX: ...' on registry read failure.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX is empty. No custom tools have been created yet."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        if not registry:
            return "TOOLBOX is empty."

        lines = ["[TOOLBOX] Contents:", ""]
        for name, info in sorted(registry.items()):
            enabled = info.get("enabled", True)
            status = "🟢" if enabled else "🔴"
            eval_score = info.get("eval_score")

            lines.append(f"{status} [TOOL] {name} v{info.get('version', '1.0.0')}")
            lines.append(f"   Author: {info.get('author', 'unknown')}")
            lines.append(f"   Created: {info.get('created', 'Unknown')}")
            if info.get("tags"):
                lines.append(f"   Tags: {', '.join(info.get('tags', []))}")
            lines.append(f"   Description: {info.get('description', 'No description')[:80]}...")
            if eval_score is not None:
                lines.append(f"   Eval Score: {eval_score:.2f} ({info.get('eval_count', 0)} runs)")
            lines.append("")

        return "\n".join(lines)
    except Exception as e:
        logger.error(f"Error listing TOOLBOX: {e}")
        return f"Error listing TOOLBOX: {e}"


def _builtin_tool_doc(name: str) -> str:
    """Documentation of a built-in tool: description, arguments and docstring."""
    import inspect

    info = TOOLS.get(name) or {}
    schema = _tool_schema(name)
    out = [f"# {name}", "", (info.get("desc") if isinstance(info, dict) else "") or schema.get("description", "")]
    props = (schema.get("parameters") or {}).get("properties") or {}
    req = set((schema.get("parameters") or {}).get("required") or [])
    if props:
        out += ["", "Arguments:"]
        for p, spec in props.items():
            kind = spec.get("type", "")
            d = " ".join(str(spec.get("description", "")).split())
            out.append(f"- {p} ({kind}{', required' if p in req else ', optional'}){': ' + d if d else ''}")
    func = info.get("func") if isinstance(info, dict) else None
    target = getattr(func, "_load", None)  # lazily imported tools (_LazyCallable)
    try:
        func = target() if callable(target) else func
    except Exception:
        pass
    doc = inspect.getdoc(func) if func is not None else ""
    if doc and doc.strip() not in "\n".join(out):
        out += ["", doc.strip()]
    return "\n".join(x for x in out if x is not None).strip()


def get_tool_documentation(name: str) -> str:
    """Get the full documentation for a specific TOOLBOX tool by name.

    Reads and returns the {name}_README.md documentation file for the given tool.
    Documentation is created automatically when a tool is registered with
    register_tool(name, code, documentation).

    Args:
        name: Tool name as registered in TOOLBOX (e.g. 'calculate_sum')

    Returns:
        Full Markdown documentation string on success.
        'No documentation found for tool {name}.' if not in TOOLBOX.
        'Error reading documentation: ...' on read failure.
    """
    doc_path = TOOLBOX_DIR / f"{name}_README.md"
    if not doc_path.exists():
        # 2026-10-21: built-in tools have no README; describe them from the
        # registry instead (agents asked for their docs and got nothing back,
        # one call per tool, until the tool-step limit stopped them).
        if name in TOOLS:
            return _builtin_tool_doc(name)
        return (f"No tool called '{name}'. "
                "Use list_tools(details=True) to see every tool with what it does.")

    try:
        return doc_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.error(f"Error reading documentation for {name}: {e}")
        return f"Error reading documentation: {e}"


def load_custom_tools():
    """Load persisted custom tools from TOOLBOX at startup — called by gateway.py / cli.py."""
    if not TOOLBOX_REG.exists():
        return
    try:
        registry = json.loads(TOOLBOX_REG.read_text())
        for name, info in registry.items():
            if isinstance(info, dict):
                tool_path = Path(info.get("path", ""))
            else:
                # Handle old format where registry was just path strings
                tool_path = Path(info)

            # Skip disabled tools (before the file check: a disabled tool whose
            # file was removed is not a problem).
            if isinstance(info, dict) and not info.get("enabled", True):
                logger.info(f"Skipping disabled tool from TOOLBOX: {name}")
                continue

            if not tool_path.exists():
                logger.warning(f"Tool file missing from TOOLBOX: {tool_path} — "
                               f"remove '{name}' from {TOOLBOX_REG.name} or set it to disabled")
                continue

            # 2026-10-27/28 (rules review): agent-made tools are checked again on load
            # (unless the user marked a tool as theirs) and always run sandboxed.
            mine = isinstance(info, dict) and info.get("author") == "user"
            func, problem = load_toolbox_tool(name, tool_path, check_code=not mine)
            if func is None:
                SKIPPED_TOOLS[name] = str(problem)
                logger.warning(f"Not loading tool '{name}' from TOOLBOX: {problem} — rewrite it with "
                               f"register_tool, or set \"author\": \"user\" in {TOOLBOX_REG.name} if you "
                               "wrote it yourself (it still runs in the tool sandbox).")
                continue
            _install(name, func)
            logger.info(f"Loaded tool from TOOLBOX (sandboxed): {name}")

        # Update the TOOLBOX README
        _update_toolbox_readme()

        # Sync dynamic schemas for LLMs after loading all tools
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())
    except Exception as e:
        logger.error(f"Error loading TOOLBOX registry: {e}")


# ── Skill Evaluation Harness ─────────────────────────────────────────────────


def get_skill_info(skill_name: str) -> str:
    """Get detailed information about a skill from the TOOLBOX registry.

    skill_name: The name of the skill to query

    Returns:
        Formatted skill information including version, tags, evaluation score, etc.
    """
    if not TOOLBOX_REG.exists():
        return f"TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."

        info = registry[skill_name]

        lines = [
            f"📋 Skill: {skill_name}",
            f"   Version: {info.get('version', '1.0.0')}",
            f"   Author: {info.get('author', 'unknown')}",
            f"   Status: {'🟢 Enabled' if info.get('enabled', True) else '🔴 Disabled'}",
            f"   Created: {info.get('created', 'Unknown')}",
            f"   Last Modified: {info.get('last_modified', 'Unknown')}",
        ]

        if info.get("tags"):
            lines.append(f"   Tags: {', '.join(info.get('tags', []))}")

        if info.get("description"):
            lines.append(f"   Description: {info.get('description')}")

        lines.extend(
            [
                f"   Evaluation Score: {info.get('eval_score', 'Not evaluated')}",
                f"   Evaluation Count: {info.get('eval_count', 0)}",
                f"   Path: {info.get('path', 'Unknown')}",
            ]
        )

        if info.get("errors"):
            lines.append(f"   Recent Errors: {len(info['errors'])}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error getting skill info: {e}")
        return f"Error getting skill info: {e}"


def enable_skill(skill_name: str) -> str:
    """Enable a disabled skill in the TOOLBOX.

    skill_name: The name of the skill to enable

    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."

        registry[skill_name]["enabled"] = True
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

        # Reload the tool into TOOLS if it was previously disabled
        if skill_name not in TOOLS:
            info = registry[skill_name]
            tool_path = Path(info.get("path", ""))
            if tool_path.exists():
                func, problem = load_toolbox_tool(skill_name, tool_path,
                                                  check_code=info.get("author") != "user")
                if func is None:
                    SKIPPED_TOOLS[skill_name] = str(problem)
                    return f"Skill enabled but not loaded: {problem}"
                _install(skill_name, func)
                logger.info(f"Enabled and loaded skill: {skill_name}")

        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())

        return f"✅ Skill '{skill_name}' enabled."

    except Exception as e:
        logger.error(f"Error enabling skill: {e}")
        return f"Error enabling skill: {e}"


def disable_skill(skill_name: str) -> str:
    """Disable an enabled skill in the TOOLBOX (soft delete).

    skill_name: The name of the skill to disable

    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."

        registry[skill_name]["enabled"] = False
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

        # Remove from TOOLS to prevent execution
        if skill_name in TOOLS:
            del TOOLS[skill_name]
            TOOL_SCHEMAS.clear()
            TOOL_SCHEMAS.extend(_generate_schemas())

        logger.info(f"Disabled skill: {skill_name}")
        return f"✅ Skill '{skill_name}' disabled."

    except Exception as e:
        logger.error(f"Error disabling skill: {e}")
        return f"Error disabling skill: {e}"


def update_skill_metadata(
    skill_name: str, tags: str = None, description: str = None, version: str = None
) -> str:
    """Update metadata for an existing skill.

    skill_name: The name of the skill to update
    tags: Comma-separated list of tags (optional)
    description: New description (optional)
    version: New version string like "1.1.0" (optional)

    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."

        if tags is not None:
            registry[skill_name]["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

        if description is not None:
            registry[skill_name]["description"] = description
            # Also update the documentation file
            doc_path = TOOLBOX_DIR / f"{skill_name}_README.md"
            if doc_path.exists():
                content = doc_path.read_text()
                if "## Description" in content:
                    content = (
                        content.split("## Description")[0]
                        + f"## Description\n{description}\n"
                        + content.split("## Description")[1].split("\n##")[1:]
                    )
                    doc_path.write_text(content)

        if version is not None:
            registry[skill_name]["version"] = version

        registry[skill_name]["last_modified"] = datetime.now().isoformat()
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

        return f"✅ Skill '{skill_name}' metadata updated."

    except Exception as e:
        logger.error(f"Error updating skill metadata: {e}")
        return f"Error updating skill metadata: {e}"


def benchmark_skill(skill_name: str, test_cases_json: str = "[]") -> str:
    """Run benchmark tests against a skill and return evaluation results.

    skill_name: The name of the skill to benchmark
    test_cases_json: JSON array of test cases. Each test case has:
        {"input": {"param": value}, "expected": "expected_output"}

    Returns:
        Formatted benchmark results with pass/fail rates and scores.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."

        if skill_name not in TOOLS:
            return f"Skill '{skill_name}' is not loaded in memory. Enable it first."
        if getattr(
            getattr(_runtime_config, "sandbox", None), "enabled", False
        ) and _is_untrusted_skill(skill_name):
            violations = _validate_skill_for_sandbox(skill_name)
            if violations:
                return f"Sandbox blocked benchmark execution: {violations}"

        test_cases = json.loads(test_cases_json)

        if not test_cases:
            return f"No test cases provided. Pass a JSON array of test cases."

        func = TOOLS[skill_name]["func"]
        results = []
        passed = 0

        for i, tc in enumerate(test_cases):
            try:
                args = tc.get("input", {})
                expected = tc.get("expected")

                # Execute the skill
                if inspect.iscoroutinefunction(func):
                    result = run_async(func, **args)
                else:
                    result = func(**args)

                # Check result
                if expected is not None:
                    # Simple string matching (could be enhanced with regex or fuzzy matching)
                    success = str(result) == str(expected)
                else:
                    # No expected value - just check it doesn't crash
                    success = True
                    result = "executed successfully"

                if success:
                    passed += 1
                    results.append(f"  ✅ Test {i + 1}: PASS")
                else:
                    results.append(f"  ❌ Test {i + 1}: FAIL (got: {str(result)[:50]}...)")

            except Exception as e:
                results.append(f"  ❌ Test {i + 1}: ERROR - {str(e)}")

        score = (passed / len(test_cases)) * 100 if test_cases else 0

        # Update registry with new evaluation score
        current_count = registry[skill_name].get("eval_count", 0)
        current_score = registry[skill_name].get("eval_score")

        if current_score is not None:
            # Running average
            new_avg = (current_score * current_count + score) / (current_count + 1)
        else:
            new_avg = score

        registry[skill_name]["eval_score"] = round(new_avg, 2)
        registry[skill_name]["eval_count"] = current_count + 1

        # Auto-disable if score is too low (< 30%)
        if score < 30 and len(test_cases) >= 3:
            registry[skill_name]["enabled"] = False
            if skill_name in TOOLS:
                del TOOLS[skill_name]

        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

        lines = [
            f"📊 Benchmark Results for '{skill_name}':",
            f"",
            f"Tests Run: {len(test_cases)}",
            f"Passed: {passed}",
            f"Failed: {len(test_cases) - passed}",
            f"Score: {score:.1f}%",
            f"Running Avg Score: {new_avg:.2f}% (from {registry[skill_name]['eval_count']} evaluations)",
            "",
            "Details:",
        ] + results

        if score < 30 and len(test_cases) >= 3:
            lines.append("")
            lines.append("⚠️ Auto-disabled due to low score (< 30%)")

        return "\n".join(lines)

    except json.JSONDecodeError as e:
        return f"Error parsing test cases JSON: {e}"
    except Exception as e:
        logger.error(f"Benchmark error: {e}")
        return f"Benchmark error: {e}"


def evaluate_skill(skill_name: str) -> str:
    """Run basic evaluation tests on a skill.

    Performs a simple sanity check:
    1. Skill can be loaded
    2. Skill has a docstring
    3. Skill doesn't crash on basic input

    skill_name: The name of the skill to evaluate

    Returns:
        Formatted evaluation results.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."

        info = registry[skill_name]
        tool_path = Path(info.get("path", ""))

        if not tool_path.exists():
            return f"Skill file not found at: {tool_path}"

        # Run basic checks
        checks = []
        score = 0
        code = ""

        # Check 1: File exists and is readable
        checks.append(("File exists and readable", True))
        score += 20

        # Check 2: Can be compiled (syntax check)
        try:
            code = tool_path.read_text()
            compile(code, tool_path.name, "exec")
            checks.append(("Code has valid Python syntax", True))
            score += 20
        except SyntaxError as e:
            checks.append(("Code has valid Python syntax", False))
            checks.append((f"Syntax error: {e}", False))

        # Check 3: Has docstring
        if '"""' in code or "'''" in code:
            checks.append(("Has docstring", True))
            score += 15
        else:
            checks.append(("Has docstring", False))

        # Check 4: Has error handling
        if "try:" in code and "except" in code:
            checks.append(("Has error handling", True))
            score += 15
        else:
            checks.append(("Has error handling", False))

        # Check 5: Has logging
        if "logger" in code:
            checks.append(("Has logging", True))
            score += 10
        else:
            checks.append(("Has logging", False))

        # Check 6: Registry metadata complete
        required_fields = ["version", "description", "tags", "author", "created"]
        missing = [f for f in required_fields if f not in info or not info[f]]
        if not missing:
            checks.append(("Registry metadata complete", True))
            score += 20
        else:
            checks.append((f"Registry metadata complete", False))
            checks.append((f"Missing fields: {', '.join(missing)}", False))

        # Update evaluation score
        current_count = info.get("eval_count", 0)
        current_score = info.get("eval_score")

        if current_score is not None:
            new_avg = (current_score * current_count + score) / (current_count + 1)
        else:
            new_avg = score

        registry[skill_name]["eval_score"] = round(new_avg, 2)
        registry[skill_name]["eval_count"] = current_count + 1
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

        lines = [
            f"📊 Evaluation Results for '{skill_name}':",
            f"",
            f"Overall Score: {score}/100",
            f"Running Avg: {new_avg:.2f}% (from {current_count + 1} evaluations)",
            "",
            "Checks:",
        ]

        for check, passed in checks:
            icon = "✅" if passed else "❌"
            lines.append(f"  {icon} {check}")

        if score < 50:
            lines.append("")
            lines.append("⚠️ Score below 50% - skill may need improvement")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Evaluation error: {e}")
        return f"Evaluation error: {e}"


# ── Skill Self-Improvement ───────────────────────────────────────────────────


def improve_skill(skill_name: str, improved_code: str, documentation: str = "") -> str:
    """Improve an existing skill with new code, with safety checks and evaluation.

    This function allows an agent to replace/update a skill's implementation with
    improved code. The new code undergoes the same security checks as register_tool(),
    and is evaluated before being activated.

    skill_name: The name of the skill to improve (must already exist)
    improved_code: Full Python source for the improved function
    documentation: Updated documentation (optional, keeps existing if empty)

    Returns:
        Success or error message with evaluation results.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX. Use register_tool() to create new skills."

        # Get existing info to preserve
        existing_info = registry[skill_name]

        # Validate skill_name
        if not skill_name.isidentifier():
            return f"Error: '{skill_name}' is not a valid Python identifier."

        # Syntax validation
        try:
            compile(improved_code, "<agent-tool>", "exec")
        except SyntaxError as e:
            return f"Syntax error in improved code: {e}"

        # AST validation for security — same policy as register_tool().
        ast_error = _validate_tool_ast(improved_code)
        if ast_error:
            return ast_error

        # Validate code requirements
        if '"""' not in improved_code and "'''" not in improved_code:
            return "Error: Improved code must include a docstring explaining its purpose and usage."

        if "try:" not in improved_code or "except" not in improved_code:
            return "Error: Improved code must include error handling with try-except blocks."

        if "logger.error" not in improved_code:
            return "Error: Improved code must include error logging using logger.error()."
        if getattr(getattr(_runtime_config, "sandbox", None), "enabled", False):
            violations = _get_security_sandbox().validate_code(improved_code)
            if violations:
                return f"Sandbox blocked improved skill content: {'; '.join(violations)}"

        # Backup existing file
        existing_path = Path(existing_info.get("path", ""))
        backup_path = None
        if existing_path.exists():
            backup_path = existing_path.with_suffix(".py.bak")
            import shutil

            shutil.copy2(existing_path, backup_path)

        # Write new code
        existing_path.write_text(improved_code, encoding="utf-8")

        # Update documentation if provided
        if not documentation:
            documentation = existing_info.get("description", "")

        if documentation:
            doc_path = TOOLBOX_DIR / f"{skill_name}_README.md"
            doc_content = f"""# {skill_name}

## Description
{documentation}

## Code
```python
{improved_code}
```

## Updated
{datetime.now().isoformat()}

## Previous Version
{existing_info.get("version", "1.0.0")}

## Error Logging
Errors are logged to the standard logging system.
"""
            doc_path.write_text(doc_content, encoding="utf-8")

        # Try to load and validate the new code
        try:
            from . import tool_sandbox

            func, problem = load_toolbox_tool(skill_name, existing_path)
            problem = problem or tool_sandbox.check(skill_name, improved_code, str(existing_path))
            if func is None or problem:
                # Restore backup
                if backup_path and backup_path.exists():
                    shutil.copy2(backup_path, existing_path)
                return f"Error: Could not load improved code ({problem}). Restored previous version."

            # Update TOOLS
            _install(skill_name, func)

        except Exception as e:
            # Restore backup on load failure
            if backup_path and backup_path.exists():
                shutil.copy2(backup_path, existing_path)
            return f"Error loading improved code: {e}. Previous version restored."

        # Update version (increment patch version)
        old_version = existing_info.get("version", "1.0.0")
        try:
            parts = old_version.split(".")
            patch = int(parts[-1]) + 1
            new_version = ".".join(parts[:-1]) + "." + str(patch)
        except:
            new_version = "1.1.0"

        # Update registry
        registry[skill_name] = {
            "path": str(existing_path),
            "name": skill_name,
            "version": new_version,
            "description": documentation or existing_info.get("description", ""),
            "tags": existing_info.get("tags", []),
            "author": "agent",
            "created": existing_info.get("created", datetime.now().isoformat()),
            "last_modified": datetime.now().isoformat(),
            "eval_score": existing_info.get("eval_score"),
            "eval_count": existing_info.get("eval_count", 0),
            "enabled": True,
            "errors": [],
        }
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

        # Update schemas
        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())

        # Clean up backup
        if backup_path and backup_path.exists():
            backup_path.unlink()

        logger.info(f"Skill improved: {skill_name} v{new_version}")

        return (
            f"✅ Skill '{skill_name}' improved successfully!\n"
            f"   Old Version: {old_version}\n"
            f"   New Version: {new_version}\n"
            f"   Code validated and loaded.\n"
            f"   Previous version backed up and replaced."
        )

    except Exception as e:
        logger.error(f"Error improving skill: {e}")
        return f"Error improving skill: {e}"


def rollback_skill(skill_name: str) -> str:
    """Rollback a skill to its previous version if a backup exists.

    skill_name: The name of the skill to rollback

    Returns:
        Success or error message.
    """
    if not TOOLBOX_REG.exists():
        return "TOOLBOX registry not found."

    try:
        registry = json.loads(TOOLBOX_REG.read_text())

        if skill_name not in registry:
            return f"Skill '{skill_name}' not found in TOOLBOX."

        existing_info = registry[skill_name]
        existing_path = Path(existing_info.get("path", ""))
        backup_path = existing_path.with_suffix(".py.bak")

        if not backup_path.exists():
            return f"No backup found for '{skill_name}'. Cannot rollback."

        # Restore backup
        import shutil

        shutil.copy2(backup_path, existing_path)

        # Update version (decrement)
        old_version = existing_info.get("version", "1.0.0")
        try:
            parts = old_version.split(".")
            patch = max(0, int(parts[-1]) - 1)
            new_version = ".".join(parts[:-1]) + "." + str(patch)
        except:
            new_version = "1.0.0"

        # Reload the tool
        func, problem = load_toolbox_tool(skill_name, existing_path)
        if func is None:
            SKIPPED_TOOLS[skill_name] = str(problem)
            return f"Restored but not loaded: {problem}"
        _install(skill_name, func)

        # Update registry
        registry[skill_name]["version"] = new_version
        registry[skill_name]["last_modified"] = datetime.now().isoformat()
        TOOLBOX_REG.write_text(json.dumps(registry, indent=2))

        TOOL_SCHEMAS.clear()
        TOOL_SCHEMAS.extend(_generate_schemas())

        return (
            f"✅ Skill '{skill_name}' rolled back to previous version.\n"
            f"   New Version: {new_version}\n"
            f"   Backup retained for another rollback if needed."
        )

    except Exception as e:
        logger.error(f"Error rolling back skill: {e}")
        return f"Error rolling back skill: {e}"
