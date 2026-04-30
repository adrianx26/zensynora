"""Smoke tests for the agent registry.

The registry holds 136+ agent definitions as Python literals; a typo in any
of them silently breaks `get_agent`. These tests catch structural drift.
"""

import pytest

from myclaw.agents.registry import (
    AGENT_REGISTRY,
    AgentCategory,
    AgentCapability,
    AgentDefinition,
    get_agent,
    list_agents,
    list_agents_by_category,
    get_agent_count,
    get_categories_with_count,
)


# ── Structural invariants ────────────────────────────────────────────────


def test_registry_is_nonempty():
    assert get_agent_count() >= 1
    assert len(AGENT_REGISTRY) == get_agent_count()


def test_every_entry_is_an_agent_definition():
    for name, agent in AGENT_REGISTRY.items():
        assert isinstance(agent, AgentDefinition), f"{name!r} is not an AgentDefinition"


def test_registry_keys_match_agent_names():
    """The dict key must match the agent's own .name; otherwise lookup
    semantics diverge from what callers expect."""
    for key, agent in AGENT_REGISTRY.items():
        assert key == agent.name, (
            f"Key {key!r} does not match agent.name {agent.name!r}"
        )


def test_no_duplicate_names():
    names = [a.name for a in AGENT_REGISTRY.values()]
    assert len(names) == len(set(names)), "Duplicate agent names in registry"


def test_every_agent_has_a_known_category():
    valid = set(AgentCategory)
    for agent in AGENT_REGISTRY.values():
        assert agent.category in valid, (
            f"{agent.name} has unknown category {agent.category!r}"
        )


# ── Lookup APIs ──────────────────────────────────────────────────────────


def test_get_agent_returns_definition_for_known_name():
    # Use the first registered agent — the test should not hardcode a
    # particular slug, since the catalog evolves.
    first_name = next(iter(AGENT_REGISTRY))
    agent = get_agent(first_name)
    assert agent is not None
    assert agent.name == first_name


def test_get_agent_returns_none_for_unknown_name():
    assert get_agent("nonexistent-agent-xyzzy") is None


def test_list_agents_no_filter_returns_all():
    assert len(list_agents()) == get_agent_count()


def test_list_agents_by_category_filter():
    # Pick a category that actually has agents to make this assertion meaningful.
    counts = {cat: 0 for cat in AgentCategory}
    for a in AGENT_REGISTRY.values():
        counts[a.category] += 1
    populated = [c for c, n in counts.items() if n > 0]
    assert populated, "No populated categories — registry seems empty"

    cat = populated[0]
    filtered = list_agents(category=cat)
    assert all(a.category is cat for a in filtered)
    assert len(filtered) == counts[cat]


def test_list_agents_by_capability_filter():
    """capabilities and tags are separate fields; this exercises the
    `capability` arg of list_agents, which filters on AgentCapability."""
    capable = [a for a in AGENT_REGISTRY.values() if a.capabilities]
    if not capable:
        pytest.skip("No agents declare capabilities")
    sample_cap = next(iter(capable[0].capabilities))
    results = list_agents(capability=sample_cap)
    assert all(sample_cap in a.capabilities for a in results)
    assert len(results) >= 1


def test_list_agents_by_tag_filter():
    """tags are free-form strings on AgentDefinition, separate from capabilities."""
    tagged = [a for a in AGENT_REGISTRY.values() if a.tags]
    if not tagged:
        pytest.skip("No agents declare free-form tags")
    sample_tag = next(iter(tagged[0].tags))
    results = list_agents(tags=[sample_tag])
    assert all(sample_tag in a.tags for a in results)
    assert len(results) >= 1


def test_list_agents_by_category_grouping_total_matches_registry():
    grouped = list_agents_by_category()
    total = sum(len(v) for v in grouped.values())
    assert total == get_agent_count()


def test_categories_with_count_sums_to_registry_size():
    rows = get_categories_with_count()
    assert sum(n for _, n in rows) == get_agent_count()


# ── Capability & description invariants ──────────────────────────────────


@pytest.mark.parametrize("agent", list(AGENT_REGISTRY.values())[:50])
def test_agent_has_nonempty_description(agent):
    """Sample 50 agents to keep the test fast; a typo'd registry usually
    fails on the first few, so full coverage isn't necessary here."""
    assert agent.description and isinstance(agent.description, str)
    assert len(agent.description.strip()) > 0


def test_capabilities_are_capability_enum_members():
    valid = set(AgentCapability)
    for agent in AGENT_REGISTRY.values():
        for cap in agent.capabilities:
            assert cap in valid, (
                f"{agent.name} declares unknown capability {cap!r}"
            )
