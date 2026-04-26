import json
from pathlib import Path
from graphify.build import build_from_json
from graphify.cluster import cluster, score_all
from graphify.analyze import god_nodes, surprising_connections, suggest_questions
from graphify.report import generate
from graphify.export import to_json, to_html

# Load extraction
a = json.loads(Path('graphify-out/.graphify_extract.json').read_text())
G = build_from_json(a)
communities = cluster(G)
cohesion = score_all(G, communities)
gods = god_nodes(G)
surprises = surprising_connections(G, communities)
labels = {cid: f'Community {cid}' for cid in communities}
# dummy detection for report
detect = {'total_files':0,'total_words':0,'files':{'code':[]}}
report = generate(G, communities, cohesion, labels, gods, surprises, detect, {'input':0,'output':0}, '.', suggested_questions=[])
# Write UTF-8
Path('graphify-out/GRAPH_REPORT.md').write_text(report, encoding='utf-8')
# Export
to_json(G, communities, 'graphify-out/graph.json')
to_html(G, communities, 'graphify-out/graph.html', community_labels=labels)
print('Report and HTML generated')
