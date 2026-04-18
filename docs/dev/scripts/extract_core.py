import re

with open('myclaw/tools.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

core_lines = []

core_lines.append('"""')
core_lines.append('Tools Core — Infrastructure for MyClaw tool system.')
core_lines.append('')
core_lines.append('Provides shared registry, hooks, rate limiting, audit logging,')
core_lines.append('and validation used by all tool submodules.')
core_lines.append('"""')
core_lines.append('')

import_lines = [
    'import asyncio',
    'import subprocess',
    'import shlex',
    'import logging',
    'import json',
    'import time',
    'import re',
    'import inspect',
    'from pathlib import Path',
    'from datetime import datetime, timedelta',
    'from typing import Any, Dict, List, Optional',
    'from collections import defaultdict',
    '',
    'from ..worker_pool import WorkerPoolManager',
    'from ..sandbox import SecuritySandbox, SecurityPolicy',
    'from ..audit_log import TamperEvidentAuditLog',
    '',
]
core_lines.extend(import_lines)

core_lines.append('logger = logging.getLogger(__name__)')
core_lines.append('')

for line in lines[99:665]:
    core_lines.append(line.rstrip())

core_lines.append('')
core_lines.append('')

core_lines.append('def register_mcp_tool(name: str, server_name: str, func, documentation: str = "") -> str:')
core_lines.append('    """Register a remote tool retrieved via MCP."""')
core_lines.append('    global TOOLS')
core_lines.append('    local_name = f"mcp_{server_name}_{name}"')
core_lines.append('    TOOLS[local_name] = {')
core_lines.append('        "func": func,')
core_lines.append('        "desc": f"[{server_name} MCP] {documentation}"')
core_lines.append('    }')
core_lines.append('    return f"MCP tool \'{local_name}\' registered successfully."')
core_lines.append('')
core_lines.append('')

core_lines.append('# -- Tool Registry -----------------------------------------------------------')
core_lines.append('# NOTE: new custom tools are added to this dict at runtime by register_tool()')
core_lines.append('')
core_lines.append('TOOLS: Dict[str, dict] = {}')
core_lines.append('')

core_lines.append('')
core_lines.append('def _generate_schemas() -> list[dict]:')
core_lines.append('    schemas = []')
core_lines.append('    for name, info in TOOLS.items():')
core_lines.append('        func = info["func"]')
core_lines.append('        try:')
core_lines.append('            sig = inspect.signature(func)')
core_lines.append('        except ValueError:')
core_lines.append('            continue')
core_lines.append('            ')
core_lines.append('        params = {}')
core_lines.append('        required = []')
core_lines.append('        for param_name, param in sig.parameters.items():')
core_lines.append('            if param_name in ("user_id", "_depth", "context"):')
core_lines.append('                continue')
core_lines.append('                ')
core_lines.append('            ptype = "string"')
core_lines.append('            if param.annotation == int: ptype = "integer"')
core_lines.append('            elif param.annotation == bool: ptype = "boolean"')
core_lines.append('            elif param.annotation == float: ptype = "number"')
core_lines.append('            ')
core_lines.append('            params[param_name] = {"type": ptype, "description": ""}')
core_lines.append('            if param.default == inspect.Parameter.empty:')
core_lines.append('                required.append(param_name)')
core_lines.append('                ')
core_lines.append('        schemas.append({')
core_lines.append('            "type": "function",')
core_lines.append('            "function": {')
core_lines.append('                "name": name,')
core_lines.append('                "description": info["desc"] or "",')
core_lines.append('            "parameters": {')
core_lines.append('                    "type": "object",')
core_lines.append('                    "properties": params,')
core_lines.append('                    "required": required')
core_lines.append('                }')
core_lines.append('            }')
core_lines.append('        })')
core_lines.append('    return schemas')
core_lines.append('')
core_lines.append('TOOL_SCHEMAS: list[dict] = []')
core_lines.append('')

core_lines.append('')
core_lines.append('class _ToolFunctionsProxy(dict):')
core_lines.append('    """Proxy dict that reads tool functions from TOOLS registry."""')
core_lines.append('    def __contains__(self, key):')
core_lines.append('        return key in TOOLS')
core_lines.append('    def __getitem__(self, key):')
core_lines.append('        return TOOLS[key]["func"]')
core_lines.append('    def get(self, key, default=None):')
core_lines.append('        if key in TOOLS:')
core_lines.append('            return TOOLS[key]["func"]')
core_lines.append('        return default')
core_lines.append('    def keys(self):')
core_lines.append('        return TOOLS.keys()')
core_lines.append('    def __iter__(self):')
core_lines.append('        return iter(TOOLS)')
core_lines.append('    def __len__(self):')
core_lines.append('        return len(TOOLS)')
core_lines.append('    def items(self):')
core_lines.append('        return ((k, TOOLS[k]["func"]) for k in TOOLS)')
core_lines.append('    def values(self):')
core_lines.append('        return (TOOLS[k]["func"] for k in TOOLS)')
core_lines.append('')
core_lines.append('TOOL_FUNCTIONS = _ToolFunctionsProxy()')
core_lines.append('')

with open('myclaw/tools/core.py', 'w', encoding='utf-8') as f:
    f.write('\n'.join(core_lines) + '\n')

print('core.py written successfully')
print(f'Lines: {len(core_lines)}')
