"""Built-in evaluation metrics.

Each metric is a callable ``(predicted, expected) -> float`` returning a
score in ``[0.0, 1.0]``. Custom metrics follow the same shape so
``Evaluator.run`` can mix and match.

The naming is deliberately boring — these are reference scorers, not a
research project. For BLEU, ROUGE, semantic similarity, etc., wrap the
relevant library and conform to the same contract.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Tuple, Union

# Public type alias — what every metric callable must satisfy.
Metric = Callable[[Any, Any], float]


def exact_match(predicted: Any, expected: Any) -> float:
    """1.0 if values are equal (after str-coerce), else 0.0."""
    return 1.0 if str(predicted) == str(expected) else 0.0


def contains(predicted: Any, expected: Any) -> float:
    """1.0 if every ``expected`` substring appears in ``predicted``.

    ``expected`` may be a single string or a list of required substrings —
    handy for "the response must mention X and Y".
    """
    text = str(predicted)
    needles: List[str]
    if isinstance(expected, (list, tuple)):
        needles = [str(s) for s in expected]
    else:
        needles = [str(expected)]
    if not needles:
        return 1.0
    hits = sum(1 for n in needles if n in text)
    return hits / len(needles)


def regex_match(predicted: Any, expected: Any) -> float:
    """1.0 if ``predicted`` matches the regex ``expected``."""
    try:
        return 1.0 if re.search(str(expected), str(predicted)) else 0.0
    except re.error:
        return 0.0


def length_within(predicted: Any, expected: Any) -> float:
    """``expected`` is ``[min, max]`` (inclusive). Score = fraction of overlap.

    Returns 1.0 when the predicted-text length falls inside the band, else
    a graceful 0–1 score that decays with distance from the band — useful
    for partial credit when models overshoot/undershoot a length budget.
    """
    if not isinstance(expected, (list, tuple)) or len(expected) != 2:
        return 0.0
    lo, hi = int(expected[0]), int(expected[1])
    n = len(str(predicted))
    if lo <= n <= hi:
        return 1.0
    if n < lo:
        return max(0.0, n / max(lo, 1))
    # n > hi — penalize linearly with distance, capped at twice the band.
    overage = n - hi
    return max(0.0, 1.0 - (overage / max(hi, 1)))


def json_subset(predicted: Any, expected: Any) -> float:
    """Predicted JSON must be a superset of expected key/value pairs.

    ``predicted`` may be a JSON string or already-parsed dict. ``expected``
    is the dict of required keys. Score = fraction of expected keys whose
    value matches in predicted.
    """
    try:
        pred_obj: Any
        if isinstance(predicted, str):
            pred_obj = json.loads(predicted)
        else:
            pred_obj = predicted
    except json.JSONDecodeError:
        return 0.0
    if not isinstance(expected, dict) or not isinstance(pred_obj, dict):
        return 0.0
    if not expected:
        return 1.0
    hits = sum(1 for k, v in expected.items() if pred_obj.get(k) == v)
    return hits / len(expected)
