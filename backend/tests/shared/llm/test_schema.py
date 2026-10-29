from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from shared.domain import Money, Place
from shared.llm.schema import inline_schema


class _Args(BaseModel):
    title: str
    origin: Place
    stops: list[Place] = []


class _Optionals(BaseModel):
    note: str | None = Field(default=None, max_length=50, description="A short note")
    cabin: Literal["economy", "business"] = "economy"
    cost: Money | None = None
    format: str = ""


def _has_key(node: Any, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_has_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_has_key(value, key) for value in node)
    return False


def test_inlines_refs_and_drops_defs() -> None:
    schema = inline_schema(_Args)

    assert not _has_key(schema, "$ref")
    assert "$defs" not in schema
    iata = schema["properties"]["origin"]["properties"]["iata"]
    assert iata["type"] == "string" and "anyOf" not in iata
    assert schema["properties"]["stops"]["items"]["required"] == ["name"]


def test_drops_title_keywords_but_keeps_a_property_named_title() -> None:
    schema = inline_schema(_Args)

    assert "title" not in schema
    assert "title" not in schema["properties"]["origin"]
    assert schema["properties"]["title"] == {"type": "string"}
    assert schema["required"] == ["title", "origin"]


def test_optional_field_becomes_plain_type_and_keeps_description() -> None:
    schema = inline_schema(_Optionals)

    assert schema["properties"]["note"] == {"type": "string", "description": "A short note"}
    assert not _has_key(schema, "anyOf")
    assert "required" not in schema


def test_keeps_meaningful_defaults_and_drops_empty_ones() -> None:
    properties = inline_schema(_Optionals)["properties"]

    assert properties["cabin"]["default"] == "economy"
    assert properties["cabin"]["enum"] == ["economy", "business"]
    assert properties["cost"]["properties"]["currency"]["default"] == "USD"
    # A property *named* like a dropped keyword survives; its empty default doesn't.
    assert properties["format"] == {"type": "string"}


def test_decimal_amount_is_a_number() -> None:
    cost = inline_schema(_Optionals)["properties"]["cost"]

    assert cost["type"] == "object"
    assert cost["properties"]["amount"]["type"] == "number"
    assert "anyOf" not in cost["properties"]["amount"]
    assert cost["required"] == ["amount"]


def test_drops_size_limits_but_keeps_shape_hints() -> None:
    schema = inline_schema(_Args)

    for keyword in ("maxLength", "minLength", "maximum", "minimum"):
        assert not _has_key(schema, keyword), keyword
    # `pattern` and `format` tell the model what a string looks like, so they stay.
    assert schema["properties"]["origin"]["properties"]["iata"]["pattern"] == "^[A-Z]{3}$"


def test_keeps_date_time_format() -> None:
    class _Timed(BaseModel):
        start: datetime

    assert inline_schema(_Timed)["properties"]["start"] == {
        "type": "string",
        "format": "date-time",
    }
