from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from features.chat.agent import MAX_TOOL_ROUNDS, run_agent
from features.chat.prompts import (
    AGENT_FINAL_ANSWER,
    ALREADY_STAGED,
    RETURN_FLIGHT_NUDGE,
    agent_system_prompt,
)
from features.chat.schemas import (
    ChatEvent,
    PlanUpdatedEvent,
    ProposedChange,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from features.chat.tools import TOOL_SPECS, ToolContext
from features.itinerary import service as itinerary_service
from features.itinerary.schemas import FlightItemCreate
from features.plans import service as plans_service
from features.plans.schemas import PlanUpdate
from shared.domain import Money
from shared.llm.fake import FakeLLM, text, tool_calls
from shared.llm.types import LLMMessage, ToolSpec
from tests.features.chat.conftest import HOTEL_ARGS

TODAY = date(2027, 1, 15)


async def _run(
    llm: FakeLLM,
    ctx: ToolContext,
    latest: str = "Add Hotel Okura",
    history: list[LLMMessage] | None = None,
    tool_specs: Sequence[ToolSpec] = TOOL_SPECS,
    staged: list[ProposedChange] | None = None,
) -> list[ChatEvent]:
    plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
    return [
        event
        async for event in run_agent(
            llm=llm,
            ctx=ctx,
            plan=plan,
            history=history or [],
            latest=latest,
            today=TODAY,
            staged=[] if staged is None else staged,
            tool_specs=tool_specs,
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
    assert "Today is Friday 2027-01-15 (January 15, 2027)" in system and '"Tokyo in May"' in system
    # Last month is the example: in January, "December" is this year's.
    assert '"in December" means December 2027' in system
    assert request.messages[-1].content == "Plan my flights"


async def test_tool_specs_narrow_the_offered_tools(ctx: ToolContext) -> None:
    search_only = [spec for spec in TOOL_SPECS if spec.name == "web_search"]
    llm = FakeLLM([text("Here's what I'd change.")])

    await _run(llm, ctx, "Fix it", tool_specs=search_only)

    (request,) = llm.requests
    assert request.tools == search_only


async def test_system_prompt_dates_an_earlier_month_next_year(ctx: ToolContext) -> None:
    plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)

    system = agent_system_prompt(plan, date(2026, 10, 29))

    assert '"in September" means September 2027' in system


# ---------------------------------------------------------------------------
# Tool calls: plan changes are staged, never applied
# ---------------------------------------------------------------------------


async def test_tool_call_stages_a_change_without_writing(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", HOTEL_ARGS), content="Let me propose it."),
            text("I've proposed Hotel Okura, May 2 to 6."),
        ]
    )
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    (call,) = _of_type(events, ToolCallEvent)
    (result,) = _of_type(events, ToolResultEvent)
    assert call.name == "add_lodging" and call.arguments == HOTEL_ARGS
    assert result.call_id == call.call_id and result.ok and result.proposed
    assert result.summary == "Proposed: Add lodging “Hotel Okura” on 2027-05-02 15:00"
    assert _of_type(events, PlanUpdatedEvent) == []
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []
    (change,) = staged
    assert change.call_id == call.call_id and change.name == "add_lodging"
    assert change.arguments["title"] == "Hotel Okura" and "type" not in change.arguments
    assert _text(events) == "Let me propose it.\n\nI've proposed Hotel Okura, May 2 to 6."

    # No auditor call in between: two streamed rounds, and the model was told it's staged.
    assert len(llm.requests) == 2 and all(r.streamed for r in llm.requests)
    second = llm.requests[1].messages
    assert second[-2].role == "assistant" and second[-2].tool_calls[0].name == "add_lodging"
    assert second[-1].role == "tool" and second[-1].tool_name == "add_lodging"
    assert second[-1].content.startswith("Staged for the user's approval: Add lodging")


async def test_several_calls_are_staged_in_order(ctx: ToolContext) -> None:
    second_hotel: dict[str, Any] = {
        **HOTEL_ARGS,
        "title": "Ryokan Sawanoya",
        "start": "2027-05-06T15:00:00",
        "end": "2027-05-08T10:00:00",
    }
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", HOTEL_ARGS), ("add_lodging", second_hotel)),
            text("Both proposed."),
        ]
    )
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    assert [e.proposed for e in _of_type(events, ToolResultEvent)] == [True, True]
    assert [change.arguments["title"] for change in staged] == ["Hotel Okura", "Ryokan Sawanoya"]


async def test_changes_in_one_turn_share_the_budget(ctx: ToolContext) -> None:
    patch = PlanUpdate(budget=Money(amount=Decimal(1000)))
    await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, patch)
    stays = [
        {**HOTEL_ARGS, "title": title, "cost": {"amount": 400}}
        for title in ("Hotel Okura", "Ryokan Sawanoya", "Park Hyatt")
    ]
    llm = FakeLLM(
        [tool_calls(*(("add_lodging", stay) for stay in stays)), text("Two fit the budget.")]
    )
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    # Each fits the budget alone; the third doesn't fit next to the other two.
    assert [e.ok for e in _of_type(events, ToolResultEvent)] == [True, True, False]
    assert [change.arguments["title"] for change in staged] == ["Hotel Okura", "Ryokan Sawanoya"]


async def test_a_repeated_change_is_staged_once(ctx: ToolContext) -> None:
    # The repeat differs only in a null the tool drops, so it normalises to the same change.
    repeat = {**HOTEL_ARGS, "notes": None}
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", HOTEL_ARGS)),
            tool_calls(("add_lodging", repeat)),
            text("Proposed."),
        ]
    )
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    assert len(staged) == 1
    first, second = _of_type(events, ToolResultEvent)
    assert first.proposed and not second.proposed and second.ok
    assert second.summary.startswith("Already proposed: Add lodging")
    assert llm.requests[2].messages[-1].content == ALREADY_STAGED


async def test_web_search_runs_and_is_not_staged(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("web_search", {"query": "ryokan in Hakone", "kind": "hotels"})),
            text("Here are a few ryokan."),
        ]
    )
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "Find a ryokan", staged=staged)

    (result,) = _of_type(events, ToolResultEvent)
    assert result.ok and not result.proposed
    assert staged == []
    assert llm.requests[1].messages[-1].content.startswith("Web results for")


async def test_malformed_arguments_are_reported_then_corrected(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", {**HOTEL_ARGS, "start": "next tuesday"})),
            tool_calls(("add_lodging", HOTEL_ARGS)),
            text("Proposed."),
        ]
    )
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    assert [e.ok for e in _of_type(events, ToolResultEvent)] == [False, True]
    assert len(staged) == 1
    # The model saw the validation error before retrying.
    feedback = llm.requests[1].messages[-1]
    assert feedback.role == "tool"
    assert feedback.content.startswith("Error: Invalid arguments for add_lodging: start")


async def test_malformed_arguments_that_are_never_fixed_are_reported(ctx: ToolContext) -> None:
    bad = tool_calls(("remove_item", {"item_id": "not-a-uuid"}))
    llm = FakeLLM([bad, bad, text("Sorry, I couldn't find that item.")])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "Remove the hotel", staged=staged)

    results = _of_type(events, ToolResultEvent)
    assert [e.ok for e in results] == [False, False]
    assert results[0].summary.startswith("Invalid arguments for remove_item: item_id")
    assert staged == []
    assert _text(events) == "Sorry, I couldn't find that item."


async def test_invented_plan_details_are_only_proposed(ctx: ToolContext) -> None:
    # The old failure mode: details the user never gave. They can't reach the plan now;
    # the worst case is a proposal card the user denies.
    invented: dict[str, Any] = {"origin": {"name": "New York"}, "travelers": {"adults": 1}}
    llm = FakeLLM([tool_calls(("set_plan_details", invented)), text("Proposed.")])
    staged: list[ProposedChange] = []

    await _run(llm, ctx, "I want to spend a week in Tokyo in May", staged=staged)

    (change,) = staged
    assert change.summary == "Set trip origin, travelers"
    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.origin is None and plan.travelers is None


# ---------------------------------------------------------------------------
# Return flight
# ---------------------------------------------------------------------------


def _flight(from_iata: str, to_iata: str, day: str, direction: str) -> dict[str, Any]:
    return {
        "title": f"{from_iata} to {to_iata}",
        "carrier": "Japan Airlines",
        "start": f"{day}T10:00:00",
        "end": f"{day}T14:00:00",
        "timezone": "Asia/Tokyo",
        "from_place": {"iata": from_iata},
        "to_place": {"iata": to_iata},
        "direction": direction,
    }


OUTBOUND = _flight("PHL", "HND", "2027-05-01", "outbound")
RETURN = _flight("HND", "PHL", "2027-05-08", "return")


def _system_notes(llm: FakeLLM) -> list[str]:
    """System messages after the prompt (the last request has every earlier message)."""
    return [m.content for m in llm.requests[-1].messages[1:] if m.role == "system"]


async def test_an_outbound_flight_alone_gets_a_return_flight_reminder(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_flight", OUTBOUND)),
            tool_calls(("add_flight", RETURN)),
            text("I've proposed both flights."),
        ]
    )
    staged: list[ProposedChange] = []

    await _run(llm, ctx, "Fly me there and back", staged=staged)

    # The reminder follows the outbound flight's result, so the model sees it next round.
    after_outbound = llm.requests[1].messages
    assert after_outbound[-1].role == "system"
    assert after_outbound[-1].content == RETURN_FLIGHT_NUDGE.format(
        away="HND", home="PHL", when=" on 2027-05-08"
    )
    assert [change.arguments["direction"] for change in staged] == ["outbound", "return"]
    # Once the return is on the card there's nothing more to say.
    assert len(_system_notes(llm)) == 1


async def test_both_flights_on_one_card_need_no_reminder(ctx: ToolContext) -> None:
    llm = FakeLLM([tool_calls(("add_flight", OUTBOUND), ("add_flight", RETURN)), text("Done.")])

    await _run(llm, ctx, "Fly me there and back")

    assert _system_notes(llm) == []


async def test_the_return_flight_reminder_is_given_once_a_turn(ctx: ToolContext) -> None:
    hotel = tool_calls(("add_lodging", HOTEL_ARGS))
    llm = FakeLLM([tool_calls(("add_flight", OUTBOUND)), hotel, text("What time home?")])

    await _run(llm, ctx, "Fly me there")

    assert len(_system_notes(llm)) == 1


async def test_a_trip_already_missing_its_return_flight_is_not_nagged(ctx: ToolContext) -> None:
    # The gap was raised when the outbound flight was proposed; later turns about hotels
    # shouldn't keep bringing it up.
    flight = FlightItemCreate.model_validate({**OUTBOUND, "type": "flight"})
    await itinerary_service.add_item(
        ctx.session, ctx.user_id, ctx.plan_id, flight, created_by="assistant"
    )
    llm = FakeLLM([tool_calls(("add_lodging", HOTEL_ARGS)), text("Proposed the hotel.")])

    await _run(llm, ctx)

    assert _system_notes(llm) == []


async def test_removing_the_return_flight_gets_a_reminder(ctx: ToolContext) -> None:
    for args in (OUTBOUND, RETURN):
        flight = FlightItemCreate.model_validate({**args, "type": "flight"})
        await itinerary_service.add_item(
            ctx.session, ctx.user_id, ctx.plan_id, flight, created_by="user"
        )
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    return_id = next(str(i.id) for i in items if i.type == "flight" and i.direction == "return")
    llm = FakeLLM([tool_calls(("remove_item", {"item_id": return_id})), text("Removed it.")])

    await _run(llm, ctx, "Drop the flight home")

    assert len(_system_notes(llm)) == 1


# ---------------------------------------------------------------------------
# Round limit
# ---------------------------------------------------------------------------


async def test_round_limit_forces_a_text_answer(ctx: ToolContext) -> None:
    looping = tool_calls(("remove_item", {"item_id": "00000000-0000-0000-0000-000000000000"}))
    llm = FakeLLM([looping] * MAX_TOOL_ROUNDS)
    # The final round ignores any tool calls the model still makes.
    llm.queue(tool_calls(("add_lodging", HOTEL_ARGS), content="I couldn't do that."))
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    assert llm.pending == 0
    assert len(llm.requests) == MAX_TOOL_ROUNDS + 1
    assert len(_of_type(events, ToolCallEvent)) == MAX_TOOL_ROUNDS
    final = llm.requests[-1]
    assert final.tools == []
    assert final.messages[-1].role == "system"
    assert final.messages[-1].content == AGENT_FINAL_ANSWER
    assert _text(events) == "I couldn't do that."
    assert staged == []
