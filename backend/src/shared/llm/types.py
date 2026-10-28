"""Provider-neutral chat types and the `LLMClient` protocol.

Features talk to the model only through these types, so the Ollama client and the
`FakeLLM` used by tests / offline mode are interchangeable.
"""

from collections.abc import AsyncIterator, Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

LLMRole = Literal["system", "user", "assistant", "tool"]


class LLMToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class LLMMessage(BaseModel):
    role: LLMRole
    content: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)
    tool_name: str | None = None  # set on role="tool" results


class ToolSpec(BaseModel):
    """A function the model may call; `parameters` is a self-contained JSON schema."""

    name: str
    description: str
    parameters: dict[str, Any]


class LLMChunk(BaseModel):
    """One streamed piece of an assistant reply. Tool calls usually arrive in a single
    chunk at the end, but consumers should accumulate them across chunks."""

    content: str = ""
    tool_calls: list[LLMToolCall] = Field(default_factory=list)


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
        format: dict[str, Any] | None = None,
    ) -> LLMMessage:
        """One non-streaming completion. `format` constrains the output to a JSON schema."""
        ...

    def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """Stream a completion chunk by chunk."""
        ...

    async def aclose(self) -> None: ...
