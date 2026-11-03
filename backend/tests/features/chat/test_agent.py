from collections import deque
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from features.chat.agent import MAX_TOOL_ROUNDS, run_agent
from features.chat.prompts import (
    AGENT_FINAL_ANSWER,
    ALREADY_STAGED,
    PHANTOM_PROPOSAL_NOTE,
    PHANTOM_PROPOSAL_NUDGE,
    PROMISED_SEARCH_NUDGE,
    RETURN_FLIGHT_NUDGE,
    WITHDRAWN_NOTE,
    agent_system_prompt,
    approval_applied_message,
    review_feedback,
)
from features.chat.review import ReviewFn
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
    review: ReviewFn | None = None,
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
            review=review,
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
# Cited sources (every agent turn checks them against its own web results)
# ---------------------------------------------------------------------------


async def test_an_invented_source_url_is_refused_and_retried(ctx: ToolContext) -> None:
    invented = {**HOTEL_ARGS, "source_url": "https://www.example.com/okura"}
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", invented)),
            tool_calls(("add_lodging", HOTEL_ARGS)),
            text("I've proposed Hotel Okura."),
        ]
    )
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    assert [e.ok for e in _of_type(events, ToolResultEvent)] == [False, True]
    assert "Never invent a URL" in llm.requests[1].messages[-1].content
    (change,) = staged
    assert "source_url" not in change.arguments


async def test_a_link_the_user_pasted_may_be_cited(ctx: ToolContext) -> None:
    url = "https://www.hotelokura.co.jp/tokyo"
    llm = FakeLLM([tool_calls(("add_lodging", {**HOTEL_ARGS, "source_url": url})), text("Done.")])
    staged: list[ProposedChange] = []

    await _run(llm, ctx, f"Add this hotel: {url}/.", staged=staged)

    (change,) = staged
    assert change.arguments["source_url"] == url


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


async def test_a_round_trip_stages_two_flights_with_distinct_ids(ctx: ToolContext) -> None:
    round_trip = tool_calls(("add_round_trip", {"outbound": OUTBOUND, "return": RETURN}))
    llm = FakeLLM([round_trip, round_trip, text("I've proposed both flights.")])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "Fly me there and back", staged=staged)

    assert [change.arguments["direction"] for change in staged] == ["outbound", "return"]
    first_call = _of_type(events, ToolResultEvent)[0].call_id
    assert [change.call_id for change in staged] == [first_call, f"{first_call}-2"]
    # The repeat is caught, and a whole round trip needs no return-flight reminder.
    assert llm.requests[2].messages[-1].content == ALREADY_STAGED
    assert _system_notes(llm) == []


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
# Claimed proposal with no tool call
# ---------------------------------------------------------------------------

# What a small model wrote in place of calling add_flight, copying its history's note.
PHANTOM = (
    "I've proposed adding a return flight from NRT to PHL.\n\n"
    "[Proposed changes (approved):\n- Add flight “Flight NRT to PHL” on 2027-05-08 14:00]"
)


async def test_a_claimed_proposal_without_a_tool_call_gets_a_nudge(ctx: ToolContext) -> None:
    llm = FakeLLM([text(PHANTOM), tool_calls(("add_flight", RETURN)), text("It's on the card.")])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "Yes, fly me home on the 8th", staged=staged)

    assert llm.requests[1].messages[-2:] == [
        LLMMessage(role="assistant", content=PHANTOM),
        LLMMessage(role="system", content=PHANTOM_PROPOSAL_NUDGE),
    ]
    assert [change.name for change in staged] == ["add_flight"]
    assert _text(events) == f"{PHANTOM}\n\nIt's on the card."


async def test_the_claimed_proposal_nudge_is_given_once_a_turn(ctx: ToolContext) -> None:
    llm = FakeLLM([text("I\u2019ve proposed the flight."), text("I proposed it, honest.")])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    assert llm.pending == 0
    assert _system_notes(llm) == [PHANTOM_PROPOSAL_NUDGE]
    assert staged == []
    # The repeated claim is held back; the user gets a correction instead of a second copy.
    assert _text(events) == f"I\u2019ve proposed the flight.\n\n{PHANTOM_PROPOSAL_NOTE}"


# What qwen3:8b wrote (twice) instead of calling add_activity three times.
ADDED_CLAIM = (
    "I've added the following activities to your itinerary:\n\n- **TeamLab Planets**\n"
    "- **Shibuya Sky**\n\nYou can now press Next to move on to ground transport."
)


async def test_a_claimed_addition_without_a_tool_call_gets_the_nudge(ctx: ToolContext) -> None:
    for claim in (
        ADDED_CLAIM,
        "I have included TeamLab Planets in your plan.",
        "TeamLab Planets has been added to your itinerary.",
        "These activities are now on your trip.",
    ):
        llm = FakeLLM([text(claim), text("Which day suits you for TeamLab?")])

        events = await _run(llm, ctx, "sounds good, lets add those.")

        assert _system_notes(llm) == [PHANTOM_PROPOSAL_NUDGE], claim
        # A different, honest second reply (no claim) is kept after the first.
        assert _text(events) == f"{claim}\n\nWhich day suits you for TeamLab?", claim


async def test_a_repeated_claim_after_the_nudge_becomes_a_note(ctx: ToolContext) -> None:
    llm = FakeLLM([text(ADDED_CLAIM), text(ADDED_CLAIM)])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "sounds good, lets add those.", staged=staged)

    assert staged == []
    assert llm.pending == 0
    assert _text(events) == f"{ADDED_CLAIM}\n\n{PHANTOM_PROPOSAL_NOTE}"


async def test_the_nudge_round_that_calls_the_tool_keeps_its_text(ctx: ToolContext) -> None:
    proposing = tool_calls(("add_lodging", HOTEL_ARGS), content="Proposing now.")
    llm = FakeLLM([text(ADDED_CLAIM), proposing, text("Done.")])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged)

    assert [change.name for change in staged] == ["add_lodging"]
    assert _text(events) == f"{ADDED_CLAIM}\n\nProposing now.\n\nDone."


async def test_confirming_an_approval_is_not_a_claimed_proposal(ctx: ToolContext) -> None:
    confirmation = "Hotel Okura has been added to your itinerary. Press Next for activities."
    llm = FakeLLM([text(confirmation)])

    events = await _run(llm, ctx, approval_applied_message(["Added lodging Hotel Okura"]))

    assert _system_notes(llm) == []
    assert _text(events) == confirmation


async def test_a_plain_answer_or_a_real_proposal_gets_no_nudge(ctx: ToolContext) -> None:
    plain = FakeLLM([text("Which day would you like to fly home?")])
    real = FakeLLM([tool_calls(("add_lodging", HOTEL_ARGS)), text("I've proposed Hotel Okura.")])

    await _run(plain, ctx)
    await _run(real, ctx)

    assert _system_notes(plain) == _system_notes(real) == []


# ---------------------------------------------------------------------------
# Promised search with no tool call
# ---------------------------------------------------------------------------

# What qwen3:8b wrote after its made-up source_url was rejected, then ended the turn.
PROMISED = "I see. Let me search for “Clube de Fado in Lisbon” first. One moment please."
SEARCH = ("web_search", {"query": "Clube de Fado Lisbon", "kind": "activities"})


async def test_a_promised_search_without_a_tool_call_gets_a_nudge(ctx: ToolContext) -> None:
    llm = FakeLLM([text(PROMISED), tool_calls(SEARCH), text("Here is what I found.")])

    events = await _run(llm, ctx, "Book dinner at Clube de Fado")

    assert llm.requests[1].messages[-2:] == [
        LLMMessage(role="assistant", content=PROMISED),
        LLMMessage(role="system", content=PROMISED_SEARCH_NUDGE),
    ]
    (result,) = _of_type(events, ToolResultEvent)
    assert result.name == "web_search" and result.ok
    assert _text(events) == f"{PROMISED}\n\nHere is what I found."


async def test_the_promised_search_nudge_is_given_once_a_turn(ctx: ToolContext) -> None:
    llm = FakeLLM([text("I should first search for it."), text("I'll look it up soon.")])

    await _run(llm, ctx)

    assert llm.pending == 0
    assert _system_notes(llm) == [PROMISED_SEARCH_NUDGE]


async def test_talk_of_searching_after_a_search_or_as_advice_gets_no_nudge(
    ctx: ToolContext,
) -> None:
    searched = FakeLLM([tool_calls(SEARCH), text("I searched; here are two options.")])
    advice = FakeLLM([text("You could search for fado houses in Alfama.")])

    await _run(searched, ctx)
    await _run(advice, ctx)

    assert _system_notes(searched) == _system_notes(advice) == []


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


# ---------------------------------------------------------------------------
# Review: withdrawing changes that don't fit the request
# ---------------------------------------------------------------------------


class _Reviewer:
    """A scripted review callback: each call returns the next list of rejections (none
    once the script runs out), and records what it was asked."""

    def __init__(self, *verdicts: list[tuple[int, str]]) -> None:
        self._verdicts = deque(verdicts)
        self.calls: list[tuple[str, list[ProposedChange]]] = []

    async def __call__(
        self, latest: str, changes: Sequence[ProposedChange]
    ) -> list[tuple[int, str]]:
        self.calls.append((latest, list(changes)))
        return self._verdicts.popleft() if self._verdicts else []


DINNER_AS_LODGING: dict[str, Any] = {
    **HOTEL_ARGS,
    "title": "Dinner at Alchimiste",
    "kind": "other",
    "start": "2027-05-03T19:00:00",
    "end": "2027-05-04T09:00:00",
}
_DINNER_SUMMARY = "Add lodging “Dinner at Alchimiste” on 2027-05-03 19:00"
_NOT_A_MEAL = "they asked for dinner, not somewhere to stay"


async def test_an_approved_card_is_reviewed_once_and_kept(ctx: ToolContext) -> None:
    llm = FakeLLM([tool_calls(("add_lodging", HOTEL_ARGS)), text("Proposed.")])
    review = _Reviewer()
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "Hotel Okura please", staged=staged, review=review)

    ((latest, changes),) = review.calls
    assert latest == "Hotel Okura please" and changes == staged
    assert len(staged) == 1 and len(_of_type(events, ToolResultEvent)) == 1


async def test_a_rejected_change_is_withdrawn_and_the_model_told_why(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", DINNER_AS_LODGING)),
            text("I've proposed dinner."),
            text("Sorry, dinner belongs to the activities stage."),
        ]
    )
    review = _Reviewer([(0, _NOT_A_MEAL)])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "Book dinner at Alchimiste", staged=staged, review=review)

    assert staged == []
    proposed, withdrawn = _of_type(events, ToolResultEvent)
    assert withdrawn.call_id == proposed.call_id and withdrawn.name == "add_lodging"
    assert not withdrawn.ok and not withdrawn.proposed
    assert withdrawn.summary == f"Withdrawn: {_DINNER_SUMMARY} — {_NOT_A_MEAL}"
    assert llm.requests[2].messages[-2:] == [
        LLMMessage(role="assistant", content="I've proposed dinner."),
        LLMMessage(role="system", content=review_feedback([(_DINNER_SUMMARY, _NOT_A_MEAL)])),
    ]
    # Nothing left to review, and the model explained itself: no note for the user.
    assert len(review.calls) == 1
    assert WITHDRAWN_NOTE not in _text(events)
    assert _text(events).endswith("Sorry, dinner belongs to the activities stage.")


async def test_a_bundle_is_withdrawn_as_a_unit(ctx: ToolContext) -> None:
    round_trip = tool_calls(("add_round_trip", {"outbound": OUTBOUND, "return": RETURN}))
    llm = FakeLLM([round_trip, text("Proposed both flights."), text("Which flights then?")])
    # Only the flight home is rejected; the flight out goes with it.
    review = _Reviewer([(1, "they asked for one way")])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, "Fly me there", staged=staged, review=review)

    assert staged == []
    proposed, withdrawn = _of_type(events, ToolResultEvent)
    assert withdrawn.call_id == proposed.call_id and withdrawn.name == "add_round_trip"
    assert withdrawn.summary.startswith("Withdrawn: Add flight “PHL to HND”")
    assert "; Add flight “HND to PHL”" in withdrawn.summary


async def test_only_the_rejected_call_is_withdrawn(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", HOTEL_ARGS), ("add_lodging", DINNER_AS_LODGING)),
            text("Proposed both."),
            text("The hotel is on the card."),
        ]
    )
    review = _Reviewer([(1, _NOT_A_MEAL)])
    staged: list[ProposedChange] = []

    await _run(llm, ctx, "Hotel Okura, and dinner", staged=staged, review=review)

    assert [change.arguments["title"] for change in staged] == ["Hotel Okura"]
    # The card didn't change after the withdrawal, so it isn't reviewed again.
    assert len(review.calls) == 1


async def test_after_a_rejection_a_corrected_proposal_is_kept(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", DINNER_AS_LODGING)),
            text("Proposed."),
            tool_calls(("add_lodging", HOTEL_ARGS)),
            text("I've proposed Hotel Okura instead."),
        ]
    )
    review = _Reviewer([(0, _NOT_A_MEAL)], [])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged, review=review)

    assert [change.arguments["title"] for change in staged] == ["Hotel Okura"]
    assert len(review.calls) == 2
    assert WITHDRAWN_NOTE not in _text(events)


async def test_a_second_rejection_is_withdrawn_with_a_note(ctx: ToolContext) -> None:
    llm = FakeLLM(
        [
            tool_calls(("add_lodging", DINNER_AS_LODGING)),
            text("Proposed."),
            tool_calls(("add_lodging", DINNER_AS_LODGING)),
            text("Proposed again."),
        ]
    )
    review = _Reviewer([(0, _NOT_A_MEAL)], [(0, "still a meal")])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged, review=review)

    assert staged == []
    assert llm.pending == 0 and len(review.calls) == 2
    assert _text(events).endswith(
        f"Proposed again.\n\n{WITHDRAWN_NOTE}\n- {_DINNER_SUMMARY} — still a meal"
    )


async def test_a_rejection_in_the_final_round_is_withdrawn_without_a_retry(
    ctx: ToolContext,
) -> None:
    looping = tool_calls(("remove_item", {"item_id": "00000000-0000-0000-0000-000000000000"}))
    llm = FakeLLM([tool_calls(("add_lodging", DINNER_AS_LODGING))])
    llm.queue(*[looping] * (MAX_TOOL_ROUNDS - 1), text("I'm out of steps."))
    review = _Reviewer([(0, _NOT_A_MEAL)])
    staged: list[ProposedChange] = []

    events = await _run(llm, ctx, staged=staged, review=review)

    assert staged == []
    assert len(llm.requests) == MAX_TOOL_ROUNDS + 1
    assert _text(events).endswith(
        f"I'm out of steps.\n\n{WITHDRAWN_NOTE}\n- {_DINNER_SUMMARY} — {_NOT_A_MEAL}"
    )


async def test_nothing_staged_means_no_review(ctx: ToolContext) -> None:
    review = _Reviewer()

    await _run(FakeLLM([text("Which dates?")]), ctx, review=review)

    assert review.calls == []
