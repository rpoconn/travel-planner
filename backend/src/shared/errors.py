"""Shared error response schema so non-2xx responses are typed in the OpenAPI spec / SDK."""

from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Body of every `HTTPException` raised by the API."""

    detail: str


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    """`responses=` helper for route decorators: documents each code as an `ErrorResponse`."""
    return {code: {"model": ErrorResponse} for code in status_codes}
