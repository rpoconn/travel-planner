"""The planning flow's transcript messages, its free-text request, and the server-sent events
a planning turn streams.

Kept apart from `planning.schemas` because these use the chat message types (`chat.schemas`).
"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from features.chat.schemas import MessageContent, MessageKind, MessageRole
from features.planning.schemas import MessagePayload, TravelPlan

# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------


class PlanningMessage(BaseModel):
    """One transcript entry. `payload` holds the form, choices or answer it showed."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    plan_id: UUID
    role: MessageRole
    kind: MessageKind
    content: str
    payload: MessagePayload | None
    created_at: datetime


class PlanningText(BaseModel):
    """Free text typed into the composer (`POST /plans/{id}/planning/chat`)."""

    content: MessageContent


class ItemRemoval(BaseModel):
    """An item's Remove button (`POST /plans/{id}/planning/removals`)."""

    item_id: UUID


class ContinueRequest(BaseModel):
    """`POST /plans/{id}/planning/continue`. With `refresh`, the pending choices are
    searched again for other options ("More options"); without it (or with no body) the
    planner just carries on."""

    refresh: bool = False


# ---------------------------------------------------------------------------
# Server-sent events: each event's `data:` is one of these as JSON, told apart by `type`.
# ---------------------------------------------------------------------------


class _Event(BaseModel):
    # `type` has a default for convenience but is always sent: keep it required in the spec.
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class PlanningStatusEvent(_Event):
    """Something slow is running, e.g. "Finding flights out…". Not stored."""

    type: Literal["status"] = "status"
    text: str


class PlanningTokenEvent(_Event):
    """Part of the assistant's streamed answer to a question; the stored message follows."""

    type: Literal["token"] = "token"
    text: str


class PlanningMessageEvent(_Event):
    """A message was stored: the user's input, the assistant's reply or nudge, the next
    form or choices, a stage divider. Clients append it to the transcript."""

    type: Literal["message"] = "message"
    message: PlanningMessage


class PlanningPlanEvent(_Event):
    """The plan changed (an answer, a pick, a stage move)."""

    type: Literal["plan_updated"] = "plan_updated"
    plan: TravelPlan


class PlanningDoneEvent(_Event):
    """The turn finished; `plan` (with its assessment) is what the UI shows next."""

    type: Literal["done"] = "done"
    plan: TravelPlan


class PlanningErrorEvent(_Event):
    """The turn failed; `message` is the stored notice, when it could be saved."""

    type: Literal["error"] = "error"
    detail: str
    message: PlanningMessage | None = None


PlanningEvent = Annotated[
    PlanningStatusEvent
    | PlanningTokenEvent
    | PlanningMessageEvent
    | PlanningPlanEvent
    | PlanningDoneEvent
    | PlanningErrorEvent,
    Field(discriminator="type"),
]
