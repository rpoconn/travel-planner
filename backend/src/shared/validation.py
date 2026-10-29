"""Helpers for presenting Pydantic validation errors to people (and to the model)."""

from typing import Any

from pydantic import ValidationError
from pydantic_core import ErrorDetails


def _sent_keys(error: ErrorDetails) -> str:
    # For a missing field the error's `input` is the enclosing object, so we can tell the
    # model what it sent instead ("cost.amount: Field required (sent keys: total)"); a
    # small model otherwise retries the same wrong key.
    value: Any = error.get("input")
    if error["type"] != "missing" or not isinstance(value, dict) or not value:
        return ""
    return f" (sent keys: {', '.join(str(key) for key in value)})"


def summarize_validation_error(exc: ValidationError) -> str:
    """One line per error, e.g. `end: Value error, end must be at or after start`."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}{_sent_keys(error)}"
        if error["loc"]
        else error["msg"]
        for error in exc.errors()
    )
