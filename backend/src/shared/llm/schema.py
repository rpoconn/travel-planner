"""Turn Pydantic models into JSON schemas that small local models handle well.

These schemas are for *prompting*, not validation: tool schemas are placed straight into
the prompt on every agent round, so they're trimmed to what helps the model fill them in.
Arguments are still validated against the full Pydantic models, and validation errors are
fed back to the model.
"""

from typing import Any

from pydantic import BaseModel

# Keywords that only constrain values (the model rarely hits these limits, and validation
# reports them if it does), so they cost prompt tokens without helping.
_VALIDATION_ONLY = frozenset(
    {
        "maxLength",
        "minLength",
        "maximum",
        "minimum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "pattern",
        "maxItems",
        "minItems",
        "format",
    }
)
_NULL = {"type": "null"}


# ---------------------------------------------------------------------------
# $ref inlining
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Simplification
# ---------------------------------------------------------------------------


def _collapse_any_of(node: dict[str, Any]) -> dict[str, Any]:
    branches: list[Any] = node["anyOf"]
    # `X | None` → X: optionality is already expressed by `required`, and tools treat a
    # null argument as "not sent" anyway.
    rest = [branch for branch in branches if branch != _NULL]
    # Pydantic renders Decimal as number | string; ask the model for a number.
    if {branch.get("type") for branch in rest} == {"number", "string"}:
        rest = [branch for branch in rest if branch.get("type") == "number"]
    if len(rest) == len(branches):
        return node
    parent = {key: value for key, value in node.items() if key != "anyOf"}
    if len(rest) != 1:
        return {**parent, "anyOf": rest}
    # The parent's keys (description, a meaningful default) win over the branch's.
    return {**rest[0], **parent}


def _simplify(node: Any, *, in_properties: bool = False) -> Any:
    if isinstance(node, list):
        return [_simplify(value) for value in node]
    if not isinstance(node, dict):
        return node
    if in_properties:
        # Keys here are field names, not keywords; a field may be called "format".
        return {key: _simplify(value) for key, value in node.items()}
    if "anyOf" in node:
        node = _collapse_any_of(node)
    return {
        key: _simplify(value, in_properties=key == "properties")
        for key, value in node.items()
        if key not in _VALIDATION_ONLY
        # An empty default says nothing the model needs; "USD" or "economy" does.
        and not (key == "default" and value in (None, ""))
    }


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """`model`'s JSON schema, flattened and trimmed for use in a prompt.

    Every `$ref` is inlined (models follow `$ref`s unreliably), and titles, null branches,
    empty defaults and validation-only keywords are removed. Not for validation.
    """
    schema = model.model_json_schema()
    defs: dict[str, Any] = schema.pop("$defs", {})
    simplified: dict[str, Any] = _simplify(_resolve(schema, defs))
    return simplified
