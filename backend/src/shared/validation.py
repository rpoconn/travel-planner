"""Helpers for presenting Pydantic validation errors to people (and to the model)."""

from pydantic import ValidationError


def summarize_validation_error(exc: ValidationError) -> str:
    """One line per error, e.g. `end: Value error, end must be at or after start`."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        if error["loc"]
        else error["msg"]
        for error in exc.errors()
    )
