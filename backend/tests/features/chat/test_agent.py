from datetime import date
from typing import Any

from features.chat.agent import MAX_TOOL_ROUNDS, run_agent
from features.chat.prompts import AGENT_FINAL_ANSWER
from features.chat.schemas import (
    ChatEvent,
    PlanUpdatedEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from features.chat.tools import TOOL_SPECS, ToolContext
from features.itinerary import service as itinerary_service
from shared.llm.fake import FakeLLM, text, tool_calls
from tests.features.chat.conftest import HOTEL_ARGS

TODAY = date(2027, 1, 15)


async def _run(llm: FakeLLM, ctx: ToolContext, latest: str = "Add Hotel Okura") -> list[ChatEvent]:
    plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
    return [
        event
        async for event in run_agent(
            llm=llm, ctx=ctx, plan=plan, history=[], latest=latest, today=TODAY
        )
    ]


def _text(events: list[ChatEvent]) -> str:
    return "".join(event.text for event in events if isinstance(event, TokenEvent))


def _of_type[T](events: list[ChatEvent], kind: type[T]) -> list[T]:
    return [event for event in events if isinstance(event, kind)]


# ---------------------------------------------------------------------------
# Plain answers
# ---------------------------------------------------------------------------


async def test_text_reply_streams_tokens(ctx: ToolContext) -> None:
    llm = FakeLLM([text("When would you like to fly?")])

    events = await _run(llm, ctx, "Plan my flights")

    assert all(isinstance(event, TokenEvent) for event in events)
    assert len(events) > 1
    assert _text(events) == "When would you like to fly?"
    (request,) = llm.requests
    assert request.streamed and request.tools == TOOL_SPECS
    system = request.messages[0].content
    assert "Today is 2027-01-15" in system and '"Tokyo in May"' in system
    assert request.messages[-1].content == "Plan my flights"


# ---------------------------------------------------------------------------
# Tool calls
# ---------------------------------------------------------------------------


async def test_tool_call_adds_item_and_emits_plan_updated(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", HOTEL_ARGS), content="Adding it now."),
            text("Done! Hotel Okura is on your plan."),
        ]
    )

    events = await _run(llm, ctx)

    (call,) = _of_type(events, ToolCallEvent)
    (result,) = _of_type(events, ToolResultEvent)
    (updated,) = _of_type(events, PlanUpdatedEvent)
    assert call.name == "add_lodging" and call.arguments == HOTEL_ARGS
    assert result.call_id == call.call_id and result.ok
    assert [item.title for item in updated.plan.items] == ["Hotel Okura"]
    assert events.index(call) < events.index(result) < events.index(updated)
    assert _text(events) == "Adding it now.\n\nDone! Hotel Okura is on your plan."

    # Second round: the tool result is fed back and the system prompt shows the new item.
    second = llm.requests[1].messages
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].name == "add_lodging"
    assert second[-1].role == "tool" and second[-1].tool_name == "add_lodging"
    assert second[-1].content.startswith('Added lodging "Hotel Okura"')
    assert str(updated.plan.items[0].id) in second[0].content


async def test_several_calls_in_one_round_run_in_order(ctx: ToolContext) -> None:
    second_hotel: dict[str, Any] = {
        **HOTEL_ARGS,
        "title": "Ryokan Sawanoya",
        "start": "2027-05-06T15:00:00",
        "end": "2027-05-08T10:00:00",
    }
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", HOTEL_ARGS), ("add_lodging", second_hotel)),
            text("Both added."),
        ]
    )

    events = await _run(llm, ctx)

    assert [e.ok for e in _of_type(events, ToolResultEvent)] == [True, True]
    last = _of_type(events, PlanUpdatedEvent)[-1]
    assert [item.title for item in last.plan.items] == ["Hotel Okura", "Ryokan Sawanoya"]


async def test_malformed_arguments_are_reported_then_corrected(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", {**HOTEL_ARGS, "start": "next tuesday"})),
            tool_calls(("add_lodging", HOTEL_ARGS)),
            text("Added."),
        ]
    )

    events = await _run(llm, ctx)

    assert [e.ok for e in _of_type(events, ToolResultEvent)] == [False, True]
    assert len(_of_type(events, PlanUpdatedEvent)) == 1
    # The model saw the validation error before retrying.
    feedback = llm.requests[1].messages[-1]
    assert feedback.role == "tool"
    assert feedback.content.startswith("Error: Invalid arguments for add_lodging: start")


async def test_malformed_arguments_that_are_never_fixed_are_reported(ctx: ToolContext) -> None:
    bad = tool_calls(("remove_item", {"item_id": "not-a-uuid"}))
    llm = FakeLLM([bad, bad, text("Sorry, I couldn't find that item.")])

    events = await _run(llm, ctx, "Remove the hotel")

    results = _of_type(events, ToolResultEvent)
    assert [e.ok for e in results] == [False, False]
    assert results[0].summary.startswith("Invalid arguments for remove_item: item_id")
    assert _of_type(events, PlanUpdatedEvent) == []
    assert _text(events) == "Sorry, I couldn't find that item."


# ---------------------------------------------------------------------------
# Round limit
# ---------------------------------------------------------------------------


async def test_round_limit_forces_a_text_answer(ctx: ToolContext) -> None:
    looping = tool_calls(("remove_item", {"item_id": "00000000-0000-0000-0000-000000000000"}))
    llm = FakeLLM([looping] * MAX_TOOL_ROUNDS)
    # The final round ignores any tool calls the model still makes.
    llm.queue(tool_calls(("add_lodging", HOTEL_ARGS), content="I couldn't do that."))

    events = await _run(llm, ctx)

    assert llm.pending == 0
    assert len(llm.requests) == MAX_TOOL_ROUNDS + 1
    assert len(_of_type(events, ToolCallEvent)) == MAX_TOOL_ROUNDS
    final = llm.requests[-1]
    assert final.tools == []
    assert final.messages[-1].role == "system"
    assert final.messages[-1].content == AGENT_FINAL_ANSWER
    assert _text(events) == "I couldn't do that."
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []
