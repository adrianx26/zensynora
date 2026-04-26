import json
from pathlib import Path
from graphify.extract import collect_files, extract

code_paths = Path('graphify-out/code_files.txt').read_text().splitlines()
code_files = []
for f in code_paths:
    p = Path(f)
    if p.is_dir():
        code_files.extend(collect_files(p))
    else:
        code_files.append(p)
result = extract(code_files)
Path('graphify-out/.graphify_ast.json').write_text(json.dumps(result, indent=2))
print(f"AST: {len(result['nodes'])} nodes, {len(result['edges'])} edges")
