"""Transcript message types shared by the planning flow, and the plan change a candidate's
pick runs through `chat.tools`."""

from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

# ---------------------------------------------------------------------------
# Field types
# ---------------------------------------------------------------------------

MessageRole = Literal["user", "assistant", "system"]


class MessageKind(StrEnum):
    """What a transcript message is, so the UI knows how to show it."""

    # Plain text: the user's free text, or the assistant's answer to a question.
    MESSAGE = "message"
    # A stored system notice that a turn failed.
    ERROR = "error"
    # The fixed reply to free text that doesn't answer the pending input, or a pick that
    # doesn't fit.
    NUDGE = "nudge"
    # The user's answer to a form (or a skip).
    INPUT = "input"
    # The user's choice from an offer.
    PICK = "pick"
    # The form shown.
    EXPECTED_INPUT = "expected_input"
    # The candidates shown.
    CHOICES = "choices"
    # Edits the user asked for in their own words, as written (or why none were).
    CHANGES = "changes"


MAX_MESSAGE_CHARS = 4000
MessageContent = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE_CHARS)
]


# ---------------------------------------------------------------------------
# Plan changes
# ---------------------------------------------------------------------------


class ProposedChange(BaseModel):
    """One plan change waiting to be checked or written: a pick's change, with an id."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    call_id: str
    name: str  # the change, e.g. "add_flight"
    arguments: dict[str, JsonValue] = Field(default_factory=dict)
    summary: str  # imperative, e.g. 'Add flight “SFO → NRT” on 2027-05-01 11:00'
