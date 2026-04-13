"""
Knowledge Researcher for ZenSynora.
Identifies knowledge gaps from logs and performs automated web research.
"""

import os
import json
import logging
import asyncio
import time
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional

try:
    from scrapling import Fetcher
except ImportError:
    Fetcher = None

from ..config import load_config
from ..provider import get_provider
from .db import KnowledgeDB
from .storage import write_note, get_knowledge_dir

logger = logging.getLogger(__name__)

GAP_FILE = Path.home() / ".myclaw" / "knowledge_gaps.jsonl"
RESEARCH_LOG = Path.home() / ".myclaw" / "research_log.jsonl"

class GapResearcher:
    """Background worker that fills knowledge gaps using web search."""

    def __init__(self, config=None):
        self.config = config or load_config()
        self._is_running = False

    def _get_unprocessed_gaps(self) -> List[Dict[str, Any]]:
        """Read gaps from the JSONL file and filter duplicates/processed."""
        if not GAP_FILE.exists():
            return []
        
        gaps = []
        try:
            with open(GAP_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        try:
                            gaps.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"Error reading gap file: {e}")
            
        # De-duplicate by query
        unique_gaps = {}
        for g in gaps:
            unique_gaps[g["query"].lower()] = g
            
        return list(unique_gaps.values())

    async def research_gap(self, gap_query: str) -> bool:
        """Perform web research for a specific gap query."""
        if not Fetcher:
            logger.warning("Scrapling not available, skipping research")
            return False

        logger.info(f"🔍 Researching knowledge gap: '{gap_query}'")
        
        try:
            # 1. Search the web
            fetcher = Fetcher()
            # Simple search using a search engine (DuckDuckGo or Google proxy)
            search_url = f"https://www.google.com/search?q={gap_query}+detailed+information"
            r = fetcher.get(search_url)
            
            # Simple extraction: Get first few results or snippets
            # In a real scenario, we'd follow links. For now, we take text content helper
            content = r.text[:10000] # Limit content size
            
            # 2. Use an agent to synthesize the information
            provider = get_provider(self.config)
            system_prompt = (
                "You are the ZenSynora Knowledge Researcher. "
                "Synthesize the following web search data into a clean, factual knowledge base entry. "
                "Focus on accuracy and brevity. Format as a markdown note."
            )
            
            user_msg = f"Web Search Data for '{gap_query}':\n\n{content}\n\nTask: Summarize the findings for '{gap_query}'."
            
            summary, _ = await provider.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_msg}
                ],
                model=self.config.agents.defaults.model
            )
            
            # 3. Store in Knowledge Base
            db = KnowledgeDB()
            
            tags = ["automated-research", "knowledge-gap-filled"]
            
            # Save note using existing storage functions
            note_permalink = write_note(
                name=f"research-{gap_query.lower().replace(' ', '-')}",
                title=f"Research: {gap_query}",
                content=summary,
                tags=tags
            )
            
            # 4. Log the research
            self._log_research(gap_query, note_permalink)
            
            return True
            
        except Exception as e:
            logger.error(f"Research failed for '{gap_query}': {e}")
            return False

    def _log_research(self, query: str, note_id: str):
        """Record successful research in the research log."""
        log_entry = {
            "query": query,
            "note_id": note_id,
            "timestamp": datetime.now().isoformat()
        }
        with open(RESEARCH_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(log_entry) + "\n")

    async def run_batch(self):
        """Process all pending gaps."""
        if self._is_running:
            return
        
        self._is_running = True
        try:
            gaps = self._get_unprocessed_gaps()
            # Sort by timestamp (optional)
            
            # Limit batch size to avoid long runs
            for gap in gaps[:5]:
                await self.research_gap(gap["query"])
                await asyncio.sleep(5) # Small delay between searches
                
            # Clear the gap file after processing (or mark items as done)
            # For simplicity, we'll just truncate it if we processed enough
            if len(gaps) <= 5:
                # Truncate file
                with open(GAP_FILE, "w"): pass
        finally:
            self._is_running = False

async def start_researcher_job():
    """Entry point for the background research job."""
    researcher = GapResearcher()
    await researcher.run_batch()
