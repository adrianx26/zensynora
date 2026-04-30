"""Tests for the structured-output validator and JSON repair loop."""

import pytest
from pydantic import BaseModel

from myclaw.structured_output import (
    extract_json,
    repair_json,
    schema_for,
    validate_json,
)


# ── extract_json ──────────────────────────────────────────────────────────


def test_extract_plain_object():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extract_with_prose():
    out = extract_json('Sure! Here is the JSON: {"a": 1, "b": 2}. Hope that helps!')
    assert out == '{"a": 1, "b": 2}'


def test_extract_from_code_fence():
    text = "```json\n{\"a\": 1}\n```"
    assert extract_json(text).strip() == '{"a": 1}'


def test_extract_handles_nested_braces():
    text = 'noise {"outer": {"inner": [1, 2, 3]}} trailing'
    assert extract_json(text) == '{"outer": {"inner": [1, 2, 3]}}'


def test_extract_ignores_braces_in_strings():
    text = '{"k": "value with } brace"}'
    assert extract_json(text) == text


def test_extract_returns_none_when_absent():
    assert extract_json("just prose, no json here") is None
    assert extract_json("") is None


def test_extract_array_root():
    assert extract_json("[1, 2, {\"a\": 3}]") == "[1, 2, {\"a\": 3}]"


# ── validate_json with Pydantic ───────────────────────────────────────────


class Item(BaseModel):
    name: str
    qty: int


def test_validate_pydantic_success():
    result = validate_json('{"name": "widget", "qty": 3}', Item)
    assert result.ok
    assert isinstance(result.data, Item)
    assert result.data.qty == 3


def test_validate_pydantic_type_failure():
    result = validate_json('{"name": "widget", "qty": "three"}', Item)
    assert not result.ok
    assert any("qty" in err for err in result.errors)


def test_validate_pydantic_missing_field():
    result = validate_json('{"name": "widget"}', Item)
    assert not result.ok


def test_validate_pydantic_with_surrounding_prose():
    """The validator should extract JSON before parsing."""
    text = "Here's your item: {\"name\": \"widget\", \"qty\": 5}"
    result = validate_json(text, Item)
    assert result.ok
    assert result.data.qty == 5


def test_validate_no_json_in_input():
    result = validate_json("totally not JSON", Item)
    assert not result.ok
    assert any("No balanced JSON" in e for e in result.errors)


def test_validate_invalid_json_syntax():
    result = validate_json('{"name": "widget", "qty": 3,}', Item)  # trailing comma
    assert not result.ok


# ── schema_for ────────────────────────────────────────────────────────────


def test_schema_for_pydantic_model():
    schema = schema_for(Item)
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "qty" in schema["properties"]


def test_schema_for_rejects_non_pydantic():
    with pytest.raises(TypeError):
        schema_for(dict)


# ── validate_json with JSON-schema dict (jsonschema dep) ──────────────────


def test_validate_with_jsonschema_dict():
    pytest.importorskip("jsonschema")
    schema = {
        "type": "object",
        "properties": {"x": {"type": "integer"}},
        "required": ["x"],
    }
    assert validate_json('{"x": 5}', schema).ok
    assert not validate_json('{"x": "five"}', schema).ok


# ── repair_json ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_repair_succeeds_when_input_already_valid():
    async def llm(_messages):  # should never be called
        raise AssertionError("repair should not call LLM when input is valid")

    result = await repair_json('{"name": "ok", "qty": 1}', Item, llm)
    assert result.ok
    assert result.data.name == "ok"


@pytest.mark.asyncio
async def test_repair_fixes_invalid_via_llm():
    calls = {"n": 0}

    async def llm(messages):
        calls["n"] += 1
        # Pretend the model returns a fixed payload.
        return '{"name": "fixed", "qty": 7}'

    # Bad input: qty is wrong type.
    result = await repair_json('{"name": "fixed", "qty": "seven"}', Item, llm)
    assert calls["n"] == 1
    assert result.ok
    assert result.data.qty == 7


@pytest.mark.asyncio
async def test_repair_gives_up_after_max_attempts():
    async def llm(_messages):
        return '{"name": "still bad", "qty": "still bad"}'

    result = await repair_json('{"qty": "bad"}', Item, llm, max_attempts=2)
    assert not result.ok
    assert result.errors


@pytest.mark.asyncio
async def test_repair_handles_llm_call_failure():
    async def llm(_messages):
        raise RuntimeError("rate limited")

    result = await repair_json('{"qty": "bad"}', Item, llm)
    assert not result.ok
    assert any("Repair call raised" in e for e in result.errors)
