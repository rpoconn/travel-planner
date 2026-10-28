"""Turn Pydantic models into JSON schemas that small local models handle well."""

from typing import Any

from pydantic import BaseModel


def _resolve(node: Any, defs: dict[str, Any], *, in_properties: bool = False) -> Any:
    if isinstance(node, list):
        return [_resolve(value, defs) for value in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = _resolve(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        return {**target, **_resolve(siblings, defs)}
    return {
        key: _resolve(value, defs, in_properties=key == "properties")
        for key, value in node.items()
        # Drop auto-generated "title" keywords (noise in the prompt), but keep a property
        # that happens to be *named* "title".
        if in_properties or not (key == "title" and isinstance(value, str))
    }


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s validation JSON schema with every `$ref` inlined and titles removed.

    Models follow `$ref`s unreliably, and tool schemas are placed straight into the
    prompt, so a flat, self-contained schema works better than `$defs`.
    """
    schema = model.model_json_schema()
    defs: dict[str, Any] = schema.pop("$defs", {})
    resolved: dict[str, Any] = _resolve(schema, defs)
    return resolved
