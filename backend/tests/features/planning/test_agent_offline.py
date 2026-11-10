"""The offline policy (`LLM_PROVIDER=fake`) playing the model's part in the planner's loop,
against the canned searches: enough to plan a whole trip, and to suggest a change.
"""

from dataclasses import replace
from uuid import UUID

import pytest
from httpx import AsyncClient

from features.chat.schemas import MessageKind
from features.planning.agent.offline import (
    COMPLETE,
    NOTHING_TO_DROP,
    OFFLINE_ANSWER,
    PICK_ABOVE,
    SUGGEST_REASON,
    agent_reply,
)
from features.planning.agent.tools import TurnContext, store
from features.planning.pending import ask_input
from features.planning.schemas import AskInput, OfferChoice, ReviewProposal
from features.plans.schemas import SlotName
from shared.llm.fake import FakeLLM
from tests.features.planning.agent_helpers import go, plan_of, say, stored
from tests.features.planning.planning_http import create_plan

FIRST = "The first one."


@pytest.fixture
def offline(turn: TurnContext) -> TurnContext:
    """The turn's context with the fake the app runs offline."""
    return replace(turn, llm=FakeLLM(fallback=agent_reply))


async def _asked(ctx: TurnContext) -> SlotName | None:
    pending = (await plan_of(ctx)).pending
    return pending.slot if isinstance(pending, AskInput) else None


async def _offered(ctx: TurnContext) -> SlotName | None:
    pending = (await plan_of(ctx)).pending
    return pending.slot if isinstance(pending, OfferChoice) else None


async def _with_an_activity(ctx: TurnContext) -> str:
    """Drive to the plan's first thing to do, without flights; returns its title."""
    await say(ctx, "We're driving.")
    await say(ctx, "A midrange hotel.")
    await say(ctx, FIRST)
    await say(ctx, "Museums and food.")
    await say(ctx, FIRST)
    activities = [item for item in (await plan_of(ctx)).items if item.type == "activity"]
    return activities[0].title


# ---------------------------------------------------------------------------
# Answers in words
# ---------------------------------------------------------------------------


async def test_the_basics_are_asked_and_answered_in_words(
    offline: TurnContext, client: AsyncClient, headers: dict[str, str]
) -> None:
    empty = replace(offline, plan_id=UUID(await create_plan(client, headers)))

    # A new plan opens on the destination's form, and the first thing typed answers it.
    assert await _asked(empty) == SlotName.DESTINATION

    events = await say(empty, "Lisbon")
    assert stored(events) == [
        (MessageKind.CHANGES, "Destination: Lisbon, Portugal."),
        (MessageKind.EXPECTED_INPUT, "When are you going?"),
    ]

    await say(empty, "Next month for a week.")
    await say(empty, "Two adults")
    assert await _asked(empty) == SlotName.ORIGIN

    await say(empty, "philadelphia")
    plan = await plan_of(empty)
    # Counted from the turn's today (Nov 1, 2026).
    assert plan.start_date is not None and plan.start_date.isoformat() == "2026-12-01"
    assert plan.travelers is not None and plan.travelers.adults == 2
    # The basics are in, so the flights are next: their preferences first.
    assert await _asked(empty) == SlotName.FLIGHT_PREFERENCES


async def test_an_answer_the_rules_turn_down_is_explained_and_asked_again(
    offline: TurnContext,
) -> None:
    form = ask_input(SlotName.DATES, await plan_of(offline))
    await store(offline, "assistant", form.prompt, kind=MessageKind.EXPECTED_INPUT, payload=form)

    events = await say(offline, "Last month")

    (kind, content), again = stored(events)
    assert kind == MessageKind.MESSAGE and "can't start in the past" in content
    # Nothing changed, so the form is put back for another try.
    assert again == (MessageKind.EXPECTED_INPUT, "When are you going?")


async def test_a_question_gets_the_offline_answer(offline: TurnContext) -> None:
    events = await say(offline, "What's the weather like?")

    assert stored(events) == [(MessageKind.MESSAGE, OFFLINE_ANSWER)]
    assert (await plan_of(offline)).pending is None


async def test_anything_else_carries_on_with_the_trip(offline: TurnContext) -> None:
    await say(offline, "hmm")

    assert await _asked(offline) == SlotName.FLIGHT_PREFERENCES


# ---------------------------------------------------------------------------
# Searching and picking
# ---------------------------------------------------------------------------


async def test_preferences_lead_to_a_search_and_its_choices(offline: TurnContext) -> None:
    await go(offline)
    assert await _asked(offline) == SlotName.FLIGHT_PREFERENCES

    events = await say(offline, "Economy")

    assert stored(events) == [
        (MessageKind.CHANGES, "Flight preferences: Economy cabin, any number of stops."),
        (MessageKind.CHOICES, "Pick your flight out."),
    ]

    # Carrying on with the choices still open doesn't search again.
    assert stored(await go(offline)) == [(MessageKind.MESSAGE, PICK_ABOVE)]

    await say(offline, FIRST)
    assert await _offered(offline) == SlotName.FLIGHT_RETURN


async def test_the_problem_asked_for_is_worked_on_first(offline: TurnContext) -> None:
    await go(offline, focus="no_activities")

    assert await _asked(offline) == SlotName.ACTIVITY_PREFERENCES


async def test_a_whole_trip_can_be_planned_offline(offline: TurnContext) -> None:
    await go(offline)
    await say(offline, "Economy")
    await say(offline, FIRST)  # the flight out
    await say(offline, FIRST)  # the flight home
    await say(offline, "A midrange hotel.")
    await say(offline, FIRST)
    await say(offline, "Museums and food.")
    await say(offline, FIRST)
    # What is left are the rides between them, one pick each.
    for _ in range(12):
        if (await plan_of(offline)).complete:
            break
        assert await _offered(offline) == SlotName.TRANSFER
        await say(offline, FIRST)

    plan = await plan_of(offline)
    assert plan.complete and plan.pending is None
    assert stored(await go(offline)) == [(MessageKind.MESSAGE, COMPLETE)]


# ---------------------------------------------------------------------------
# A suggestion
# ---------------------------------------------------------------------------


async def test_a_suggestion_can_be_approved(offline: TurnContext) -> None:
    title = await _with_an_activity(offline)

    events = await say(offline, "What would you drop?")

    assert stored(events) == [(MessageKind.MESSAGE, SUGGEST_REASON)]
    pending = (await plan_of(offline)).pending
    assert isinstance(pending, ReviewProposal)
    assert [change.summary for change in pending.changes] == [f"Remove “{title}”"]

    events = await say(offline, "Yes")

    kind, content = stored(events)[0]
    assert kind == MessageKind.CHANGES and content.startswith(f"Removed “{title}”.")
    assert title not in [item.title for item in (await plan_of(offline)).items]


async def test_a_suggestion_can_be_rejected(offline: TurnContext) -> None:
    title = await _with_an_activity(offline)
    await say(offline, "What would you drop?")

    events = await say(offline, "No")

    assert stored(events) == [(MessageKind.MESSAGE, "The suggestion was rejected.")]
    plan = await plan_of(offline)
    # The choices the suggestion was made over are what the plan waits on again.
    assert isinstance(plan.pending, OfferChoice)
    assert title in [item.title for item in plan.items]


async def test_nothing_to_suggest_without_something_to_do(offline: TurnContext) -> None:
    events = await say(offline, "What would you drop?")

    assert stored(events) == [(MessageKind.MESSAGE, NOTHING_TO_DROP)]
