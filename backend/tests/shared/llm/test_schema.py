from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, JsonValue

from shared.domain import Money, Place
from shared.llm.schema import inline_schema
from tests.json_path import at, obj


class _Args(BaseModel):
    title: str
    origin: Place
    stops: list[Place] = []


class _Optionals(BaseModel):
    note: str | None = Field(default=None, max_length=50, description="A short note")
    cabin: Literal["economy", "business"] = "economy"
    cost: Money | None = None
    format: str = ""


def _has_key(node: JsonValue, key: str) -> bool:
    if isinstance(node, dict):
        return key in node or any(_has_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_has_key(value, key) for value in node)
    return False


def test_inlines_refs_and_drops_defs() -> None:
    schema = inline_schema(_Args)

    assert not _has_key(schema, "$ref")
    assert "$defs" not in schema
    iata = obj(schema, "properties", "origin", "properties", "iata")
    assert iata["type"] == "string" and "anyOf" not in iata
    assert at(schema, "properties", "stops", "items", "required") == ["name"]


def test_drops_title_keywords_but_keeps_a_property_named_title() -> None:
    schema = inline_schema(_Args)

    assert "title" not in schema
    assert "title" not in obj(schema, "properties", "origin")
    assert at(schema, "properties", "title") == {"type": "string"}
    assert schema["required"] == ["title", "origin"]


def test_optional_field_becomes_plain_type_and_keeps_description() -> None:
    schema = inline_schema(_Optionals)

    assert at(schema, "properties", "note") == {"type": "string", "description": "A short note"}
    assert not _has_key(schema, "anyOf")
    assert "required" not in schema


def test_keeps_meaningful_defaults_and_drops_empty_ones() -> None:
    properties = inline_schema(_Optionals)["properties"]

    assert at(properties, "cabin", "default") == "economy"
    assert at(properties, "cabin", "enum") == ["economy", "business"]
    assert at(properties, "cost", "properties", "currency", "default") == "USD"
    # A property *named* like a dropped keyword survives; its empty default doesn't.
    assert at(properties, "format") == {"type": "string"}


def test_decimal_amount_is_a_number() -> None:
    cost = obj(inline_schema(_Optionals), "properties", "cost")

    assert cost["type"] == "object"
    assert at(cost, "properties", "amount", "type") == "number"
    assert "anyOf" not in obj(cost, "properties", "amount")
    assert cost["required"] == ["amount"]


def test_drops_size_limits_but_keeps_shape_hints() -> None:
    schema = inline_schema(_Args)

    for keyword in ("maxLength", "minLength", "maximum", "minimum"):
        assert not _has_key(schema, keyword), keyword
    # `pattern` and `format` tell the model what a string looks like, so they stay.
    assert at(schema, "properties", "origin", "properties", "iata", "pattern") == "^[A-Z]{3}$"


class _Cat(BaseModel):
    kind: Literal["cat"] = "cat"
    lives: int


class _Dog(BaseModel):
    kind: Literal["dog"] = "dog"


class _Pets(BaseModel):
    pets: list[Annotated[_Cat | _Dog, Field(discriminator="kind")]]


def test_a_tagged_union_is_a_plain_any_of_whose_tag_is_required() -> None:
    items = obj(inline_schema(_Pets), "properties", "pets", "items")

    assert not _has_key(items, "oneOf") and not _has_key(items, "discriminator")
    branches = at(items, "anyOf")
    assert isinstance(branches, list)
    cat, dog = branches
    assert at(cat, "properties", "kind", "const") == "cat"
    assert at(cat, "required") == ["kind", "lives"]
    assert at(dog, "required") == ["kind"]


def test_keeps_date_time_format() -> None:
    class _Timed(BaseModel):
        start: datetime

    assert at(inline_schema(_Timed), "properties", "start") == {
        "type": "string",
        "format": "date-time",
    }
