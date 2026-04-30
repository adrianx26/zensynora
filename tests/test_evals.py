"""Tests for the eval harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myclaw.evals import (
    EvalCase,
    Evaluator,
    contains,
    exact_match,
    json_subset,
    length_within,
    load_jsonl,
    regex_match,
    save_report,
)


# ── Metrics ────────────────────────────────────────────────────────────────


def test_exact_match():
    assert exact_match("a", "a") == 1.0
    assert exact_match("a", "b") == 0.0
    assert exact_match(1, "1") == 1.0  # str-coerced


def test_contains_single_and_list():
    assert contains("hello world", "world") == 1.0
    assert contains("hello world", ["hello", "world"]) == 1.0
    assert contains("hello world", ["hello", "missing"]) == pytest.approx(0.5)
    assert contains("hello world", []) == 1.0  # vacuously true


def test_regex_match():
    assert regex_match("foo123", r"^foo\d+$") == 1.0
    assert regex_match("nope", r"^foo\d+$") == 0.0
    assert regex_match("anything", r"[") == 0.0  # invalid regex => 0


def test_length_within_band():
    assert length_within("abcde", [3, 7]) == 1.0
    assert length_within("ab", [3, 7]) == pytest.approx(2 / 3)
    # Overshoot: linear decay.
    out = length_within("a" * 14, [3, 7])
    assert 0.0 <= out < 1.0


def test_length_within_rejects_bad_expected():
    assert length_within("abc", "not-a-list") == 0.0


def test_json_subset_dict_input():
    assert json_subset({"a": 1, "b": 2}, {"a": 1}) == 1.0
    assert json_subset({"a": 1, "b": 2}, {"a": 1, "b": 99}) == pytest.approx(0.5)


def test_json_subset_string_input():
    assert json_subset('{"a": 1}', {"a": 1}) == 1.0
    assert json_subset("not-json", {"a": 1}) == 0.0


# ── load_jsonl / save_report ──────────────────────────────────────────────


def test_load_jsonl_roundtrip(tmp_path: Path):
    p = tmp_path / "ds.jsonl"
    p.write_text(
        '{"id": "c1", "input": "hi", "expected": {"em": "hello"}}\n'
        '\n'
        '{"input": "yo", "expected": {"em": "yo"}}\n',
        encoding="utf-8",
    )
    cases = load_jsonl(p)
    assert len(cases) == 2
    assert cases[0].case_id == "c1"
    # Auto-generated id when omitted.
    assert cases[1].case_id.startswith("case-")


def test_load_jsonl_rejects_invalid_json(tmp_path: Path):
    p = tmp_path / "bad.jsonl"
    p.write_text('{"input": "ok"}\n{not-json}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_jsonl(p)


# ── Evaluator ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluator_perfect_run():
    async def echo(x):
        return x

    cases = [
        EvalCase(case_id="c1", input="alpha", expected={"em": "alpha"}),
        EvalCase(case_id="c2", input="beta", expected={"em": "beta"}),
    ]
    ev = Evaluator(target=echo, metrics={"em": exact_match})
    report = await ev.run(cases)

    assert report.dataset_size == 2
    assert report.failure_count == 0
    assert report.metric_means["em"] == 1.0
    assert report.overall_score == 1.0


@pytest.mark.asyncio
async def test_evaluator_mixed_scores():
    async def echo(x):
        return x

    cases = [
        EvalCase(case_id="c1", input="alpha", expected={"em": "alpha"}),
        EvalCase(case_id="c2", input="beta", expected={"em": "WRONG"}),
    ]
    ev = Evaluator(target=echo, metrics={"em": exact_match})
    report = await ev.run(cases)
    assert report.metric_means["em"] == pytest.approx(0.5)
    assert report.failure_count == 0


@pytest.mark.asyncio
async def test_evaluator_records_target_exceptions_as_failures():
    async def boom(_):
        raise RuntimeError("nope")

    cases = [EvalCase(case_id="c1", input="x", expected={"em": "x"})]
    ev = Evaluator(target=boom, metrics={"em": exact_match})
    report = await ev.run(cases)
    assert report.failure_count == 1
    assert report.metric_means["em"] == 0.0
    assert report.cases[0].error and "RuntimeError" in report.cases[0].error


@pytest.mark.asyncio
async def test_evaluator_timeout_marks_case_failed():
    import asyncio

    async def slow(_):
        await asyncio.sleep(1.0)
        return "ok"

    cases = [EvalCase(case_id="slow", input="x", expected={"em": "ok"})]
    ev = Evaluator(target=slow, metrics={"em": exact_match}, timeout_per_case=0.05)
    report = await ev.run(cases)
    assert report.failure_count == 1


@pytest.mark.asyncio
async def test_evaluator_concurrency_returns_correct_size():
    async def echo(x):
        return x

    cases = [
        EvalCase(case_id=f"c{i}", input=str(i), expected={"em": str(i)})
        for i in range(20)
    ]
    ev = Evaluator(target=echo, metrics={"em": exact_match}, concurrency=5)
    report = await ev.run(cases)
    assert report.dataset_size == 20
    assert report.metric_means["em"] == 1.0


@pytest.mark.asyncio
async def test_evaluator_empty_dataset_returns_zero_metrics():
    async def echo(x): return x
    ev = Evaluator(target=echo, metrics={"em": exact_match})
    report = await ev.run([])
    assert report.dataset_size == 0
    assert report.failure_count == 0
    assert report.metric_means["em"] == 0.0


def test_evaluator_rejects_no_metrics():
    async def echo(x): return x
    with pytest.raises(ValueError):
        Evaluator(target=echo, metrics={})


@pytest.mark.asyncio
async def test_save_report_writes_json(tmp_path: Path):
    async def echo(x): return x
    cases = [EvalCase(case_id="c1", input="x", expected={"em": "x"})]
    ev = Evaluator(target=echo, metrics={"em": exact_match})
    report = await ev.run(cases)

    out = tmp_path / "rep" / "report.json"
    save_report(report, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["dataset_size"] == 1
    assert data["overall_score"] == 1.0
