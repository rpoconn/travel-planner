"""Reading into nested JSON in tests without `Any`.

App code types JSON as `pydantic.JsonValue`, so `arguments["from_place"]["iata"]` doesn't
type-check: each level could be any JSON value. `at` walks a path and fails the test with
a clear message when a step isn't the object or array it should be.
"""

from pydantic import JsonValue, TypeAdapter

_JSON_OBJECT = TypeAdapter(dict[str, JsonValue])

# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def json_object(raw: str | bytes) -> dict[str, JsonValue]:
    """`raw` parsed as a JSON object (fails if it's any other JSON value)."""
    return _JSON_OBJECT.validate_json(raw)


# ---------------------------------------------------------------------------
# Path lookup
# ---------------------------------------------------------------------------


def at(value: JsonValue, *path: str | int) -> JsonValue:
    """The value at `path` in `value`: str steps index objects, int steps index arrays."""
    for depth, step in enumerate(path):
        where = "/".join(str(part) for part in path[:depth]) or "the root"
        if isinstance(step, str):
            assert isinstance(value, dict), f"expected an object at {where}, got {value!r}"
            assert step in value, f"no {step!r} at {where}; keys: {sorted(value)}"
            value = value[step]
        else:
            assert isinstance(value, list), f"expected an array at {where}, got {value!r}"
            value = value[step]
    return value


def obj(value: JsonValue, *path: str | int) -> dict[str, JsonValue]:
    """`at(value, *path)`, which must be a JSON object."""
    found = at(value, *path)
    assert isinstance(found, dict), f"expected an object, got {found!r}"
    return found


def text(value: JsonValue, *path: str | int) -> str:
    """`at(value, *path)`, which must be a JSON string."""
    found = at(value, *path)
    assert isinstance(found, str), f"expected a string, got {found!r}"
    return found
