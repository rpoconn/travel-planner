"""Chat API schemas: stored messages, the send-message request, the guardrail verdict, and
the server-sent events streamed while the assistant answers."""

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from features.plans.schemas import TravelPlan

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

MessageRole = Literal["user", "assistant", "system"]
# "redirect": the guardrail judged the user's message off-topic and the assistant steered
# back to trip planning. "error": a stored system notice that answering failed.
MessageKind = Literal["message", "redirect", "error"]

MAX_MESSAGE_CHARS = 4000
MessageContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)
]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class ToolTrace(BaseModel):
    """One tool the assistant ran: what it asked for and how it went."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    call_id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    ok: bool
    summary: str  # short human-readable outcome, e.g. "Added flight SFO → NRT"


class ChatMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    role: MessageRole
    kind: MessageKind
    content: str
    tool_calls: list[ToolTrace]
    created_at: datetime


class SendMessage(BaseModel):
    content: MessageContent


# ---------------------------------------------------------------------------
# Guardrail
# ---------------------------------------------------------------------------


class GuardrailVerdict(BaseModel):
    """Structured output of the topic classifier (also the JSON schema the model must
    follow)."""

    on_topic: bool
    confidence: float = Field(ge=0, le=1)
    topic: str = Field(max_length=200, description="A few words naming what the message is about")


# ---------------------------------------------------------------------------
# Server-sent events: each event's `data:` is one of these as JSON, told apart by `type`.
# (No SSE `event:` name is sent, so clients read every event through `onmessage`.)
# ---------------------------------------------------------------------------


class _Event(BaseModel):
    # `type` has a default for convenience but is always sent, so keep it required in the
    # schema the SDK is generated from.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class GuardrailEvent(_Event):
    type: Literal["guardrail"] = "guardrail"
    on_topic: bool
    topic: str


class TokenEvent(_Event):
    type: Literal["token"] = "token"
    text: str


class ToolCallEvent(_Event):
    type: Literal["tool_call"] = "tool_call"
    call_id: str
    name: str
    arguments: dict[str, Any]


class ToolResultEvent(_Event):
    type: Literal["tool_result"] = "tool_result"
    call_id: str
    name: str
    ok: bool
    summary: str


class PlanUpdatedEvent(_Event):
    type: Literal["plan_updated"] = "plan_updated"
    plan: TravelPlan


class DoneEvent(_Event):
    """The stored assistant reply; always the last event of a successful turn."""

    type: Literal["done"] = "done"
    message: ChatMessage


class ErrorEvent(_Event):
    """Answering failed; `message` is the stored system notice (when it could be saved)."""

    type: Literal["error"] = "error"
    detail: str
    message: ChatMessage | None = None


ChatEvent = Annotated[
    GuardrailEvent
    | TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | PlanUpdatedEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
