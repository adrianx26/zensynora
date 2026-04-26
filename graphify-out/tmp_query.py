import json, sys
from pathlib import Path
from networkx.readwrite import json_graph
import networkx as nx

data = json.loads(Path('graphify-out/graph.json').read_text())
G = json_graph.node_link_graph(data, edges='links')
question = "How does the cost-tracking subsystem obtain usage data from the LLM library?"
terms = [t.lower() for t in question.split() if len(t)>3]

def score(nid):
    label = G.nodes[nid].get('label','').lower()
    return sum(1 for t in terms if t in label)
scored = [(score(nid), nid) for nid in G.nodes()]
scored = [s for s in scored if s[0]>0]
scored.sort(reverse=True)
start_nodes = [nid for _,nid in scored[:3]]
if not start_nodes:
    print('No matching nodes')
    sys.exit(0)
frontier = set(start_nodes)
sub_nodes = set(start_nodes)
edges = []
for _ in range(3):
    next_f = set()
    for n in frontier:
        for nb in G.neighbors(n):
            if nb not in sub_nodes:
                next_f.add(nb)
                edges.append((n, nb))
    sub_nodes.update(next_f)
    frontier = next_f
lines = []
lines.append(f'Start nodes: {[G.nodes[n].get("label",n) for n in start_nodes]}')
for nid in sub_nodes:
    d = G.nodes[nid]
    lines.append(f'NODE {d.get("label", nid)} src={d.get("source_file","")}')
for u,v in edges:
    e = G.edges[u,v]
    lines.append(f'EDGE {G.nodes[u].get("label",u)} --{e.get("relation","")} [{e.get("confidence","")}]--> {G.nodes[v].get("label",v)}')
print('\n'.join(lines))
