"""ASGI middleware shared by every route."""

from fastapi import HTTPException, status
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from shared.errors import ErrorResponse


class BodySizeLimitMiddleware:
    """Reject request bodies larger than `max_bytes` with 413.

    A declared `Content-Length` is checked up front. Bodies without one (chunked uploads)
    are counted as they're read; going over raises an `HTTPException` from `receive`, which
    FastAPI re-raises from body parsing and its exception handler turns into the 413.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    def _too_large(self) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Request body is larger than {self.max_bytes} bytes",
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        declared = headers.get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise self._too_large()
            return message

        await self.app(scope, counting_receive, send)

    async def _reject(self, send: Send) -> None:
        body = ErrorResponse(detail=self._too_large().detail).model_dump_json().encode()
        await send(
            {
                "type": "http.response.start",
                "status": status.HTTP_413_CONTENT_TOO_LARGE,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
