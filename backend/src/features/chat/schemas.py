"""Chat API schemas: stored messages and proposals, the send-message and decision requests,
the guardrail verdict, and the server-sent events streamed while the assistant answers."""

from datetime import datetime
from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from features.itinerary.schemas import TimelineWarning
from features.plans.schemas import TravelPlan

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

MessageRole = Literal["user", "assistant", "system"]
# "redirect": the guardrail judged the user's message off-topic and the assistant steered
# back to trip planning. "error": a stored system notice that answering failed.
# "decision": the user's answer to a proposal card (approve / deny / free-text revision).
MessageKind = Literal["message", "redirect", "error", "decision"]
# pending: waiting for the user. approved / denied / revised: the user's decision.
# superseded: a newer proposal replaced it before a decision. failed: approved, but a change
# no longer validated against the plan, so nothing was applied.
ProposalStatus = Literal["pending", "approved", "denied", "revised", "superseded", "failed"]
ProposalAction = Literal["approve", "deny", "revise"]

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
    # A plan change that was only staged into the message's proposal, not applied.
    proposed: bool = False


class ProposedChange(BaseModel):
    """One staged plan change: a validated write-tool call waiting for the user."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    call_id: str
    name: str  # the write tool, e.g. "add_flight"
    arguments: dict[str, Any] = Field(default_factory=dict)  # normalised by the tool
    summary: str  # imperative, e.g. 'Add flight “SFO → NRT” on 2027-05-01 11:00'


class ChatProposal(BaseModel):
    """The changes the assistant proposed in one reply, applied all-or-nothing on approval."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    message_id: UUID  # the assistant message that proposed it
    status: ProposalStatus
    changes: list[ProposedChange]
    plan_version: int  # the plan's version when proposed (informational)
    created_at: datetime
    decided_at: datetime | None


class ChatMessage(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    role: MessageRole
    kind: MessageKind
    content: str
    tool_calls: list[ToolTrace]
    proposal: ChatProposal | None  # assistant messages that staged plan changes
    created_at: datetime


class WarningToFix(TimelineWarning):
    """A timeline warning sent back by the client, as it received it. A subclass only so
    it gets its own schema name: using `TimelineWarning` itself as input too would split
    it into `TimelineWarningInput` / `TimelineWarningOutput` in the generated SDK."""


class SendMessage(BaseModel):
    content: MessageContent
    # Set by the itinerary's "Fix it" button: `content` is still the visible, stored text
    # ("Fix: …"), and the warning is handed to the assistant to propose a fix for.
    fix: WarningToFix | None = None


class ProposalDecision(BaseModel):
    """The user's answer to a proposal card. `message` is the free text of "Other…"."""

    action: ProposalAction
    message: MessageContent | None = None

    @model_validator(mode="after")
    def _message_only_to_revise(self) -> Self:
        if (self.action == "revise") != (self.message is not None):
            raise ValueError('"message" is required to revise, and only then')
        return self


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
    proposed: bool = False  # staged into the reply's proposal rather than applied


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
