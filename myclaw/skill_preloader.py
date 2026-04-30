"""
Proactive Skill Pre-loader - Optimization #4

Predicts and pre-loads skills before they're requested to reduce latency.
Uses pattern matching and context analysis to determine which skills are
likely to be needed based on user message history and conversation context.
"""

import logging
import asyncio
import re
from typing import Deque, Dict, List, Optional, Set
from collections import defaultdict, deque
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

from pathlib import Path
TOOLBOX_DIR = Path.home() / ".myclaw" / "TOOLBOX"
SKILL_PATTERNS = {
    'read': ['read', 'view', 'show', 'display', 'cat', 'type', 'open'],
    'write': ['write', 'create', 'save', 'edit', 'modify', 'update'],
    'search': ['search', 'find', 'look', 'grep', 'query'],
    'shell': ['run', 'execute', 'command', 'shell', 'bash', 'terminal'],
    'knowledge': ['remember', 'know', 'note', 'write_to_knowledge', 'learn'],
    'delegate': ['delegate', 'assign', 'task', 'worker', 'swarm'],
    'schedule': ['schedule', 'later', 'remind', 'cron', 'timer'],
    'file': ['file', 'path', 'directory', 'folder', 'list'],
    'web': ['browse', 'http', 'url', 'download', 'fetch', 'scrape'],
}


class SkillPredictor:
    """Analyzes conversation context to predict which skills will be needed."""
    
    def __init__(self):
        self._skill_usage_history: Dict[str, List[datetime]] = defaultdict(list)
        self._context_patterns: Dict[str, re.Pattern] = {}
        # Bounded deque: O(1) FIFO eviction at maxlen, no manual pop(0).
        self._recent_tools: Deque[str] = deque(maxlen=10)
        self._consecutive_patterns: int = 0
        self._last_prediction_time: Optional[datetime] = None
    
    def analyze_context(self, messages: List[Dict], current_message: str) -> List[str]:
        """Analyze conversation context to predict needed skills.
        
        Args:
            messages: Recent conversation messages
            current_message: The current user message
            
        Returns:
            List of predicted skill names to pre-load
        """
        predicted_skills = set()
        text_parts = [current_message.lower()]
        
        for msg in messages[-5:]:
            if isinstance(msg, dict):
                content = msg.get('content', '')
                text_parts.append(content.lower())
        combined_text = ' '.join(text_parts)
        
        for skill_name, patterns in SKILL_PATTERNS.items():
            for pattern in patterns:
                if pattern in combined_text:
                    predicted_skills.add(skill_name)
                    break
        
        # deque doesn't support negative-index slicing; convert the tail.
        recent_tail = list(self._recent_tools)[-3:]
        for tool in recent_tail:
            if tool in SKILL_PATTERNS:
                predicted_skills.add(tool)
        
        self._last_prediction_time = datetime.now()
        return list(predicted_skills)
    
    def record_tool_usage(self, tool_name: str):
        """Record that a tool was used for future prediction."""
        self._skill_usage_history[tool_name].append(datetime.now())
        # deque(maxlen=10) auto-evicts the oldest entry on append.
        self._recent_tools.append(tool_name)

        if len(self._recent_tools) >= 3:
            recent = list(self._recent_tools)[-3:]
            if len(set(recent)) == 1:
                self._consecutive_patterns += 1
            else:
                self._consecutive_patterns = 0
    
    def get_hot_skills(self, hours: int = 1) -> List[str]:
        """Get skills that have been frequently used recently."""
        cutoff = datetime.now() - timedelta(hours=hours)
        hot_skills = []
        
        for skill, timestamps in self._skill_usage_history.items():
            recent_count = sum(1 for t in timestamps if t > cutoff)
            if recent_count >= 2:
                hot_skills.append(skill)
        
        return hot_skills


class SkillPreloader:
    """Proactively loads skills before they're requested to reduce latency.
    
    Features:
    - Background skill loading
    - Predicts skills based on context
    - Maintains pre-loaded skill cache
    - Monitors skill usage patterns
    """
    
    def __init__(
        self,
        max_preloaded: int = 20,
        preload_timeout: float = 5.0,
        prediction_window: int = 5
    ):
        self.max_preloaded = max_preloaded
        self.preload_timeout = preload_timeout
        self.prediction_window = prediction_window
        
        self._preloaded_skills: Set[str] = set()
        self._loading_skills: Set[str] = set()
        self._skill_code_cache: Dict[str, str] = {}
        
        self._predictor = SkillPredictor()
        self._preload_lock = asyncio.Lock()
        self._background_task: Optional[asyncio.Task] = None
        self._running = False
        
        self.stats = {
            'preloads': 0,
            'hits': 0,
            'predictions': 0,
            'background_loads': 0
        }
    
    async def start_background_preloader(self):
        """Start background preloading task."""
        if self._running:
            return
        
        self._running = True
        self._background_task = asyncio.create_task(self._background_preloader())
        logger.info("Skill preloader background task started")
    
    async def stop_background_preloader(self):
        """Stop background preloading task."""
        self._running = False
        if self._background_task:
            self._background_task.cancel()
            try:
                await self._background_task
            except asyncio.CancelledError:
                pass
        logger.info("Skill preloader background task stopped")
    
    async def _background_preloader(self):
        """Background task that periodically preloads commonly used skills."""
        while self._running:
            try:
                await asyncio.sleep(60)
                
                if len(self._preloaded_skills) >= self.max_preloaded:
                    continue
                
                hot_skills = self._predictor.get_hot_skills(hours=1)
                for skill in hot_skills[:5]:
                    if skill not in self._preloaded_skills and skill not in self._loading_skills:
                        await self._preload_skill(skill)
                        self.stats['background_loads'] += 1
                        
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Background preload error: {e}")
    
    async def predict_and_preload(self, messages: List[Dict], current_message: str) -> List[str]:
        """Predict needed skills and pre-load them in background.
        
        Args:
            messages: Recent conversation messages
            current_message: The current user message
            
        Returns:
            List of skills that will be pre-loaded
        """
        self.stats['predictions'] += 1
        predicted = self._predictor.analyze_context(messages, current_message)
        
        skills_to_preload = [
            skill for skill in predicted
            if skill not in self._preloaded_skills and skill not in self._loading_skills
        ]
        
        for skill in skills_to_preload[:5]:
            asyncio.create_task(self._preload_skill(skill))
        
        return skills_to_preload
    
    async def _preload_skill(self, skill_name: str):
        """Pre-load a skill's code into memory."""
        async with self._preload_lock:
            if skill_name in self._preloaded_skills or skill_name in self._loading_skills:
                return
            
            self._loading_skills.add(skill_name)
        
        try:
            await asyncio.wait_for(
                self._load_skill_code(skill_name),
                timeout=self.preload_timeout
            )
            
            async with self._preload_lock:
                self._preloaded_skills.add(skill_name)
                self._loading_skills.discard(skill_name)
            
            self.stats['preloads'] += 1
            logger.debug(f"Pre-loaded skill: {skill_name}")
            
        except asyncio.TimeoutError:
            logger.warning(f"Timeout pre-loading skill: {skill_name}")
        except Exception as e:
            logger.error(f"Error pre-loading skill {skill_name}: {e}")
        finally:
            async with self._preload_lock:
                self._loading_skills.discard(skill_name)
    
    async def _load_skill_code(self, skill_name: str):
        """Load skill code into cache from TOOLBOX or system."""
        try:
            skill_file = TOOLBOX_DIR / f"{skill_name}.py"
            
            if skill_file.exists():
                # Use a thread-safe way to read the file in an async context
                def read_file():
                    with open(skill_file, 'r', encoding='utf-8') as f:
                        return f.read()
                
                code = await asyncio.to_thread(read_file)
                self._skill_code_cache[skill_name] = code
                logger.debug(f"Loaded skill code for {skill_name}")
            else:
                # If it's a built-in tool, we don't necessarily have a file in TOOLBOX
                # We can store a marker or search the registry
                self._skill_code_cache[skill_name] = "# system_built_in"
                
        except Exception as e:
            logger.error(f"Failed to load skill code for {skill_name}: {e}")
    
    def is_preloaded(self, skill_name: str) -> bool:
        """Check if a skill is already pre-loaded."""
        return skill_name in self._preloaded_skills
    
    def get_preloaded_count(self) -> int:
        """Get count of currently pre-loaded skills."""
        return len(self._preloaded_skills)
    
    def record_usage(self, tool_name: str):
        """Record tool usage for improved predictions."""
        self._predictor.record_tool_usage(tool_name)
        if tool_name in self._preloaded_skills:
            self.stats['hits'] += 1
    
    def get_stats(self) -> Dict:
        """Get preloader statistics."""
        return {
            **self.stats,
            'preloaded_count': len(self._preloaded_skills),
            'loading_count': len(self._loading_skills),
            'cache_size': len(self._skill_code_cache)
        }
    
    def clear_cache(self):
        """Clear all pre-loaded skills and cache."""
        self._preloaded_skills.clear()
        self._skill_code_cache.clear()
        self.stats = {
            'preloads': 0,
            'hits': 0,
            'predictions': 0,
            'background_loads': 0
        }
        logger.info("Skill preloader cache cleared")


_global_preloader: Optional[SkillPreloader] = None


def get_skill_preloader(
    max_preloaded: int = 20,
    preload_timeout: float = 5.0
) -> SkillPreloader:
    """Get or create the global skill preloader instance."""
    global _global_preloader
    
    if _global_preloader is None:
        _global_preloader = SkillPreloader(
            max_preloaded=max_preloaded,
            preload_timeout=preload_timeout
        )
    
    return _global_preloader


async def start_preloader():
    """Start the global skill preloader."""
    preloader = get_skill_preloader()
    await preloader.start_background_preloader()


async def stop_preloader():
    """Stop the global skill preloader."""
    global _global_preloader
    
    if _global_preloader:
        await _global_preloader.stop_background_preloader()
        _global_preloader = None


def preload_skills_for_context(messages: List[Dict], current_message: str) -> List[str]:
    """Trigger skill preloading based on context.
    
    Call this at the start of each agent interaction to pre-load
    predicted skills in the background.
    
    Args:
        messages: Recent conversation messages
        current_message: The current user message
        
    Returns:
        List of skills being pre-loaded
    """
    preloader = get_skill_preloader()
    return asyncio.get_event_loop().run_until_complete(
        preloader.predict_and_preload(messages, current_message)
    )


# ── Public API surface ───────────────────────────────────────────────
# Listing __all__ explicitly so `from this_module import *` doesn't leak
# internal helpers (e.g. _profile_cache, _LAST_ACTIVE_TIME). Names that
# aren't here are still importable by direct attribute access — they
# just don't participate in star imports.
__all__ = ['SkillPredictor', 'get_skill_preloader', 'start_preloader', 'stop_preloader']
