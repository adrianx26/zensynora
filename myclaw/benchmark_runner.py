"""
LLM Benchmark Runner for ZenSynora.
Evaluates configured models against a set of static tasks.
"""

import asyncio
import time
import json
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional

from .provider import get_provider
from .config import load_config

logger = logging.getLogger(__name__)

BENCHMARK_DIR = Path.home() / ".myclaw" / "benchmarks"
BENCHMARK_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = BENCHMARK_DIR / "results.json"

# Static benchmark tasks
BENCHMARK_TASKS = [
    {
        "id": "logic_basic",
        "category": "reasoning",
        "prompt": "If a doctor gives you 3 pills and tells you to take one every half hour, how long will they last?",
        "expected_regex": r"1\s*hour|60\s*minutes",
        "description": "Basic logic/temporal calculation"
    },
    {
        "id": "python_code",
        "category": "coding",
        "prompt": "Write a Python function `is_prime(n)` that returns True if n is prime, in one line using all()",
        "expected_regex": r"all\(n\s*%\s*i\s*!=\s*0|n\s*>\s*1\s*and\s*all",
        "description": "Python one-liner proficiency"
    },
    {
        "id": "translation_idomatic",
        "category": "lingua",
        "prompt": "Translate the idiom 'Piece of cake' to French.",
        "expected_regex": r"Simple\s*comme\s*bonjour|C'est\s*du\s*gâteau",
        "description": "Idiomatic translation"
    },
    {
        "id": "instruction_following",
        "category": "format",
        "prompt": "Output only the word 'Zensynora' and nothing else. No punctuation.",
        "expected_regex": r"^Zensynora$",
        "description": "Strict instruction following"
    }
]

class BenchmarkRunner:
    """Orchestrates model evaluation against static tasks."""

    def __init__(self, config=None):
        self.config = config or load_config()
        self.results = self._load_results()

    def _load_results(self) -> Dict[str, Any]:
        if RESULTS_FILE.exists():
            try:
                return json.loads(RESULTS_FILE.read_text())
            except Exception:
                return {}
        return {}

    def _save_results(self):
        RESULTS_FILE.write_text(json.dumps(self.results, indent=2))

    async def run_model_benchmark(self, model_id: str, provider_name: str = None) -> Dict[str, Any]:
        """Run all tasks against a specific model."""
        provider = get_provider(self.config, provider_name)
        model_results = []
        
        logger.info(f"🚀 Starting benchmark for {model_id} via {provider.__class__.__name__}")
        
        for task in BENCHMARK_TASKS:
            start_time = time.time()
            try:
                # We use a simple chat call for benchmarking
                # Note: Token usage is estimated if provider doesn't return it
                response, _ = await provider.chat(
                    messages=[{"role": "user", "content": task["prompt"]}],
                    model=model_id
                )
                end_time = time.time()
                
                latency = end_time - start_time
                is_correct = bool(re.search(task["expected_regex"], response, re.IGNORECASE))
                
                # Estimated tokens (4 chars per token)
                tokens_in = len(task["prompt"]) // 4
                tokens_out = len(response) // 4
                
                res = {
                    "task_id": task["id"],
                    "latency": round(latency, 3),
                    "is_correct": is_correct,
                    "tokens_in": tokens_in,
                    "tokens_out": tokens_out,
                    "timestamp": datetime.now().isoformat()
                }
                model_results.append(res)
                logger.debug(f"Task {task['id']} complete: {'✅' if is_correct else '❌'} in {latency:.2f}s")
                
            except Exception as e:
                logger.error(f"Error benchmarking {model_id} on task {task['id']}: {e}")
                model_results.append({
                    "task_id": task["id"],
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                })

        # Summary statistics
        successful = [r for r in model_results if "error" not in r]
        accuracy = sum(1 for r in successful if r["is_correct"]) / len(BENCHMARK_TASKS) if successful else 0
        avg_latency = sum(r["latency"] for r in successful) / len(successful) if successful else 0
        
        summary = {
            "model_id": model_id,
            "provider": provider_name or "default",
            "accuracy": round(accuracy * 100, 1),
            "avg_latency": round(avg_latency, 3),
            "total_tokens": sum(r.get("tokens_in", 0) + r.get("tokens_out", 0) for r in successful),
            "last_run": datetime.now().isoformat(),
            "detail": model_results
        }
        
        self.results[f"{provider_name or 'default'}:{model_id}"] = summary
        self._save_results()
        
        return summary

    def get_comparison_table(self) -> str:
        """Returns a Markdown table comparing model performance."""
        if not self.results:
            return "No benchmark results found. Run a benchmark first."
            
        lines = [
            "| Provider:Model | Accuracy | Avg Latency | Total Tokens | Last Run |",
            "|----------------|----------|-------------|--------------|----------|"
        ]
        
        for key, sumry in self.results.items():
            lines.append(
                f"| {key} | {sumry['accuracy']}% | {sumry['avg_latency']}s | {sumry['total_tokens']} | {sumry['last_run'][:16].replace('T', ' ')} |"
            )
            
        return "\n".join(lines)

async def run_all_benchmarks():
    """CLI entry point to benchmark all configured agents."""
    config = load_config()
    runner = BenchmarkRunner(config)
    
    # Benchmark default
    await runner.run_model_benchmark(config.agents.defaults.model, config.agents.defaults.provider)
    
    # Benchmark named agents
    for agent in config.agents.named:
        await runner.run_model_benchmark(agent.model, agent.provider or config.agents.defaults.provider)
    
    print("\n📊 Benchmark Results:")
    print(runner.get_comparison_table())

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_all_benchmarks())
