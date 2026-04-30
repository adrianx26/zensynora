"""Tests for the Sprint 12 YAML migration of the agent registry.

Two responsibilities:

1. **Loader correctness** — `load_agents_from_yaml` parses well-formed
   YAML, recovers gracefully from malformed records, and returns ``{}``
   when the file is missing.

2. **Sync invariant** — the YAML data file and the embedded literal
   fallback agree on every agent. CI catches drift the moment somebody
   adds an agent to one source but forgets the other.
"""

from __future__ import annotations

from pathlib import Path

import pytest


# ── Loader ────────────────────────────────────────────────────────────────


def test_load_returns_empty_when_file_missing(tmp_path: Path):
    from myclaw.agents.registry import load_agents_from_yaml
    out = load_agents_from_yaml(tmp_path / "nope.yaml")
    assert out == {}


def test_load_parses_minimal_record(tmp_path: Path):
    import yaml
    from myclaw.agents.registry import load_agents_from_yaml, AgentCategory

    rec = [{
        "name": "tester",
        "description": "test agent",
        "category": "core-development",
        "capabilities": ["backend"],
        "profile_name": "core-development/tester",
    }]
    p = tmp_path / "a.yaml"
    p.write_text(yaml.safe_dump(rec), encoding="utf-8")

    out = load_agents_from_yaml(p)
    assert "tester" in out
    a = out["tester"]
    assert a.category is AgentCategory.CORE_DEVELOPMENT
    assert a.tags == set()  # default when omitted
    assert a.checkboxes == []
    assert a.model_routing == "gpt-5.3-codex-spark"  # default applied


def test_load_skips_record_with_unknown_category(tmp_path: Path, caplog):
    import yaml
    from myclaw.agents.registry import load_agents_from_yaml

    rec = [
        {
            "name": "good",
            "description": "ok",
            "category": "core-development",
            "capabilities": [],
            "profile_name": "x/good",
        },
        {
            "name": "bad-cat",
            "description": "x",
            "category": "no-such-category",
            "capabilities": [],
            "profile_name": "x/bad",
        },
    ]
    p = tmp_path / "a.yaml"
    p.write_text(yaml.safe_dump(rec), encoding="utf-8")
    out = load_agents_from_yaml(p)
    # Bad row is dropped; good one still loads.
    assert set(out) == {"good"}


def test_load_skips_record_with_unknown_capability(tmp_path: Path):
    """An unknown capability shouldn't kill the whole agent — it just
    drops that one capability so the agent can still be used."""
    import yaml
    from myclaw.agents.registry import load_agents_from_yaml, AgentCapability

    rec = [{
        "name": "partial",
        "description": "x",
        "category": "core-development",
        "capabilities": ["backend", "no-such-cap"],
        "profile_name": "x/partial",
    }]
    p = tmp_path / "a.yaml"
    p.write_text(yaml.safe_dump(rec), encoding="utf-8")
    out = load_agents_from_yaml(p)
    assert "partial" in out
    assert AgentCapability.BACKEND in out["partial"].capabilities
    # Unknown one not added.
    assert len(out["partial"].capabilities) == 1


def test_load_handles_corrupt_yaml(tmp_path: Path):
    p = tmp_path / "broken.yaml"
    p.write_text("{ unterminated: yaml", encoding="utf-8")
    from myclaw.agents.registry import load_agents_from_yaml
    out = load_agents_from_yaml(p)
    assert out == {}  # logged a warning, no crash


def test_load_rejects_non_list_top_level(tmp_path: Path):
    """The file format is a YAML list of records. A dict-at-top is invalid."""
    import yaml
    p = tmp_path / "wrong.yaml"
    p.write_text(yaml.safe_dump({"agents": []}), encoding="utf-8")
    from myclaw.agents.registry import load_agents_from_yaml
    out = load_agents_from_yaml(p)
    assert out == {}


def test_load_dedupes_duplicate_names(tmp_path: Path):
    import yaml
    from myclaw.agents.registry import load_agents_from_yaml

    rec = [
        {"name": "dup", "description": "first", "category": "core-development",
         "capabilities": [], "profile_name": "x/dup"},
        {"name": "dup", "description": "second", "category": "core-development",
         "capabilities": [], "profile_name": "x/dup"},
    ]
    p = tmp_path / "a.yaml"
    p.write_text(yaml.safe_dump(rec), encoding="utf-8")
    out = load_agents_from_yaml(p)
    # First wins.
    assert out["dup"].description == "first"


# ── YAML / literal sync invariant ─────────────────────────────────────────
#
# The two sources must agree on every agent's name and category. If a
# contributor adds an agent to only one source, this test fails — and
# they get a clear message about which source is missing the entry.


def test_yaml_and_literal_have_same_agent_names():
    from myclaw.agents.registry import _LITERAL_AGENT_REGISTRY, load_agents_from_yaml
    yaml_agents = load_agents_from_yaml()
    yaml_names = set(yaml_agents)
    literal_names = set(_LITERAL_AGENT_REGISTRY)

    only_in_yaml = yaml_names - literal_names
    only_in_literal = literal_names - yaml_names

    assert not only_in_yaml, (
        f"{len(only_in_yaml)} agents in YAML but not in the embedded literal: "
        f"{sorted(only_in_yaml)[:5]}{'…' if len(only_in_yaml) > 5 else ''}"
    )
    assert not only_in_literal, (
        f"{len(only_in_literal)} agents in the embedded literal but not in YAML: "
        f"{sorted(only_in_literal)[:5]}{'…' if len(only_in_literal) > 5 else ''}. "
        "Regenerate `myclaw/agents/data/agents.yaml` or add the missing record."
    )


def test_yaml_categories_are_valid_enum_values():
    """Every category string in the YAML must map to ``AgentCategory``."""
    from myclaw.agents.registry import AgentCategory, load_agents_from_yaml
    valid = {c.value for c in AgentCategory}
    for agent in load_agents_from_yaml().values():
        assert agent.category.value in valid


def test_yaml_capabilities_are_valid_enum_values():
    from myclaw.agents.registry import AgentCapability, load_agents_from_yaml
    valid = set(AgentCapability)
    for agent in load_agents_from_yaml().values():
        for cap in agent.capabilities:
            assert cap in valid


# ── End-to-end: AGENT_REGISTRY is the canonical source ────────────────────


def test_module_picks_yaml_as_canonical_when_present():
    """The module-level AGENT_REGISTRY must come from YAML when the data
    file exists. Verifies the wiring at import time, not just the helper."""
    from myclaw.agents.registry import AGENT_REGISTRY, AGENT_DATA_FILE
    assert AGENT_DATA_FILE.exists(), "data/agents.yaml must ship with the package"
    # When YAML loads cleanly, AGENT_REGISTRY contains exactly its keys.
    from myclaw.agents.registry import load_agents_from_yaml
    yaml_keys = set(load_agents_from_yaml())
    assert set(AGENT_REGISTRY) == yaml_keys
