import re

with open("myclaw/tools.py", "r", encoding="utf-8") as f:
    content = f.read()
    lines = content.splitlines()

def find_line(pattern, start=0):
    for i in range(start, len(lines)):
        if re.search(pattern, lines[i]):
            return i
    return -1

sections = [
    ("shell", "async def shell_async", "# -- SSH"),
    ("ssh", "# -- SSH", "# -- Internet"),
    ("web", "# -- Internet", "# -- Feature 3"),
    ("swarm", "# -- Feature 3", "# -- Feature 4"),
    ("toolbox", "# -- Feature 4", "# -- Feature 5"),
    ("scheduler", "# -- Feature 5", "# -- Knowledge"),
    ("kb", "# -- Knowledge", "# -- Agent Swarm"),
    ("swarm_tools", "# -- Agent Swarm", "# -- Phase 5/6"),
    ("management", "# -- Phase 5/6", "# -- Tool Registry"),
]

for name, start_pat, end_pat in sections:
    start = find_line(start_pat)
    end = find_line(end_pat, start)
    if start >= 0 and end >= 0:
        print(f"{name}: lines {start+1}-{end}")
    else:
        print(f"{name}: NOT FOUND start={start} end={end}")
