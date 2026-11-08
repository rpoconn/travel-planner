"""The readers: structured model calls that turn free text into typed data. The slot
extractor reads text typed while an input is pending as that slot's value, the pick reader
text typed while choices are pending as the candidates it picks, and the command reader
text that asks to change the trip as commands (`planning.commands` checks and writes them).
Either of the first two can say a message is an edit ("command") and hand it on.

The model is asked for `{status, value}` constrained to a JSON schema built from the slot's
value model, with thinking off. Its reply is parsed in two steps, so a value that has the
right shape but fails the slot's rules ("from May 8 to May 1") is told apart from output
that is malformed. Malformed twice fails closed to `unrelated`, which the caller answers
with the slot's fixed nudge. The command reader works the same way against the closed
command union. This is the whole guardrail: nothing else reads model output.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from typing import Literal

from pydantic import BaseModel, Field, JsonValue, ValidationError, create_model

from features.planning.answers import AnswerError, parse_answer
from features.planning.picks import PickError, picked_candidates
from features.planning.prompts import extractor_messages, pick_messages
from features.planning.schemas import Candidate, Command, CommandReply, CommandStatus, OfferChoice
from features.planning.slots import SLOTS
from features.plans.schemas import PlanSnapshot, SlotName
from shared.llm.schema import inline_schema
from shared.llm.types import LLMClient, LLMMessage

logger = logging.getLogger(__name__)

# Tries before giving up on malformed output (as in the old topic guardrail).
MAX_ATTEMPTS = 2

ExtractionStatus = Literal["answered", "command", "question", "unrelated"]

_STATUS_DESCRIPTION = (
    "answered: the message gives the answer; command: it asks to change the trip instead; "
    "question: it asks something instead; unrelated: anything else"
)


class _RawExtraction(BaseModel):
    """The reply's outer shape. `value` stays a plain dict here and is checked against the
    slot afterwards, so a rule it breaks becomes a message for the user, not a retry."""

    status: ExtractionStatus
    value: dict[str, JsonValue] | None = None


@dataclass(frozen=True)
class Extraction:
    status: ExtractionStatus
    answer: BaseModel | None = None  # answered, and the value fits the slot
    error: str | None = None  # answered, but the value breaks the slot's rules


class PickReply(BaseModel):
    """The pick reader's reply. The ids are checked against the offer afterwards."""

    status: ExtractionStatus = Field(description=_STATUS_DESCRIPTION)
    candidate_ids: list[str] = Field(
        default_factory=list, description="The ids of the options the message picks"
    )


@dataclass(frozen=True)
class PickExtraction:
    status: ExtractionStatus
    candidates: list[Candidate] | None = None  # answered, and the pick fits the offer
    error: str | None = None  # answered, but not with a pick the offer allows


@dataclass(frozen=True)
class CommandReading:
    status: CommandStatus
    commands: list[Command] = field(default_factory=list)  # command: at least one
    clarification: str | None = None  # ambiguous: the question to ask back
    reply: str | None = None  # the reply as read, for a repair pass (`prompts.repair_messages`)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# The pick reader's and the command reader's schemas are the same for every request: the
# options and the itinerary are in the prompt.
PICK_SCHEMA: dict[str, JsonValue] = inline_schema(PickReply)
COMMAND_SCHEMA: dict[str, JsonValue] = inline_schema(CommandReply)


@cache
def extraction_schema(slot: SlotName) -> dict[str, JsonValue]:
    """The JSON schema the model's reply must follow for `slot` (sent as Ollama's `format`)."""
    value_model = SLOTS[slot].value_model
    if value_model is None:
        raise ValueError(f"{slot} is a pick slot; it has no value to extract")
    model = create_model(
        f"{value_model.__name__}Extraction",
        status=(ExtractionStatus, Field(description=_STATUS_DESCRIPTION)),
        # A union built at runtime: pydantic accepts it, but mypy only takes static type forms.
        value=(value_model | None, None),  # type: ignore[arg-type]
    )
    return inline_schema(model)


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


async def extract(
    llm: LLMClient, slot: SlotName, content: str, plan: PlanSnapshot, today: date
) -> Extraction:
    """Read `content` as an answer to `slot`. `LLMError` (model unreachable) propagates."""
    spec = SLOTS[slot]
    messages = extractor_messages(spec, content, plan, today)
    schema = extraction_schema(slot)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = await llm.chat(messages, format=schema, think=False)
        try:
            raw = _RawExtraction.model_validate_json(reply.content)
        except ValidationError as exc:
            logger.warning(
                "Extractor returned malformed output for %s (attempt %d/%d): %s",
                slot,
                attempt,
                MAX_ATTEMPTS,
                exc.errors(include_url=False)[:3],
            )
            continue
        logger.info("Extractor read %s as %s", slot, raw.status)
        if raw.status != "answered":
            return Extraction(status=raw.status)
        if raw.value is None:
            # "Answered" with nothing to show for it: treat like no answer at all.
            return Extraction(status="unrelated")
        try:
            return Extraction(status="answered", answer=parse_answer(slot, raw.value, today))
        except AnswerError as exc:
            return Extraction(status="answered", error=str(exc))

    logger.error("Extractor failed %d times for %s; answering with the nudge", MAX_ATTEMPTS, slot)
    return Extraction(status="unrelated")


async def extract_pick(
    llm: LLMClient, action: OfferChoice, content: str, plan: PlanSnapshot
) -> PickExtraction:
    """Read `content` as a pick from the pending offer ("the second one"). Fails closed to
    `unrelated` like `extract`; `LLMError` propagates."""
    messages = pick_messages(action, content, plan)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = await llm.chat(messages, format=PICK_SCHEMA, think=False)
        try:
            raw = PickReply.model_validate_json(reply.content)
        except ValidationError as exc:
            logger.warning(
                "Pick reader returned malformed output (attempt %d/%d): %s",
                attempt,
                MAX_ATTEMPTS,
                exc.errors(include_url=False)[:3],
            )
            continue
        logger.info("Pick reader read %s as %s", action.slot, raw.status)
        if raw.status != "answered":
            return PickExtraction(status=raw.status)
        if not raw.candidate_ids:
            return PickExtraction(status="unrelated")
        try:
            return PickExtraction(
                status="answered", candidates=picked_candidates(action, raw.candidate_ids)
            )
        except PickError as exc:
            return PickExtraction(status="answered", error=str(exc))

    logger.error("Pick reader failed %d times; answering with the nudge", MAX_ATTEMPTS)
    return PickExtraction(status="unrelated")


async def read_commands(llm: LLMClient, messages: Sequence[LLMMessage]) -> CommandReading:
    """Read the commands a message asks for (`prompts.command_messages`, or
    `prompts.repair_messages` for a second try). A reply that isn't the closed command
    union (an unknown command, a cost to change, no tag) is malformed: asked again, then
    `unrelated`. `LLMError` propagates."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        reply = await llm.chat(messages, format=COMMAND_SCHEMA, think=False)
        try:
            raw = CommandReply.model_validate_json(reply.content)
        except ValidationError as exc:
            logger.warning(
                "Command reader returned malformed output (attempt %d/%d): %s",
                attempt,
                MAX_ATTEMPTS,
                exc.errors(include_url=False)[:3],
            )
            continue
        logger.info("Command reader read %s (%d command(s))", raw.status, len(raw.commands))
        match raw.status:
            case "command" if raw.commands:
                return CommandReading(
                    status="command", commands=raw.commands, reply=raw.model_dump_json()
                )
            case "ambiguous" if raw.clarification and raw.clarification.strip():
                return CommandReading(status="ambiguous", clarification=raw.clarification.strip())
            case "question" | "unrelated":
                return CommandReading(status=raw.status)
        # A change with nothing to make, or a clarification with no question: no reading.
        return CommandReading(status="unrelated")

    logger.error("Command reader failed %d times; answering with the nudge", MAX_ATTEMPTS)
    return CommandReading(status="unrelated")
