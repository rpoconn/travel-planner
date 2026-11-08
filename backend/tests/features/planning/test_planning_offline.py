"""The offline fake's planning replies (LLM_PROVIDER=fake)."""

import json
from datetime import date

from pydantic import JsonValue

from features.planning.extractor import COMMAND_SCHEMA, PICK_SCHEMA, extraction_schema
from features.planning.offline import (
    OFFLINE_ANSWER,
    command_reply,
    extract_reply,
    pick_reply,
    planning_reply,
)
from features.planning.prompts import command_messages, question_messages
from features.plans.schemas import SlotName
from shared.llm.fake import FakeRequest
from shared.llm.types import LLMMessage
from tests.features.plans.builders import complete_trip, snapshot
from tests.json_path import json_object

TODAY = date(2026, 10, 15)


def _command_prompt(cancelled: int | None = None) -> str:
    """The command reader's system prompt for the full Tokyo trip, whose items are
    1 transfer, 2 flight, 3 transfer, 4 lodging "Hotel Okura", 5 activity "Hotel dinner"..."""
    items = complete_trip()
    if cancelled is not None:
        items[cancelled - 1] = items[cancelled - 1].model_copy(update={"status": "cancelled"})
    plan = snapshot(items)
    return command_messages("", plan, plan.items, TODAY)[0].content


def _request(slot: SlotName, content: str) -> FakeRequest:
    messages = [LLMMessage(role="system", content="..."), LLMMessage(role="user", content=content)]
    return FakeRequest(messages, [], extraction_schema(slot), streamed=False, think=False)


def test_a_known_phrase_is_answered() -> None:
    assert extract_reply(SlotName.TRAVELERS, "Two adults.", TODAY) == {
        "status": "answered",
        "value": {"adults": 2},
    }


def test_relative_dates_count_from_today() -> None:
    reply = extract_reply(SlotName.DATES, "next month for a week", TODAY)

    assert reply["value"] == {"dates": {"start": "2026-11-01", "end": "2026-11-08"}}


def test_a_question_and_anything_else() -> None:
    assert extract_reply(SlotName.DATES, "When is it cheapest?", TODAY)["status"] == "question"
    assert extract_reply(SlotName.DATES, "what's 2+2", TODAY)["status"] == "unrelated"


def test_the_slot_is_read_from_the_requested_schema() -> None:
    reply = planning_reply(_request(SlotName.DESTINATION, "lisbon"))

    assert '"Lisbon"' in reply.content and '"answered"' in reply.content


def test_a_question_request_gets_the_offline_answer() -> None:
    messages = question_messages(snapshot(), "Is May warm?", None)

    reply = planning_reply(FakeRequest(messages, [], None, streamed=True))

    assert reply.content == OFFLINE_ANSWER


def test_any_other_request_gets_a_fixed_reply() -> None:
    unknown_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {"on_topic": {"type": "boolean"}},
    }
    structured = FakeRequest([], [], unknown_schema, streamed=False)
    plain = FakeRequest([LLMMessage(role="system", content="You plan trips")], [], None, True)

    assert json.loads(planning_reply(structured).content) == {"status": "unrelated", "value": None}
    assert planning_reply(plain).content == OFFLINE_ANSWER


def test_a_pick_is_read_by_position() -> None:
    assert pick_reply("The second one.") == {"status": "answered", "candidate_ids": ["2"]}
    assert pick_reply("first")["candidate_ids"] == ["1"]
    assert pick_reply("Which is shortest?")["status"] == "question"
    assert pick_reply("anything works")["status"] == "unrelated"


def test_a_pick_request_is_recognised_by_its_schema() -> None:
    messages = [LLMMessage(role="user", content="the third one")]

    reply = planning_reply(FakeRequest(messages, [], PICK_SCHEMA, streamed=False, think=False))

    assert reply is not None and '"3"' in reply.content


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def test_remove_finds_the_item_in_the_numbered_list() -> None:
    prompt = _command_prompt()

    assert command_reply("Remove the hotel.", prompt) == {
        "status": "command",
        "commands": [{"type": "remove_item", "item": 4}],
    }
    assert command_reply("remove the dinner", prompt)["commands"] == [
        {"type": "remove_item", "item": 5}
    ]


def test_a_removed_item_is_not_found_again() -> None:
    reply = command_reply("remove the hotel", _command_prompt(cancelled=4))

    assert reply == {"status": "unrelated", "commands": []}


def test_stage_phrases() -> None:
    prompt = _command_prompt()

    assert command_reply("go back to lodging", prompt)["commands"] == [
        {"type": "change_stage", "action": "go_to", "stage": "lodging"}
    ]
    assert command_reply("go back to ground transport", prompt)["commands"] == [
        {"type": "change_stage", "action": "go_to", "stage": "ground"}
    ]
    assert command_reply("Go back", prompt)["commands"] == [
        {"type": "change_stage", "action": "back"}
    ]
    assert command_reply("skip flights", prompt)["commands"] == [
        {"type": "change_stage", "action": "skip_flights"}
    ]


def test_the_command_request_is_recognised_by_its_schema() -> None:
    messages = [
        LLMMessage(role="system", content=_command_prompt()),
        LLMMessage(role="user", content="remove the hotel"),
    ]

    reply = planning_reply(FakeRequest(messages, [], COMMAND_SCHEMA, streamed=False))

    assert json_object(reply.content)["commands"] == [{"type": "remove_item", "item": 4}]


def test_both_readers_hand_an_edit_on() -> None:
    edit = [LLMMessage(role="user", content="remove the hotel")]

    for schema in (extraction_schema(SlotName.DATES), PICK_SCHEMA):
        reply = planning_reply(FakeRequest(edit, [], schema, streamed=False))
        assert json_object(reply.content) == {"status": "command"}
