"""Deterministic `LLMClient` for tests and `LLM_PROVIDER=fake` (offline dev / E2E).

Replies are served from a queue in call order (both `chat` and `stream` consume it); when
the queue is empty the optional `fallback` builds one from the request. Every request is
recorded in `requests` so tests can assert on prompts, tools and formats.
"""

import re
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, JsonValue
from pydantic_core import to_json

from shared.llm.types import LLMChunk, LLMError, LLMMessage, LLMToolCall, ToolSpec

# ---------------------------------------------------------------------------
# Request record + reply builders
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeRequest:
    messages: list[LLMMessage]
    tools: list[ToolSpec]
    format: dict[str, JsonValue] | None
    streamed: bool
    think: bool | None = None  # the per-call override `chat` was given


FakeReply = LLMMessage | LLMError
FakeFallback = Callable[[FakeRequest], LLMMessage]


def text(content: str) -> LLMMessage:
    return LLMMessage(role="assistant", content=content)


def tool_calls(*calls: tuple[str, Mapping[str, JsonValue]], content: str = "") -> LLMMessage:
    return LLMMessage(
        role="assistant",
        content=content,
        tool_calls=[LLMToolCall(name=name, arguments=dict(arguments)) for name, arguments in calls],
    )


def json_reply(value: BaseModel | Mapping[str, JsonValue] | str) -> LLMMessage:
    """A structured-output reply; pass a raw string to simulate malformed JSON."""
    if isinstance(value, BaseModel):
        return text(value.model_dump_json())
    if isinstance(value, Mapping):
        return text(to_json(value).decode())
    return text(value)


# Keeps whitespace attached to the preceding word so chunks concatenate back exactly.
_TOKEN = re.compile(r"\s*\S+\s*|\s+")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class FakeLLM:
    def __init__(
        self, replies: Iterable[FakeReply] = (), *, fallback: FakeFallback | None = None
    ) -> None:
        self._replies: deque[FakeReply] = deque(replies)
        self._fallback = fallback
        self.requests: list[FakeRequest] = []

    def queue(self, *replies: FakeReply) -> None:
        self._replies.extend(replies)

    @property
    def pending(self) -> int:
        return len(self._replies)

    def _next(self, request: FakeRequest) -> LLMMessage:
        self.requests.append(request)
        if self._replies:
            reply = self._replies.popleft()
            if isinstance(reply, LLMError):
                raise reply
            return reply
        if self._fallback is not None:
            return self._fallback(request)
        raise RuntimeError("FakeLLM has no scripted reply left for this request")

    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        format: dict[str, JsonValue] | None = None,
        think: bool | None = None,
    ) -> LLMMessage:
        request = FakeRequest(list(messages), list(tools or []), format, False, think)
        return self._next(request)

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        think: bool | None = None,
    ) -> AsyncIterator[LLMChunk]:
        request = FakeRequest(list(messages), list(tools or []), None, True, think)
        reply = self._next(request)
        for token in _TOKEN.findall(reply.content):
            yield LLMChunk(content=token)
        if reply.tool_calls:
            yield LLMChunk(tool_calls=reply.tool_calls, provider_content=reply.provider_content)

    async def aclose(self) -> None:
        return None
