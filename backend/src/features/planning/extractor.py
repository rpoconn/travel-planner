"""The slot extractor: turns free text typed while an input is pending into that slot's value.

The model is asked for `{status, value}` constrained to a JSON schema built from the slot's
value model, with thinking off. Its reply is parsed in two steps, so a value that has the
right shape but fails the slot's rules ("from May 8 to May 1") is told apart from output
that is malformed. Malformed twice fails closed to `unrelated`, which the caller answers
with the slot's fixed nudge. This is the whole guardrail: nothing else reads model output.
"""

import logging
from dataclasses import dataclass
from datetime import date
from functools import cache
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, ValidationError, create_model

from features.planning.answers import AnswerError, parse_answer
from features.planning.prompts import extractor_messages
from features.planning.slots import SLOTS
from features.plans.schemas import PlanSnapshot, SlotName
from shared.llm.schema import inline_schema
from shared.llm.types import LLMClient

logger = logging.getLogger(__name__)

# Tries before giving up on malformed output (as in the old topic guardrail).
MAX_ATTEMPTS = 2

ExtractionStatus = Literal["answered", "question", "unrelated"]

_STATUS_DESCRIPTION = (
    "answered: the message gives the answer; question: it asks something instead; "
    "unrelated: anything else"
)


class _RawExtraction(BaseModel):
    """The reply's outer shape. `value` stays a plain dict here and is checked against the
    slot afterwards, so a rule it breaks becomes a message for the user, not a retry."""

    status: ExtractionStatus
    value: dict[str, Any] | None = None


@dataclass(frozen=True)
class Extraction:
    status: ExtractionStatus
    answer: BaseModel | None = None  # answered, and the value fits the slot
    error: str | None = None  # answered, but the value breaks the slot's rules


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


@cache
def extraction_schema(slot: SlotName) -> dict[str, Any]:
    """The JSON schema the model's reply must follow for `slot` (sent as Ollama's `format`)."""
    value_model = SLOTS[slot].value_model
    if value_model is None:
        raise ValueError(f"{slot} is a pick slot; it has no value to extract")
    model = create_model(
        f"{value_model.__name__}Extraction",
        status=(ExtractionStatus, Field(description=_STATUS_DESCRIPTION)),
        # A union built at runtime, which mypy can't see as a type form.
        value=(cast(Any, value_model | None), None),
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
