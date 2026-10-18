import os

with open("myclaw/tools.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

def write_module(name, start, end, extra_imports=None, header=None):
    out_lines = []
    if header is None:
        header = name.replace("_", " ").title()
    out_lines.append("\"\"\"")
    out_lines.append(f"Tools — {header}")
    out_lines.append("\"\"\"")
    out_lines.append("")
    out_lines.append("import asyncio")
    out_lines.append("import logging")
    out_lines.append("from typing import Any, Dict, List, Optional")
    out_lines.append("")
    out_lines.append("from .core import (")
    out_lines.append("    WORKSPACE, TOOLBOX_DIR, TOOLBOX_REG, TOOLBOX_DOCS,")
    out_lines.append("    ALLOWED_COMMANDS, BLOCKED_COMMANDS,")
    out_lines.append("    _rate_limiter, _tool_audit_logger,")
    out_lines.append("    _agent_registry, _job_queue, _user_chat_ids, _notification_callback,")
    out_lines.append("    _runtime_config,")
    out_lines.append("    TOOLS, TOOL_SCHEMAS,")
    out_lines.append("    validate_path,")
    out_lines.append("    get_parallel_executor,")
    out_lines.append("    is_tool_independent,")
    out_lines.append(")")
    out_lines.append("")
    if extra_imports:
        out_lines.extend(extra_imports)
        out_lines.append("")
    out_lines.append("logger = logging.getLogger(__name__)")
    out_lines.append("")
    for line in lines[start:end]:
        out_lines.append(line.rstrip())
    filepath = f"myclaw/tools/{name}.py"
    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    print(f"Wrote {filepath}")

# shell.py
shell_lines = []
shell_lines.append("\"\"\"")
shell_lines.append("Tools — Shell Execution")
shell_lines.append("\"\"\"")
shell_lines.append("")
shell_lines.append("import asyncio")
shell_lines.append("import subprocess")
shell_lines.append("import shlex")
shell_lines.append("import logging")
shell_lines.append("import re")
shell_lines.append("import time")
shell_lines.append("from pathlib import Path")
shell_lines.append("from typing import Any, Dict, List, Optional")
shell_lines.append("")
shell_lines.append("from .core import (")
shell_lines.append("    WORKSPACE, ALLOWED_COMMANDS, BLOCKED_COMMANDS,")
shell_lines.append("    _rate_limiter, _tool_audit_logger,")
shell_lines.append(")")
shell_lines.append("")
shell_lines.append("logger = logging.getLogger(__name__)")
shell_lines.append("")
for line in lines[666:736]:
    shell_lines.append(line.rstrip())
shell_lines.append("")
for line in lines[884:947]:
    shell_lines.append(line.rstrip())
with open("myclaw/tools/shell.py", "w", encoding="utf-8") as f:
    f.write("\n".join(shell_lines) + "\n")
print("Wrote myclaw/tools/shell.py")

# ssh.py
ssh_imports = [
    "import getpass",
    "import httpx",
    "from pathlib import Path",
    "from datetime import datetime, timedelta",
]
write_module("ssh", 736, 884, ssh_imports, "SSH Remote Execution & Hardware Diagnostics")

# files.py
files_imports = [
    "from pathlib import Path",
]
write_module("files", 948, 999, files_imports, "File I/O")

# web.py
web_imports = [
    "import re",
    "import httpx",
]
write_module("web", 1000, 1181, web_imports, "Web Browsing & Download")

# swarm.py
swarm_lines = []
swarm_lines.append("\"\"\"")
swarm_lines.append("Tools — Agent Swarm & Delegation")
swarm_lines.append("\"\"\"")
swarm_lines.append("")
swarm_lines.append("import asyncio")
swarm_lines.append("import logging")
swarm_lines.append("from typing import Any, Dict, List, Optional")
swarm_lines.append("")
swarm_lines.append("from .core import (")
swarm_lines.append("    _agent_registry,")
swarm_lines.append("    TOOLS,")
swarm_lines.append(")")
swarm_lines.append("")
swarm_lines.append("logger = logging.getLogger(__name__)")
swarm_lines.append("")
for line in lines[1181:1202]:
    swarm_lines.append(line.rstrip())
swarm_lines.append("")
for line in lines[3147:3519]:
    swarm_lines.append(line.rstrip())
with open("myclaw/tools/swarm.py", "w", encoding="utf-8") as f:
    f.write("\n".join(swarm_lines) + "\n")
print("Wrote myclaw/tools/swarm.py")

# toolbox.py
toolbox_imports = [
    "import importlib.util",
    "import json",
    "import time",
    "import re",
    "import asyncio",
    "import inspect",
    "from pathlib import Path",
    "from datetime import datetime, timedelta",
]
write_module("toolbox", 1203, 2219, toolbox_imports, "TOOLBOX Skill Management")

# session.py
session_imports = [
    "import json",
    "import asyncio",
    "from pathlib import Path",
    "from datetime import datetime",
]
write_module("session", 2220, 2523, session_imports, "Session Insights & User Profiles")

# scheduler.py
scheduler_imports = [
    "import json",
    "import time",
    "import re",
    "from datetime import datetime, timedelta",
]
write_module("scheduler", 2524, 2846, scheduler_imports, "Task Scheduling")

# kb.py
kb_imports = [
    "import re",
    "from ..knowledge import (",
    "    write_note, read_note, delete_note, list_notes, search_notes,",
    "    get_related_entities, build_context, sync_knowledge, get_all_tags,",
    "    Observation, Relation",
    ")",
]
write_module("kb", 2846, 3147, kb_imports, "Knowledge Base")

# management.py
mgmt_imports = [
    "import json",
    "from ..semantic_cache import get_semantic_cache, clear_semantic_cache as clear_global_semantic_cache",
]
write_module("management", 3520, 3626, mgmt_imports, "System Management")

print("All submodules created!")
