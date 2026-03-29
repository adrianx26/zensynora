"""
Knowledge graph operations.

Provides graph traversal and network analysis over entity relations.
"""

import logging
from typing import List, Dict, Set, Optional, Tuple
from collections import deque

from .db import KnowledgeDB
from .parser import Note
from .storage import read_note

logger = logging.getLogger(__name__)


def get_related_entities(
    permalink: str,
    user_id: str = "default",
    depth: int = 1,
    relation_type: Optional[str] = None
) -> List[Dict]:
    """
    Get entities related to the given entity.
    
    Args:
        permalink: Starting entity permalink
        user_id: User ID for isolation
        depth: How many hops to traverse (1 = direct neighbors)
        relation_type: Optional filter by relation type
        
    Returns:
        List of dicts with entity info and path
    """
    with KnowledgeDB(user_id) as db:
        start_entity = db.get_entity_by_permalink(permalink)
        if not start_entity:
            return []
        
        results = []
        visited = {start_entity.id}
        queue = deque([(start_entity.id, 0, [])])  # (entity_id, depth, path)
        
        while queue:
            current_id, current_depth, path = queue.popleft()
            
            if current_depth >= depth:
                continue
            
            # Get outgoing relations
            relations = db.get_relations_from(current_id)
            
            for rel_type, target_permalink, target_name in relations:
                if relation_type and rel_type != relation_type:
                    continue
                
                target_entity = db.get_entity_by_permalink(target_permalink)
                if not target_entity:
                    continue
                
                if target_entity.id not in visited:
                    visited.add(target_entity.id)
                    new_path = path + [(rel_type, target_permalink)]
                    
                    results.append({
                        "permalink": target_permalink,
                        "name": target_name,
                        "relation_type": rel_type,
                        "depth": current_depth + 1,
                        "path": new_path
                    })
                    
                    queue.append((target_entity.id, current_depth + 1, new_path))
        
        return results


def get_entity_network(
    permalink: str,
    user_id: str = "default",
    max_depth: int = 2
) -> Dict:
    """
    Get the complete network around an entity.
    
    Args:
        permalink: Starting entity permalink
        user_id: User ID for isolation
        max_depth: Maximum traversal depth
        
    Returns:
        Dict with nodes and edges for graph visualization
    """
    with KnowledgeDB(user_id) as db:
        start_entity = db.get_entity_by_permalink(permalink)
        if not start_entity:
            return {"nodes": [], "edges": []}
        
        nodes = {}
        edges = []
        visited = {start_entity.id}
        queue = deque([(start_entity.id, 0)])
        
        # Add starting node
        nodes[start_entity.id] = {
            "id": start_entity.id,
            "permalink": start_entity.permalink,
            "name": start_entity.name,
            "depth": 0
        }
        
        while queue:
            current_id, current_depth = queue.popleft()
            
            if current_depth >= max_depth:
                continue
            
            # Get outgoing relations
            relations = db.get_relations_from(current_id)
            
            for rel_type, target_permalink, target_name in relations:
                target_entity = db.get_entity_by_permalink(target_permalink)
                if not target_entity:
                    continue
                
                # Add edge
                edges.append({
                    "from": current_id,
                    "to": target_entity.id,
                    "relation": rel_type
                })
                
                if target_entity.id not in visited:
                    visited.add(target_entity.id)
                    nodes[target_entity.id] = {
                        "id": target_entity.id,
                        "permalink": target_permalink,
                        "name": target_name,
                        "depth": current_depth + 1
                    }
                    queue.append((target_entity.id, current_depth + 1))
                
                # Get incoming relations (bidirectional exploration)
                incoming = db.get_relations_to(current_id)
                for in_rel_type, source_permalink, source_name in incoming:
                    source_entity = db.get_entity_by_permalink(source_permalink)
                    if not source_entity:
                        continue
                    
                    edges.append({
                        "from": source_entity.id,
                        "to": current_id,
                        "relation": in_rel_type
                    })
                    
                    if source_entity.id not in visited:
                        visited.add(source_entity.id)
                        nodes[source_entity.id] = {
                            "id": source_entity.id,
                            "permalink": source_permalink,
                            "name": source_name,
                            "depth": current_depth + 1
                        }
                        queue.append((source_entity.id, current_depth + 1))
        
        return {
            "nodes": list(nodes.values()),
            "edges": edges
        }


def find_path(
    from_permalink: str,
    to_permalink: str,
    user_id: str = "default",
    max_depth: int = 5
) -> Optional[List[Tuple[str, str]]]:
    """
    Find a path between two entities.
    
    Args:
        from_permalink: Starting entity
        to_permalink: Target entity
        user_id: User ID for isolation
        max_depth: Maximum search depth
        
    Returns:
        List of (relation_type, permalink) tuples forming the path, or None
    """
    with KnowledgeDB(user_id) as db:
        start = db.get_entity_by_permalink(from_permalink)
        target = db.get_entity_by_permalink(to_permalink)
        
        if not start or not target:
            return None
        
        # BFS
        queue = deque([(start.id, [])])
        visited = {start.id}
        
        while queue:
            current_id, path = queue.popleft()
            
            if len(path) >= max_depth:
                continue
            
            if current_id == target.id:
                return path
            
            # Explore neighbors
            relations = db.get_relations_from(current_id)
            for rel_type, target_permalink, _ in relations:
                neighbor = db.get_entity_by_permalink(target_permalink)
                if neighbor and neighbor.id not in visited:
                    visited.add(neighbor.id)
                    new_path = path + [(rel_type, target_permalink)]
                    queue.append((neighbor.id, new_path))
        
        return None


def get_central_entities(user_id: str = "default", limit: int = 10) -> List[Dict]:
    """
    Get the most connected entities (highest degree centrality).
    
    Args:
        user_id: User ID for isolation
        limit: Number of entities to return
        
    Returns:
        List of entities with connection counts
    """
    with KnowledgeDB(user_id) as db:
        # Count outgoing and incoming relations
        conn = db._get_connection()
        rows = conn.execute("""
            SELECT 
                e.id,
                e.permalink,
                e.name,
                (SELECT COUNT(*) FROM relations WHERE from_entity_id = e.id) as out_degree,
                (SELECT COUNT(*) FROM relations WHERE to_entity_id = e.id) as in_degree
            FROM entities e
            ORDER BY (out_degree + in_degree) DESC
            LIMIT ?
        """, (limit,)).fetchall()
        
        return [
            {
                "permalink": row['permalink'],
                "name": row['name'],
                "out_degree": row['out_degree'],
                "in_degree": row['in_degree'],
                "total_connections": row['out_degree'] + row['in_degree']
            }
            for row in rows
        ]


def build_context(
    permalink: str,
    user_id: str = "default",
    depth: int = 2,
    include_observations: bool = True
) -> str:
    """
    Build a text context for an entity including related entities.
    
    Args:
        permalink: Entity permalink
        user_id: User ID for isolation
        depth: How many hops to include
        include_observations: Whether to include observations
        
    Returns:
        Formatted context string for LLM prompts
    """
    lines = []
    
    # Get the main note
    note = read_note(permalink, user_id)
    if not note:
        return f"No knowledge found for: {permalink}"
    
    lines.append(f"# {note.title}")
    lines.append("")
    
    if include_observations and note.observations:
        lines.append("## Key Facts")
        for obs in note.observations:
            lines.append(f"- [{obs.category}] {obs.content}")
        lines.append("")
    
    # Get related entities
    related = get_related_entities(permalink, user_id, depth)
    
    if related:
        lines.append("## Related Knowledge")
        lines.append("")
        
        # Group by depth
        by_depth = {}
        for r in related:
            d = r['depth']
            if d not in by_depth:
                by_depth[d] = []
            by_depth[d].append(r)
        
        for d in sorted(by_depth.keys()):
            lines.append(f"### Depth {d}")
            for r in by_depth[d]:
                lines.append(f"- {r['relation_type']} → **{r['name']}** ({r['permalink']})")
                
                # Optionally include observations for related entities
                if include_observations and d == 1:
                    rel_note = read_note(r['permalink'], user_id)
                    if rel_note and rel_note.observations:
                        for obs in rel_note.observations[:2]:  # Limit to 2
                            lines.append(f"  - [{obs.category}] {obs.content}")
            lines.append("")
    
    return "\n".join(lines)
