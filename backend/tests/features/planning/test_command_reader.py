"""The command reader against a scripted model: its outcomes, the request it sends, its
schema, and the prompt with the numbered itinerary (and the repair prompt)."""

from datetime import date
from typing import Literal

import pytest
from pydantic import JsonValue

from features.planning.extractor import (
    COMMAND_SCHEMA,
    MAX_ATTEMPTS,
    CommandReading,
    read_commands,
)
from features.planning.prompts import command_messages, item_line, repair_messages
from features.planning.schemas import ChangeStage, RejectedChange, RemoveItem
from features.plans.schemas import (
    FlightPreferences,
    LodgingPreferences,
    PlanPreferences,
    PlanStage,
)
from shared.llm.fake import FakeLLM, json_reply
from shared.llm.types import LLMError, LLMMessage
from tests.features.plans.builders import complete_trip, return_flight, snapshot
from tests.json_path import at, obj

TODAY = date(2026, 10, 1)


def _messages(content: str = "remove the dinner") -> list[LLMMessage]:
    plan = snapshot(complete_trip())
    return command_messages(content, plan, plan.items, TODAY)


async def _read(*replies: dict[str, JsonValue] | str) -> tuple[CommandReading, FakeLLM]:
    llm = FakeLLM([json_reply(reply) for reply in replies])
    return await read_commands(llm, _messages()), llm


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------


async def test_commands_are_read_into_the_closed_union() -> None:
    reading, llm = await _read(
        {
            "status": "command",
            "commands": [
                {"type": "remove_item", "item": 5},
                {"type": "change_stage", "action": "go_to", "stage": "activities"},
            ],
        }
    )

    assert reading.status == "command"
    assert reading.commands == [
        RemoveItem(item=5),
        ChangeStage(action="go_to", stage=PlanStage.ACTIVITIES),
    ]
    # Kept as read, for a repair pass to show the model what it said.
    assert reading.reply is not None and '"remove_item"' in reading.reply
    (request,) = llm.requests
    assert request.format == COMMAND_SCHEMA
    assert request.think is False


async def test_an_ambiguous_message_asks_which() -> None:
    reading, _llm = await _read(
        {"status": "ambiguous", "clarification": " Which dinner: Friday's or Saturday's? "}
    )

    assert reading == CommandReading(
        status="ambiguous", clarification="Which dinner: Friday's or Saturday's?"
    )


@pytest.mark.parametrize("status", ["question", "unrelated"])
async def test_a_question_or_unrelated_text_has_no_commands(
    status: Literal["question", "unrelated"],
) -> None:
    reading, _llm = await _read({"status": status, "commands": []})

    assert reading == CommandReading(status=status)


@pytest.mark.parametrize(
    "reply",
    [
        {"status": "command", "commands": []},  # a change with nothing to change
        {"status": "ambiguous", "clarification": "  "},  # asks back with no question
    ],
)
async def test_an_empty_reading_counts_as_unrelated(reply: dict[str, JsonValue]) -> None:
    reading, _llm = await _read(reply)

    assert reading == CommandReading(status="unrelated")


@pytest.mark.parametrize(
    "command",
    [
        {"type": "add_activity", "title": "Museum"},  # no adds
        {"item": 2},  # no tag
        {"type": "update_item", "item": 2, "changes": {"cost": {"amount": 10}}},  # no costs
    ],
)
async def test_a_command_outside_the_union_is_retried_then_unrelated(
    command: dict[str, JsonValue],
) -> None:
    bad: dict[str, JsonValue] = {"status": "command", "commands": [command]}

    reading, llm = await _read(bad, bad)

    assert reading == CommandReading(status="unrelated")
    assert len(llm.requests) == MAX_ATTEMPTS


async def test_malformed_output_is_asked_again() -> None:
    good: dict[str, JsonValue] = {
        "status": "command",
        "commands": [{"type": "remove_item", "item": 1}],
    }

    reading, llm = await _read("{", good)

    assert reading.commands == [RemoveItem(item=1)]
    assert len(llm.requests) == 2


async def test_an_unreachable_model_propagates() -> None:
    with pytest.raises(LLMError):
        await read_commands(FakeLLM([LLMError("Could not reach Ollama")]), _messages())


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_the_schema_offers_each_command_by_its_tag() -> None:
    items = obj(COMMAND_SCHEMA, "properties", "commands", "items")
    branches = at(items, "anyOf")

    assert "oneOf" not in items and "discriminator" not in items
    assert isinstance(branches, list)
    tags = [at(branch, "properties", "type", "const") for branch in branches]
    assert tags == [
        "remove_item",
        "update_item",
        "set_dates",
        "set_travelers",
        "set_destination",
        "set_origin",
        "set_budget",
        "set_flight_preferences",
        "set_lodging_preferences",
        "set_activity_preferences",
        "change_stage",
        "redo_search",
    ]
    # The tag is asked for, so every reply can be told apart.
    for branch in branches:
        required = at(branch, "required")
        assert isinstance(required, list) and "type" in required
        # And it comes first: Claude writes keys in schema order, so a command whose tag
        # came after its fields could never be chosen.
        assert next(iter(obj(branch, "properties"))) == "type"


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------


def test_an_item_line_shows_local_times_zones_and_places() -> None:
    flight = return_flight()

    line = item_line(7, flight)

    assert line == (
        "7. flight “NRT → SFO” | Tue 2027-05-04T17:00 to Tue 2027-05-04T10:00 "
        "(Asia/Tokyo to America/Los_Angeles) | Narita International Airport to "
        "San Francisco International Airport"
    )


def test_a_removed_item_is_marked() -> None:
    flight = return_flight().model_copy(update={"status": "cancelled"})

    assert item_line(1, flight).endswith(" | removed")


def test_the_prompt_numbers_the_itinerary_and_names_the_pending_question() -> None:
    plan = snapshot(complete_trip())
    history = [
        LLMMessage(role="user", content="remove it"),
        LLMMessage(role="assistant", content="Which one?"),
    ]

    messages = command_messages(
        "the dinner", plan, plan.items, TODAY, pending="Pick your hotel", history=history
    )

    system = messages[0].content
    assert "Today is Thursday 2026-10-01." in system
    assert "5. activity “Hotel dinner” | Sun 2027-05-02T18:00" in system
    assert 'waiting for an answer to: "Pick your hotel"' in system
    assert "ground (ground transport)" in system
    assert [(m.role, m.content) for m in messages[1:]] == [
        ("user", "remove it"),
        ("assistant", "Which one?"),
        ("user", "the dinner"),
    ]


def test_the_prompt_shows_the_search_preferences_to_change() -> None:
    preferences = PlanPreferences(
        flights=FlightPreferences(max_stops=0, outbound_token="secret-token"),
        lodging=LodgingPreferences(hotel_class="midrange"),
    )
    plan = snapshot(complete_trip(), preferences=preferences)

    system = command_messages("make it upscale", plan, plan.items, TODAY)[0].content

    assert "Search preferences:\n- flights: {" in system
    assert '- lodging: {"hotel_class":"midrange","kind":"hotel"' in system
    assert "- activities" not in system
    # The search token is for the flight search, not the model.
    assert "secret-token" not in system


def test_a_plan_without_preferences_shows_none() -> None:
    assert "Search preferences" not in _messages()[0].content


def test_an_empty_itinerary_says_so() -> None:
    messages = command_messages("go back", snapshot(), [], TODAY)

    assert "The itinerary is empty." in messages[0].content


def test_the_repair_prompt_shows_the_reply_and_the_broken_rules() -> None:
    rejected = [RejectedChange(summary="Change “Hotel dinner”: end", reason="end before start")]

    messages = repair_messages(_messages(), '{"status":"command"}', rejected)

    assert [m.role for m in messages[-2:]] == ["assistant", "user"]
    assert messages[-2].content == '{"status":"command"}'
    assert "- Change “Hotel dinner”: end: end before start" in messages[-1].content
