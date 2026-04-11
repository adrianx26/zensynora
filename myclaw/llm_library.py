"""
LLM Capability Library for ZenSynora.
Tracks model limits, capabilities, and benchmark scores.
"""

from typing import Dict, Any, Optional

# Benchmark Metrics Reference:
# reasoning: Complex logic, multi-step planning
# coding: Python/Bash generation and debugging
# concise: Ability to follow short-form instructions
# tool_use: Reliability of calling external functions

MODEL_REGISTRY: Dict[str, Dict[str, Any]] = {
    # OpenAI
    "gpt-4o": {
        "tier": "premium",
        "context_window": 128000,
        "capabilities": ["tools", "vision", "audio"],
        "benchmarks": {"reasoning": 94, "coding": 92, "concise": 88},
    },
    "gpt-4o-mini": {
        "tier": "standard",
        "context_window": 128000,
        "capabilities": ["tools", "vision"],
        "benchmarks": {"reasoning": 82, "coding": 78, "concise": 92},
    },
    "o1-preview": {
        "tier": "premium",
        "context_window": 128000,
        "capabilities": ["reasoning"],
        "benchmarks": {"reasoning": 98, "coding": 95, "concise": 60},
    },
    # Anthropic
    "claude-3-5-sonnet-20241022": {
        "tier": "premium",
        "context_window": 200000,
        "capabilities": ["tools", "vision", "artifacts"],
        "benchmarks": {"reasoning": 95, "coding": 96, "concise": 90},
    },
    "claude-3-haiku-20240307": {
        "tier": "standard",
        "context_window": 200000,
        "capabilities": ["tools"],
        "benchmarks": {"reasoning": 75, "coding": 70, "concise": 95},
    },
    # Google
    "gemini-1.5-pro": {
        "tier": "premium",
        "context_window": 1000000,
        "capabilities": ["tools", "vision", "audio", "long_context"],
        "benchmarks": {"reasoning": 90, "coding": 88, "concise": 85},
    },
    "gemini-1.5-flash": {
        "tier": "standard",
        "context_window": 1000000,
        "capabilities": ["tools", "vision", "long_context"],
        "benchmarks": {"reasoning": 80, "coding": 75, "concise": 90},
    },
    # Meta / Groq / Ollama
    "llama3.1-70b": {
        "tier": "premium",
        "context_window": 128000,
        "capabilities": ["tools"],
        "benchmarks": {"reasoning": 88, "coding": 85, "concise": 88},
    },
    "llama3.2-3b": {
        "tier": "standard",
        "context_window": 128000,
        "capabilities": ["tools"],
        "benchmarks": {"reasoning": 65, "coding": 60, "concise": 92},
    },
}

def get_model_metadata(model_id: str) -> Optional[Dict[str, Any]]:
    """Retrieve metadata for a specific model, with nickname resolution."""
    # Exact match
    if model_id in MODEL_REGISTRY:
        return MODEL_REGISTRY[model_id]
    
    # Prefix match (e.g., 'gpt-4o' matches 'gpt-4o-2024-05-13')
    for key, metadata in MODEL_REGISTRY.items():
        if model_id.startswith(key):
            return metadata
            
    return None

def get_optimal_model(tier: str = "standard", preferred_capabilities: list = None) -> str:
    """Find the best model for a given tier and capability set among registry."""
    # Simple fallback for now
    candidates = [k for k, v in MODEL_REGISTRY.items() if v["tier"] == tier]
    if preferred_capabilities:
        candidates = [
            k for k in candidates 
            if all(c in MODEL_REGISTRY[k]["capabilities"] for c in preferred_capabilities)
        ]
    
    return candidates[0] if candidates else "gpt-4o-mini"
