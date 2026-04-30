"""Tests for cost_tracker queries used by the dashboard endpoints."""

import importlib
from pathlib import Path

import pytest


@pytest.fixture
def isolated_cost_tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Reload cost_tracker pointed at a temp DB so tests don't see real spend."""
    from myclaw import cost_tracker as ct

    monkeypatch.setattr(ct, "COST_DB_PATH", tmp_path / "costs.db")
    # Force the schema to recreate against the new path.
    return ct


def test_record_and_summary(isolated_cost_tracker):
    ct = isolated_cost_tracker
    ct.record_usage("openai", "gpt-4o-mini", prompt_tokens=1000, completion_tokens=500)
    summary = ct.get_cost_summary()
    assert summary["total_prompt_tokens"] == 1000
    assert summary["total_completion_tokens"] == 500
    assert summary["total_cost_usd"] > 0


def test_costs_by_provider(isolated_cost_tracker):
    ct = isolated_cost_tracker
    ct.record_usage("openai", "gpt-4o-mini", 100, 50)
    ct.record_usage("anthropic", "claude-3-haiku-20240307", 200, 100)
    rows = ct.get_monthly_costs()
    providers = {r["provider"]: r for r in rows}
    assert "openai" in providers
    assert "anthropic" in providers


def test_costs_by_model_breakdown(isolated_cost_tracker):
    ct = isolated_cost_tracker
    ct.record_usage("openai", "gpt-4o-mini", 100, 50)
    ct.record_usage("openai", "gpt-4o", 1000, 500)
    rows = ct.get_costs_by_model(limit=10)
    models = {r["model"] for r in rows}
    assert {"gpt-4o-mini", "gpt-4o"}.issubset(models)
    # gpt-4o is more expensive — should be first when ordered by cost desc.
    assert rows[0]["model"] == "gpt-4o"


def test_costs_by_model_respects_limit(isolated_cost_tracker):
    ct = isolated_cost_tracker
    for model in ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4"]:
        ct.record_usage("openai", model, 100, 50)
    rows = ct.get_costs_by_model(limit=2)
    assert len(rows) == 2


def test_daily_timeline(isolated_cost_tracker):
    ct = isolated_cost_tracker
    ct.record_usage("openai", "gpt-4o-mini", 100, 50)
    ct.record_usage("openai", "gpt-4o-mini", 200, 100)
    rows = ct.get_daily_timeline(days=30)
    # Both calls land in today's bucket.
    assert len(rows) >= 1
    today = rows[-1]
    assert today["request_count"] >= 2
    assert today["total_tokens"] >= 450


def test_unknown_provider_records_zero_cost(isolated_cost_tracker):
    ct = isolated_cost_tracker
    out = ct.record_usage("unknown_provider", "some-model", 100, 50)
    assert out["cost_usd"] == 0.0
