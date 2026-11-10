"""Provider-neutral chat types and the `LLMClient` protocol.

Features talk to the model only through these types, so the Ollama client and the
`FakeLLM` used by tests / offline mode are interchangeable.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Literal, Protocol

from pydantic import BaseModel, Field, JsonValue

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

LLMRole = Literal["system", "user", "assistant", "tool"]


class LLMToolCall(BaseModel):
    # The provider's id for this call, echoed on its result (`LLMMessage.tool_call_id`).
    # None from providers that match results by order (Ollama) and in hand-built messages.
    id: str | None = None
    name: str
    arguments: dict[str, JsonValue] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    role: LLMRole
    content: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    tool_name: str | None = None  # set on role="tool" results
    tool_call_id: str | None = None  # role="tool": the `LLMToolCall.id` this answers
    # Assistant replies that call tools: the provider's own content blocks, in order,
    # which a tool loop must send back unchanged on the next round (Claude rejects a tool
    # result whose assistant turn lost its thinking blocks). Opaque to features: append the
    # reply to the history as it is. Empty for plain replies and for other providers.
    provider_content: list[dict[str, JsonValue]] = Field(default_factory=list)


class ToolSpec(BaseModel):
    """A function the model may call; `parameters` is a self-contained JSON schema."""

    name: str
    description: str
    parameters: dict[str, JsonValue]


class LLMChunk(BaseModel):
    """One streamed piece of an assistant reply. Tool calls usually arrive in a single
    chunk at the end, but consumers should accumulate them across chunks."""

    content: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    # Sent with the tool calls; see `LLMMessage.provider_content`.
    provider_content: list[dict[str, JsonValue]] = Field(default_factory=list)


def add_chunk(reply: LLMMessage, chunk: LLMChunk) -> None:
    """Fold one streamed chunk into `reply`, so that after the last chunk it is the whole
    assistant message, ready to append to a tool loop's history."""
    reply.content += chunk.content
    reply.tool_calls.extend(chunk.tool_calls)
    reply.provider_content.extend(chunk.provider_content)


# ---------------------------------------------------------------------------
# Client protocol
# ---------------------------------------------------------------------------


class LLMError(Exception):
    """The model could not be reached or failed after retries. The message is safe to
    show to the user."""


class LLMClient(Protocol):
    async def chat(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        format: dict[str, JsonValue] | None = None,
        think: bool | None = None,
    ) -> LLMMessage:
        """One non-streaming completion. `format` constrains the output to a JSON schema;
        `think` turns reasoning on or off for this call (None: the client's default)."""
        ...

    def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        think: bool | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a completion chunk by chunk; `think` as for `chat`."""
        ...

    async def aclose(self) -> None: ...
