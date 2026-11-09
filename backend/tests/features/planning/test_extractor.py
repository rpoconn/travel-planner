"""The slot extractor against a scripted model: every outcome, and the request it sends."""

from datetime import date

import pytest
from pydantic import JsonValue

from features.planning.extractor import (
    COMMAND_SCHEMA,
    MAX_ATTEMPTS,
    PICK_SCHEMA,
    Extraction,
    extract,
    extraction_schema,
)
from features.planning.schemas import DateRange, DatesAnswer
from features.planning.slots import SLOTS
from features.plans.schemas import SlotName
from shared.domain import Travelers
from shared.llm.anthropic_client import (
    MAX_OPTIONAL_FIELDS,
    MAX_UNION_FIELDS,
    schema_size,
    strict_schema,
)
from shared.llm.fake import FakeLLM, json_reply
from shared.llm.types import LLMError
from tests.features.plans.builders import snapshot
from tests.json_path import at, obj

TODAY = date(2026, 10, 1)
MAY: dict[str, JsonValue] = {"dates": {"start": "2027-05-01", "end": "2027-05-08"}}


async def _extract(
    llm: FakeLLM, slot: SlotName = SlotName.DATES, content: str = "May 1-8"
) -> Extraction:
    return await extract(llm, slot, content, snapshot(start_date=None, end_date=None), TODAY)


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


async def test_an_answer_is_parsed_into_the_slots_value() -> None:
    llm = FakeLLM([json_reply({"status": "answered", "value": MAY})])

    extraction = await _extract(llm)

    assert extraction.status == "answered"
    assert extraction.answer == DatesAnswer(
        dates=DateRange(start=date(2027, 5, 1), end=date(2027, 5, 8))
    )
    assert extraction.error is None


async def test_the_request_is_schema_bound_and_does_not_think() -> None:
    llm = FakeLLM([json_reply({"status": "answered", "value": {"adults": 2}})])

    extraction = await _extract(llm, SlotName.TRAVELERS, "two of us")

    (request,) = llm.requests
    assert request.format == extraction_schema(SlotName.TRAVELERS)
    assert request.think is False
    assert request.messages[-1].content == "two of us"
    assert "Who's coming?" in request.messages[0].content
    assert extraction.answer == Travelers(adults=2)


@pytest.mark.parametrize("status", ["command", "question", "unrelated"])
async def test_an_edit_a_question_or_unrelated_text_has_no_value(status: str) -> None:
    llm = FakeLLM([json_reply({"status": status, "value": None})])

    extraction = await _extract(llm)

    assert extraction.status == status
    assert extraction.answer is None and extraction.error is None


async def test_malformed_output_twice_fails_closed_to_unrelated() -> None:
    llm = FakeLLM([json_reply("not json"), json_reply({"status": "maybe"})])

    extraction = await _extract(llm)

    assert extraction.status == "unrelated" and extraction.answer is None
    assert len(llm.requests) == MAX_ATTEMPTS


async def test_malformed_output_is_asked_again() -> None:
    llm = FakeLLM([json_reply("{"), json_reply({"status": "answered", "value": MAY})])

    extraction = await _extract(llm)

    assert extraction.answer is not None
    assert len(llm.requests) == 2


async def test_an_answer_that_breaks_the_slots_rules_is_an_error_not_a_retry() -> None:
    backwards: dict[str, JsonValue] = {"dates": {"start": "2027-05-08", "end": "2027-05-01"}}
    llm = FakeLLM([json_reply({"status": "answered", "value": backwards})])

    extraction = await _extract(llm)

    assert extraction.status == "answered" and extraction.answer is None
    assert extraction.error == "The trip has to end on or after the day it starts"
    assert len(llm.requests) == 1


async def test_an_answer_without_a_value_counts_as_unrelated() -> None:
    llm = FakeLLM([json_reply({"status": "answered"})])

    extraction = await _extract(llm)

    assert extraction.status == "unrelated"


async def test_an_unreachable_model_propagates() -> None:
    llm = FakeLLM([LLMError("Could not reach Ollama")])

    with pytest.raises(LLMError):
        await _extract(llm)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_the_schema_wraps_the_slots_value_model() -> None:
    schema = extraction_schema(SlotName.DATES)

    assert schema["required"] == ["status"]
    assert at(schema, "properties", "status", "enum") == [
        "answered",
        "command",
        "question",
        "unrelated",
    ]
    dates = obj(schema, "properties", "value", "properties", "dates")
    assert at(dates, "properties", "start") == {"format": "date", "type": "string"}


def test_a_pick_slot_has_no_extraction_schema() -> None:
    with pytest.raises(ValueError, match="pick slot"):
        extraction_schema(SlotName.FLIGHT_OUTBOUND)


def _schemas() -> list[tuple[str, dict[str, JsonValue]]]:
    slots = [
        (f"slot {slot}", extraction_schema(slot))
        for slot, spec in SLOTS.items()
        if spec.value_model is not None
    ]
    return [("pick", PICK_SCHEMA), ("command", COMMAND_SCHEMA), *slots]


@pytest.mark.parametrize(("name", "schema"), _schemas(), ids=[name for name, _ in _schemas()])
def test_every_schema_fits_claudes_structured_output_limits(
    name: str, schema: dict[str, JsonValue]
) -> None:
    # Over these, Claude rejects the request before the model reads it.
    size = schema_size(strict_schema(schema))

    assert size.optional <= MAX_OPTIONAL_FIELDS, f"{name}: {size.optional} optional fields"
    assert size.unions <= MAX_UNION_FIELDS, f"{name}: {size.unions} union fields"
