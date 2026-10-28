from typing import Any

from pydantic import BaseModel

from shared.domain import Place
from shared.llm.schema import inline_schema


class _Args(BaseModel):
    title: str
    origin: Place
    stops: list[Place] = []


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
    assert schema["properties"]["origin"]["properties"]["iata"]["anyOf"][0]["pattern"]
    assert schema["properties"]["stops"]["items"]["required"] == ["name"]


def test_drops_title_keywords_but_keeps_a_property_named_title() -> None:
    schema = inline_schema(_Args)

    assert "title" not in schema
    assert "title" not in schema["properties"]["origin"]
    assert schema["properties"]["title"] == {"type": "string"}
    assert schema["required"] == ["title", "origin"]
