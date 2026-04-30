"""Dataset-driven evaluation harness for ZenSynora agents.

The framework already had a tiny ``benchmark_runner.py`` with five
hardcoded tasks. This is the proper version: load a JSONL dataset,
run an async target callable against each row, compute metrics, save
the report. No LLM-specific assumptions — works for any
``async (input) -> output`` callable.
"""

from .harness import (
    EvalCase,
    EvalReport,
    Evaluator,
    load_jsonl,
    save_report,
)
from .metrics import (
    Metric,
    exact_match,
    contains,
    json_subset,
    regex_match,
    length_within,
)

__all__ = [
    "EvalCase",
    "EvalReport",
    "Evaluator",
    "load_jsonl",
    "save_report",
    "Metric",
    "exact_match",
    "contains",
    "json_subset",
    "regex_match",
    "length_within",
]
