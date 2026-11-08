"""Turn Pydantic models into JSON schemas that small local models handle well.

These schemas are for *prompting*, not validation: a structured-output schema is sent with
every extraction call, so it's trimmed to what helps the model fill it in. Replies are
still validated against the full Pydantic models.
"""

from pydantic import BaseModel, JsonValue

# Keywords that only constrain sizes (the model rarely hits these limits, and validation
# reports them if it does), so they cost prompt tokens without helping. `format` (date-time)
# and `pattern` (an IATA code, a currency) are kept: they tell the model what shape a string
# takes, and without them it guessed (dates as "next tuesday", prices as "1,380").
_VALIDATION_ONLY = frozenset(
    {
        "maxLength",
        "minLength",
        "maximum",
        "minimum",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxItems",
        "minItems",
    }
)
_NULL = {"type": "null"}


# ---------------------------------------------------------------------------
# $ref inlining
# ---------------------------------------------------------------------------

JsonObject = dict[str, JsonValue]


def _resolve(node: JsonValue, defs: JsonObject, *, in_properties: bool = False) -> JsonValue:
    if isinstance(node, list):
        return [_resolve(value, defs) for value in node]
    if isinstance(node, dict):
        return _resolve_object(node, defs, in_properties=in_properties)
    return node


def _resolve_object(node: JsonObject, defs: JsonObject, *, in_properties: bool) -> JsonObject:
    ref = node.get("$ref")
    if isinstance(ref, str):
        target = defs[ref.rsplit("/", 1)[-1]]
        if not isinstance(target, dict):
            raise ValueError(f"$ref {ref!r} does not point at a schema object")
        siblings = {key: value for key, value in node.items() if key != "$ref"}
        return {
            **_resolve_object(target, defs, in_properties=False),
            **_resolve_object(siblings, defs, in_properties=False),
        }
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


def _type_of(branch: JsonValue) -> JsonValue:
    return branch.get("type") if isinstance(branch, dict) else None


def _collapse_any_of(node: JsonObject) -> JsonObject:
    branches = node["anyOf"]
    if not isinstance(branches, list):
        return node
    # `X | None` → X: optionality is already expressed by `required`, and tools treat a
    # null argument as "not sent" anyway.
    rest = [branch for branch in branches if branch != _NULL]
    # Pydantic renders Decimal as number | string; ask the model for a number.
    if {_type_of(branch) for branch in rest} == {"number", "string"}:
        rest = [branch for branch in rest if _type_of(branch) == "number"]
    if len(rest) == len(branches):
        return node
    parent = {key: value for key, value in node.items() if key != "anyOf"}
    only = rest[0] if len(rest) == 1 else None
    if not isinstance(only, dict):
        return {**parent, "anyOf": rest}
    # The parent's keys (description, a meaningful default) win over the branch's.
    return {**only, **parent}


def _simplify(node: JsonValue, *, in_properties: bool = False) -> JsonValue:
    if isinstance(node, list):
        return [_simplify(value) for value in node]
    if isinstance(node, dict):
        return _simplify_object(node, in_properties=in_properties)
    return node


def _tagged_union(node: JsonObject) -> JsonObject:
    """A Pydantic discriminated union (`oneOf` + `discriminator`) as a plain `anyOf`, which
    every structured-output backend takes (Claude's doesn't take `oneOf`). The tag still
    tells the branches apart: each branch requires its `const` field (`_require_consts`)."""
    rest = {key: value for key, value in node.items() if key not in ("oneOf", "discriminator")}
    return {**rest, "anyOf": node["oneOf"]}


def _require_consts(node: JsonObject) -> JsonObject:
    """Mark every `const` property required. A const has one possible value, so asking for
    it costs nothing, and a tagged union's reply can't be read without its tag (Pydantic
    renders the tag with a default, so it isn't required on its own)."""
    properties = node.get("properties")
    if not isinstance(properties, dict):
        return node
    consts = [
        name for name, field in properties.items() if isinstance(field, dict) and "const" in field
    ]
    required = node.get("required")
    listed = required if isinstance(required, list) else []
    have = [name for name in listed if isinstance(name, str)]
    missing = [name for name in consts if name not in have]
    if not missing:
        return node
    return {**node, "required": [*missing, *have]}


def _simplify_object(node: JsonObject, *, in_properties: bool) -> JsonObject:
    if in_properties:
        # Keys here are field names, not keywords; a field may be called "format".
        return {key: _simplify(value) for key, value in node.items()}
    if "oneOf" in node:
        node = _tagged_union(node)
    if "anyOf" in node:
        node = _collapse_any_of(node)
    node = _require_consts(node)
    return {
        key: _simplify(value, in_properties=key == "properties")
        for key, value in node.items()
        if key not in _VALIDATION_ONLY
        # An empty default says nothing the model needs; "USD" or "economy" does.
        and not (key == "default" and value in (None, ""))
    }


def inline_schema(model: type[BaseModel]) -> JsonObject:
    """`model`'s JSON schema, flattened and trimmed for use in a prompt.

    Every `$ref` is inlined (models follow `$ref`s unreliably), and titles, null branches,
    empty defaults and validation-only keywords are removed. Not for validation.
    """
    schema: JsonObject = model.model_json_schema()
    defs = schema.pop("$defs", {})
    if not isinstance(defs, dict):
        raise ValueError(f"{model.__name__} has a malformed $defs block")
    return _simplify_object(_resolve_object(schema, defs, in_properties=False), in_properties=False)
