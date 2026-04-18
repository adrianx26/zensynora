import re

with open("myclaw/tools.py", "r", encoding="utf-8") as f:
    lines = f.readlines()

def find_line(pattern, start=0):
    for i in range(start, len(lines)):
        if re.search(pattern, lines[i]):
            return i
    return -1

# Section boundaries (0-indexed line numbers)
sections = {
    "shell": (666, 736),      # shell_async
    "ssh": (736, 884),        # SSH & Hardware (excludes shell which is next)
    "shell2": (884, 948),     # shell (sync)
    "files": (948, 1000),     # read_file, write_file
    "web": (1000, 1181),      # Internet & Download
    "swarm_delegation": (1181, 1203),  # Feature 3: delegation
    "toolbox": (1203, 2220),  # Feature 4 + skills
    "session": (2220, 2524),  # Session reflection + profiles
    "scheduler": (2524, 2846), # Feature 5: scheduling
    "kb": (2846, 3147),       # Knowledge Tools
    "swarm_tools": (3147, 3520), # Agent Swarm Tools
    "management": (3520, 3626), # Phase 5/6 Management
}

for name, (start, end) in sections.items():
    print(f"{name}: lines {start+1}-{end} ({end-start} lines)")
    # Print first line of section
    print(f"  First: {lines[start].strip()[:60]}")
    print(f"  Last:  {lines[end-1].strip()[:60]}")
