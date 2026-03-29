"""
Semantic Memory for Preference Learning

Provides intelligent learning of user preferences from interactions:
- Automatic preference extraction from conversations
- Pattern recognition for user behavior
- Context-aware preference adaptation
- Persistent user profiles
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set

from .memory import Memory

logger = logging.getLogger(__name__)


PREFERENCE_DIR = os.path.expanduser("~/.myclaw/preferences")


@dataclass
class Preference:
    """A single user preference."""
    key: str
    value: Any
    confidence: float = 1.0
    source: str = "manual"
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    usage_count: int = 1


@dataclass
class UserProfile:
    """User profile with learned preferences."""
    user_id: str
    preferences: Dict[str, Preference] = field(default_factory=dict)
    interaction_count: int = 0
    created_at: datetime = field(default_factory=datetime.now)
    last_interaction: Optional[datetime] = None


class PreferenceLearner:
    """Learns user preferences from interactions.
    
    Features:
    - Automatic extraction of preferences from conversations
    - Confidence scoring based on frequency
    - Pattern recognition for user behavior
    - Quick adaptation to new preferences
    """
    
    PREFERENCE_PATTERNS = {
        "communication_style": ["formal", "casual", "brief", "detailed"],
        "tone": ["friendly", "professional", "direct", "humorous"],
        "response_length": ["short", "medium", "long", "adaptive"],
        "code_style": ["functional", "oop", "imperative", "declarative"],
        "documentation": ["minimal", "moderate", "extensive"],
    }
    
    TOPIC_KEYWORDS = {
        "programming": ["code", "function", "class", "api", "debug", "refactor"],
        "web": ["html", "css", "javascript", "frontend", "backend"],
        "data": ["database", "query", "analytics", "visualization"],
        "devops": ["deploy", "docker", "kubernetes", "ci/cd"],
        "ai": ["model", "llm", "training", "inference"],
    }
    
    def __init__(self, memory: Optional[Memory] = None):
        self._memory = memory
        self._profiles: Dict[str, UserProfile] = {}
        self._load_profiles()
    
    def _load_profiles(self):
        """Load saved user profiles."""
        os.makedirs(PREFERENCE_DIR, exist_ok=True)
        
        for filename in os.listdir(PREFERENCE_DIR):
            if filename.endswith(".json"):
                filepath = os.path.join(PREFERENCE_DIR, filename)
                try:
                    with open(filepath, "r") as f:
                        data = json.load(f)
                        profile = self._deserialize_profile(data)
                        self._profiles[profile.user_id] = profile
                except Exception as e:
                    logger.error(f"Error loading profile {filename}: {e}")
    
    def _deserialize_profile(self, data: Dict[str, Any]) -> UserProfile:
        """Deserialize a user profile from JSON."""
        preferences = {
            k: Preference(
                key=v["key"],
                value=v["value"],
                confidence=v.get("confidence", 1.0),
                source=v.get("source", "manual"),
                created_at=datetime.fromisoformat(v["created_at"]),
                last_updated=datetime.fromisoformat(v["last_updated"]),
                usage_count=v.get("usage_count", 1)
            )
            for k, v in data.get("preferences", {}).items()
        }
        
        return UserProfile(
            user_id=data["user_id"],
            preferences=preferences,
            interaction_count=data.get("interaction_count", 0),
            created_at=datetime.fromisoformat(data["created_at"]),
            last_interaction=datetime.fromisoformat(data["last_interaction"]) if data.get("last_interaction") else None
        )
    
    def _serialize_profile(self, profile: UserProfile) -> Dict[str, Any]:
        """Serialize a user profile to JSON."""
        return {
            "user_id": profile.user_id,
            "preferences": {
                k: {
                    "key": v.key,
                    "value": v.value,
                    "confidence": v.confidence,
                    "source": v.source,
                    "created_at": v.created_at.isoformat(),
                    "last_updated": v.last_updated.isoformat(),
                    "usage_count": v.usage_count
                }
                for k, v in profile.preferences.items()
            },
            "interaction_count": profile.interaction_count,
            "created_at": profile.created_at.isoformat(),
            "last_interaction": profile.last_interaction.isoformat() if profile.last_interaction else None
        }
    
    def _save_profile(self, user_id: str):
        """Save user profile to disk."""
        profile = self._profiles.get(user_id)
        if not profile:
            return
        
        filepath = os.path.join(PREFERENCE_DIR, f"{user_id}.json")
        try:
            with open(filepath, "w") as f:
                json.dump(self._serialize_profile(profile), f, indent=2)
        except Exception as e:
            logger.error(f"Error saving profile {user_id}: {e}")
    
    def get_profile(self, user_id: str = "default") -> Optional[UserProfile]:
        """Get user profile."""
        return self._profiles.get(user_id)
    
    def learn_from_conversation(
        self,
        user_id: str,
        messages: List[Dict[str, str]],
        assistant_response: Optional[str] = None
    ):
        """Learn preferences from a conversation.
        
        Args:
            user_id: User identifier
            messages: List of message dictionaries with 'role' and 'content'
            assistant_response: Optional assistant response for analysis
        """
        if user_id not in self._profiles:
            self._profiles[user_id] = UserProfile(user_id=user_id)
        
        profile = self._profiles[user_id]
        profile.interaction_count += 1
        profile.last_interaction = datetime.now()
        
        messages_text = " ".join(m.get("content", "") for m in messages)
        self._extract_communication_preferences(profile, messages, assistant_response)
        self._extract_topic_preferences(profile, messages_text)
        self._extract_style_preferences(profile, messages, assistant_response)
        
        self._save_profile(user_id)
        
        logger.info(f"Learned {len(messages)} messages for user {user_id}")
    
    def _extract_communication_preferences(
        self,
        profile: UserProfile,
        messages: List[Dict[str, str]],
        assistant_response: Optional[str]
    ):
        """Extract communication style preferences."""
        user_messages = [m for m in messages if m.get("role") == "user"]
        
        if not user_messages:
            return
        
        avg_length = sum(len(m.get("content", "")) / len(user_messages)
        
        if avg_length < 50:
            self._update_preference(profile, "response_length", "short", source="auto")
        elif avg_length < 200:
            self._update_preference(profile, "response_length", "medium", source="auto")
        else:
            self._update_preference(profile, "response_length", "long", source="auto")
        
        for pattern_key, patterns in self.PREFERENCE_PATTERNS.items():
            for pattern in patterns:
                if any(pattern in m.get("content", "").lower() for m in user_messages):
                    self._update_preference(profile, pattern_key, pattern, source="auto")
                    break
    
    def _extract_topic_preferences(
        self,
        profile: UserProfile,
        text: str
    ):
        """Extract topic preferences from text."""
        text_lower = text.lower()
        
        for topic, keywords in self.TOPIC_KEYWORDS.items():
            matches = sum(1 for kw in keywords if kw in text_lower)
            if matches > 0:
                current = profile.preferences.get(f"topic_{topic}")
                if current:
                    if matches > current.usage_count:
                        current.value = topic
                        current.usage_count = matches
                        current.last_updated = datetime.now()
                else:
                    profile.preferences[f"topic_{topic}"] = Preference(
                        key=f"topic_{topic}",
                        value=topic,
                        confidence=min(1.0, matches / 3),
                        source="auto"
                    )
    
    def _extract_style_preferences(
        self,
        profile: UserProfile,
        messages: List[Dict[str, str]],
        assistant_response: Optional[str]
    ):
        """Extract style preferences from messages."""
        if not assistant_response:
            return
        
        response_length = len(assistant_response)
        
        if response_length < 100:
            adapt_to = "brief"
        elif response_length < 500:
            adapt_to = "standard"
        else:
            adapt_to = "detailed"
        
        self._update_preference(profile, "detail_level", adapt_to, source="auto")
        
        if assistant_response.startswith("#") or "```" in assistant_response:
            self._update_preference(profile, "code_blocks", "preferred", source="auto")
        else:
            self._update_preference(profile, "code_blocks", "minimal", source="auto")
    
    def _update_preference(
        self,
        profile: UserProfile,
        key: str,
        value: Any,
        source: str = "manual",
        confidence: float = 0.8
    ):
        """Update or create a preference."""
        if key in profile.preferences:
            pref = profile.preferences[key]
            if pref.value == value:
                pref.usage_count += 1
                pref.confidence = min(1.0, pref.confidence + 0.1)
            else:
                pref.confidence = max(0.3, pref.confidence - 0.2)
                if pref.confidence < 0.3:
                    pref.value = value
                    pref.confidence = confidence
            pref.last_updated = datetime.now()
        else:
            profile.preferences[key] = Preference(
                key=key,
                value=value,
                confidence=confidence,
                source=source
            )
    
    def set_preference(
        self,
        user_id: str,
        key: str,
        value: Any,
        confidence: float = 1.0
    ):
        """Manually set a preference."""
        if user_id not in self._profiles:
            self._profiles[user_id] = UserProfile(user_id=user_id)
        
        profile = self._profiles[user_id]
        
        profile.preferences[key] = Preference(
            key=key,
            value=value,
            confidence=confidence,
            source="manual"
        )
        
        self._save_profile(user_id)
        logger.info(f"Set preference {key}={value} for user {user_id}")
    
    def get_preference(
        self,
        user_id: str,
        key: str,
        default: Any = None
    ) -> Any:
        """Get a preference value."""
        profile = self._profiles.get(user_id)
        if not profile:
            return default
        
        pref = profile.preferences.get(key)
        if not pref:
            return default
        
        if pref.confidence < 0.3:
            return default
        
        return pref.value
    
    def get_all_preferences(self, user_id: str) -> Dict[str, Any]:
        """Get all preferences for a user."""
        profile = self._profiles.get(user_id)
        if not profile:
            return {}
        
        return {
            k: v.value
            for k, v in profile.preferences.items()
            if v.confidence >= 0.3
        }
    
    def get_adaptive_context(self, user_id: str) -> Dict[str, Any]:
        """Get adaptive context based on learned preferences."""
        prefs = self.get_all_preferences(user_id)
        
        return {
            "detail_level": prefs.get("detail_level", "standard"),
            "response_length": prefs.get("response_length", "medium"),
            "code_blocks": prefs.get("code_blocks", "preferred"),
            "communication_style": prefs.get("communication_style", "adaptive"),
            "tone": prefs.get("tone", "professional"),
            "active_topics": [
                k.replace("topic_", "")
                for k, v in prefs.items()
                if k.startswith("topic_")
            ]
        }
    
    def clear_preferences(self, user_id: str):
        """Clear all preferences for a user."""
        if user_id in self._profiles:
            del self._profiles[user_id]
        
        filepath = os.path.join(PREFERENCE_DIR, f"{user_id}.json")
        if os.path.exists(filepath):
            os.remove(filepath)
        
        logger.info(f"Cleared preferences for user {user_id}")


class SemanticMemory:
    """Enhanced memory with semantic preference learning.
    
    Integrates preference learning with the existing Memory class
    for intelligent context management.
    """
    
    def __init__(self, memory: Optional[Memory] = None):
        self._memory = memory
        self._learner = PreferenceLearner(memory)
    
    @property
    def learner(self) -> PreferenceLearner:
        return self._learner
    
    def learn_from_interaction(
        self,
        user_id: str,
        user_message: str,
        assistant_response: str,
        context: Optional[Dict[str, Any]] = None
    ):
        """Learn from a user interaction.
        
        Args:
            user_id: User identifier  
            user_message: User's message
            assistant_response: Assistant's response
            context: Optional additional context
        """
        messages = [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_response}
        ]
        
        self._learner.learn_from_conversation(user_id, messages, assistant_response)
    
    def get_context_for_prompt(
        self,
        user_id: str,
        include_preferences: bool = True
    ) -> str:
        """Get learned context for inclusion in prompts.
        
        Args:
            user_id: User identifier
            include_preferences: Whether to include preferences
            
        Returns:
            Context string to prepend to prompts
        """
        if not include_preferences:
            return ""
        
        prefs = self._learner.get_adaptive_context(user_id)
        
        if not prefs:
            return ""
        
        context_parts = []
        
        if prefs.get("detail_level") == "brief":
            context_parts.append("Keep responses brief.")
        elif prefs.get("detail_level") == "detailed":
            context_parts.append("Provide detailed responses.")
        
        tone = prefs.get("tone")
        if tone:
            context_parts.append(f"Use a {tone} tone.")
        
        code_pref = prefs.get("code_blocks")
        if code_pref == "minimal":
            context_parts.append("Skip code blocks unless explicitly requested.")
        
        topics = prefs.get("active_topics", [])
        if topics:
            context_parts.append(f"User interested in: {', '.join(topics[:3])}")
        
        if context_parts:
            return "\n".join(context_parts) + "\n"
        
        return ""


__all__ = [
    "Preference",
    "UserProfile", 
    "PreferenceLearner",
    "SemanticMemory",
]