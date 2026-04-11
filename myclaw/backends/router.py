"""
Intelligent Agent Router for ZenSynora.
Analyzes task complexity and intent to select the most appropriate LLM.
"""

import logging
import re
from typing import List, Dict, Any, Optional, Tuple
from ..llm_library import MODEL_REGISTRY, get_model_metadata

logger = logging.getLogger(__name__)

class IntelligentRouter:
    """Orchestrates model selection based on task requirements and user configuration."""

    def __init__(self, config):
        self.config = config
        self.routing_cfg = config.intelligence.routing

    def get_routing_decision(self, user_message: str, current_model: str) -> Optional[str]:
        """
        Determine if the model should be changed for the current request.
        
        Returns:
            The ID of the model to use, or None if no change is needed.
        """
        if not self.routing_cfg.enabled and not self.config.intelligence.intelligent_routing:
            return None

        # 1. Analyze Intent & Complexity
        intent, complexity = self._analyze_intent(user_message)
        
        # 2. Get Available & Participating Candidates
        candidates = self._get_participating_candidates()
        
        # 3. Auto-Disable Logic
        if self.routing_cfg.auto_disable_on_single and len(candidates) <= 1:
            logger.debug("Routing auto-disabled: Only one candidate model available.")
            return None

        # 4. Selection Logic
        target_tier = "premium" if complexity == "high" else "standard"
        
        # Try to find a match in the target tier
        selected_model = self._select_best_candidate(candidates, target_tier, intent)
        
        if selected_model and selected_model != current_model:
            logger.info(f"Intelligent Routing Decision: [{complexity}/{intent}] -> {selected_model}")
            return selected_model

        return None

    def _analyze_intent(self, message: str) -> Tuple[str, str]:
        """Classify message intent and complexity level."""
        low_msg = message.lower()
        
        # Intent patterns
        patterns = {
            "coding": [r"def ", r"class ", r"async ", r"fix bug", r"refactor", r"code for", r"implement"],
            "math": [r"calculate", r"solve", r"integral", r"derivative", r"math"],
            "reasoning": [r"analyze", r"compare", r"pros and cons", r"explain how", r"why"],
            "creative": [r"write a story", r"poem", r"creative", r"brainstorm"]
        }
        
        detected_intent = "general"
        for intent, regexes in patterns.items():
            if any(re.search(p, low_msg) for p in regexes):
                detected_intent = intent
                break
        
        # Complexity heuristic
        is_high = False
        high_complexity_keywords = ["architecture", "optimize", "security", "deep dive", "comprehensive"]
        if detected_intent in ["coding", "math"] or \
           len(message.split()) > 60 or \
           any(kw in low_msg for kw in high_complexity_keywords):
            is_high = True
            
        return detected_intent, ("high" if is_high else "standard")

    def _get_participating_candidates(self) -> Dict[str, Dict[str, Any]]:
        """Filter the MODEL_REGISTRY based on configured providers and allowlists."""
        participating = {}
        
        # Get active providers (those with keys or local hosts)
        active_providers = self._get_active_providers()
        
        for model_id, meta in MODEL_REGISTRY.items():
            provider = model_id.split("-")[0] # Simple heuristic for registry names
            
            # 1. Check if provider is enabled/configured
            if not self._is_provider_active(model_id, active_providers):
                continue
                
            # 2. Check Provider Allowlist
            if self.routing_cfg.allowed_providers and provider not in self.routing_cfg.allowed_providers:
                continue
            
            # 3. Check Model Allowlist
            if self.routing_cfg.allowed_models and model_id not in self.routing_cfg.allowed_models:
                continue
                
            participating[model_id] = meta

        return participating

    def _get_active_providers(self) -> List[str]:
        """Check config to see which providers are actually usable."""
        active = []
        p_cfg = self.config.providers
        
        # Local
        active.append("ollama") # Always assume available if configured
        active.append("lmstudio")
        active.append("llamacpp")
        
        # Online (Check for keys)
        if p_cfg.openai.api_key.get_secret_value(): active.append("openai")
        if p_cfg.anthropic.api_key.get_secret_value(): active.append("anthropic")
        if p_cfg.gemini.api_key.get_secret_value(): active.append("gemini")
        if p_cfg.groq.api_key.get_secret_value(): active.append("groq")
        if p_cfg.openrouter.api_key.get_secret_value(): active.append("openrouter")
        
        return active

    def _is_provider_active(self, model_id: str, active_providers: List[str]) -> bool:
        """Map model prefixes to active providers."""
        model_id = model_id.lower()
        if "gpt" in model_id and "openai" in active_providers: return True
        if "claude" in model_id and "anthropic" in active_providers: return True
        if "gemini" in model_id and "gemini" in active_providers: return True
        if "llama" in model_id: return True # Usually local or Groq
        return False

    def _select_best_candidate(self, candidates: Dict[str, Dict[str, Any]], tier: str, intent: str) -> Optional[str]:
        """Find the best model among candidates for the given tier/intent."""
        matches = [k for k, v in candidates.items() if v["tier"] == tier]
        
        if not matches:
            # Fallback to standard if premium not available, or vice versa
            matches = list(candidates.keys())

        if not matches:
            return None

        # Free-First Priority
        if self.routing_cfg.prefer_free_models:
            free_matches = [m for m in matches if self._is_free_model(m)]
            if free_matches:
                matches = free_matches

        # Sort by benchmark score for the specific intent
        def get_score(m_id):
            meta = MODEL_REGISTRY.get(m_id, {})
            bench = meta.get("benchmarks", {})
            return bench.get(intent, bench.get("reasoning", 0))

        matches.sort(key=get_score, reverse=True)
        return matches[0]

    def _is_free_model(self, model_id: str) -> bool:
        """Heuristic to determine if a model is 'free' (local or free tier)."""
        free_keywords = ["llama", "mistral", "gemma", "phi"]
        flash_keywords = ["flash"] # Gemini Flash often has free tier
        
        m_lower = model_id.lower()
        if any(k in m_lower for k in free_keywords):
            return True # Assume Llama/Mistral/etc are often local or Groq-free
        if any(k in m_lower for k in flash_keywords):
            return True
        return False
