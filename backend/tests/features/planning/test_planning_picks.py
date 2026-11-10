"""Picks over HTTP: choosing from the open choices writes at once, then the planner carries
on; "More options" searches again without asking it. The searches are recording fakes
(canned results), the model a strict `FakeLLM` scripted with tool calls.

The plan has every basic and every preference (`conftest.turn`): Lisbon, May 1-8 2027,
two adults from Philadelphia.
"""

from decimal import Decimal
from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import JsonValue

from features.itinerary.schemas import ActivityItem, FlightItem, LodgingItem
from features.planning.agent.tools import TurnContext
from features.planning.events import PlanningEvent, PlanningStatusEvent
from features.planning.schemas import OfferChoice, PickRecord
from features.plans import service as plans_service
from features.plans.schemas import PlanUpdate, SlotName
from shared.domain import Money
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.hotels.types import HotelOption, HotelSearchError
from shared.llm.fake import FakeLLM, text
from tests.features.planning.agent_helpers import brief_of, set_preferences
from tests.features.planning.planning_http import (
    choices,
    done,
    messages,
    pick,
    post,
    status_of,
    stored,
)
from tests.features.planning.scripts import present, search

NIGHTS = "uncovered_night:2027-05-01"
MORE = ("message", "Show me other options.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def plan_id(turn: TurnContext) -> str:
    await set_preferences(turn)
    return str(turn.plan_id)


async def _offered(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    kind: str,
    problem: str | None = None,
) -> OfferChoice:
    """The planner searches and shows the choices."""
    fake_llm.queue(search(kind, problem), present())
    return choices(await post(client, headers, plan_id, "continue"))


async def _more(client: AsyncClient, headers: dict[str, str], plan_id: str) -> list[PlanningEvent]:
    return await post(client, headers, plan_id, "continue", {"refresh": True})


def _hotels(count: int) -> list[HotelOption]:
    """`count` 3-star hotels, best rated first: "Hotel 1" ranks above "Hotel 2"."""
    return [
        HotelOption(
            name=f"Hotel {n}",
            nightly_rate=Money(amount=Decimal(100), currency="USD"),
            total_rate=Money(amount=Decimal(700), currency="USD"),
            stars=3,
            rating=5 - n / 10,
        )
        for n in range(1, count + 1)
    ]


# ---------------------------------------------------------------------------
# Picking
# ---------------------------------------------------------------------------


async def test_picking_the_flight_out_adds_it_then_the_planner_looks_for_the_way_home(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    fake_flights: FakeFlightProvider,
) -> None:
    offer = await _offered(client, headers, plan_id, fake_llm, "flight_out")
    fake_llm.queue(search("flight_home"), present())

    events = await pick(client, headers, plan_id, offer, "1")

    assert stored(events) == [
        ("pick", "Example Air EX 101"),
        ("choices", "Pick your flight home."),
    ]
    statuses = [e.text for e in events if isinstance(e, PlanningStatusEvent)]
    assert statuses == ["Finding flights home…"]
    [flight] = [item for item in done(events).plan.items if isinstance(item, FlightItem)]
    assert flight.direction == "outbound"
    assert choices(events).slot == SlotName.FLIGHT_RETURN
    # The pick kept the flight out's token, so the flights home are the ones that pair with it.
    assert fake_flights.searches[1].departure_token is not None
    # The planner saw the plan with the flight in it.
    brief = brief_of(fake_llm.requests[2])
    assert [item.type for item in brief.plan.items] == ["flight"]
    assert brief.conversation[-1].text == "Example Air EX 101"


async def test_the_transcript_keeps_the_choices_and_the_pick(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    offer = await _offered(client, headers, plan_id, fake_llm, "flight_out")
    fake_llm.queue(text("Now for the way home."))
    await pick(client, headers, plan_id, offer, "2")

    shown, picked, _ = await messages(client, headers, plan_id)

    assert (shown.role, shown.kind) == ("assistant", "choices")
    assert isinstance(shown.payload, OfferChoice)
    assert shown.payload.offer_id == offer.offer_id
    assert (picked.role, picked.kind) == ("user", "pick")
    assert isinstance(picked.payload, PickRecord)
    assert picked.payload.candidate_ids == ["2"]
    assert picked.payload.titles == [offer.candidates[1].title]


async def test_several_things_to_do_are_picked_at_once(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    offer = await _offered(client, headers, plan_id, fake_llm, "activities")
    assert offer.max_picks > 1
    fake_llm.queue(text("Both are in."))

    events = await pick(client, headers, plan_id, offer, "1", "2")

    activities = [item for item in done(events).plan.items if isinstance(item, ActivityItem)]
    assert sorted(a.title for a in activities) == sorted(c.title for c in offer.candidates[:2])


async def test_a_pick_from_choices_not_being_shown_is_a_conflict(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    await _offered(client, headers, plan_id, fake_llm, "flight_out")
    body: dict[str, JsonValue] = {"type": "pick", "offer_id": str(uuid4()), "candidate_ids": ["1"]}

    assert (await status_of(client, headers, plan_id, "inputs", body))[0] == 409


async def test_a_pick_the_choices_do_not_allow_is_rejected(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    offer = await _offered(client, headers, plan_id, fake_llm, "flight_out")

    for ids, detail in [
        (["9"], "There's no option 9 in this list"),
        (["1", "2"], "Pick 1 option"),
        (["1", "1"], "Pick each option only once"),
    ]:
        body: dict[str, JsonValue] = {
            "type": "pick",
            "offer_id": str(offer.offer_id),
            "candidate_ids": list(ids),
        }
        assert await status_of(client, headers, plan_id, "inputs", body) == (422, detail)


async def test_a_pick_that_breaks_the_budget_changes_nothing(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    turn: TurnContext,
    fake_llm: FakeLLM,
) -> None:
    budget = PlanUpdate(budget=Money(amount=Decimal(1000), currency="USD"))
    await plans_service.update_plan(turn.session, turn.user_id, turn.plan_id, budget)
    offer = await _offered(client, headers, plan_id, fake_llm, "flight_out")
    fake_llm.queue(text("That one is over the budget. The other is cheaper."))

    events = await pick(client, headers, plan_id, offer, "1")

    (_, picked), (kind, note), _ = stored(events)
    assert picked == "Example Air EX 101"
    assert kind == "nudge"
    assert note.startswith("That one can't be added: With Flight PHL to LIS the trip")
    # Nothing was written, so the same choices are still open.
    plan = done(events).plan
    assert plan.items == []
    assert choices(events).offer_id == offer.offer_id


# ---------------------------------------------------------------------------
# More options
# ---------------------------------------------------------------------------


async def test_more_options_lists_stays_not_offered_yet(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    fake_hotels: FakeHotelProvider,
) -> None:
    fake_hotels.queue(_hotels(7), _hotels(7), _hotels(7))
    first = await _offered(client, headers, plan_id, fake_llm, "stay", NIGHTS)
    assert first.can_refresh is True
    assert [c.title for c in first.candidates] == [f"Hotel {n}" for n in range(1, 6)]

    events = await _more(client, headers, plan_id)

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Finding places to stay…"
    assert stored(events) == [MORE, ("choices", "Pick a place to stay.")]
    second = choices(events)
    assert second.offer_id != first.offer_id
    assert [(c.id, c.title) for c in second.candidates] == [("1", "Hotel 6"), ("2", "Hotel 7")]
    assert second.nights == first.nights

    # Every stay has now been offered: the second list stays up.
    events = await _more(client, headers, plan_id)

    assert stored(events) == [
        MORE,
        (
            "nudge",
            "Those are all the options I found. Pick one of these, or go back and change the trip.",
        ),
    ]
    assert choices(events).offer_id == second.offer_id
    assert len(fake_hotels.searches) == 3
    # The button is the search itself: the planner was only asked for the first list.
    assert len(fake_llm.requests) == 2


async def test_a_stay_from_more_options_can_be_picked(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    fake_hotels: FakeHotelProvider,
) -> None:
    fake_hotels.queue(_hotels(6), _hotels(6))
    await _offered(client, headers, plan_id, fake_llm, "stay", NIGHTS)
    offer = choices(await _more(client, headers, plan_id))
    fake_llm.queue(text("Hotel 6 it is."))

    events = await pick(client, headers, plan_id, offer, "1")

    [stay] = [item for item in done(events).plan.items if isinstance(item, LodgingItem)]
    assert stay.title == "Hotel 6"


async def test_more_options_keeps_the_choices_when_the_search_fails(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    fake_hotels: FakeHotelProvider,
) -> None:
    offer = await _offered(client, headers, plan_id, fake_llm, "stay", NIGHTS)
    fake_hotels.queue(HotelSearchError("Hotel search timed out."))

    events = await _more(client, headers, plan_id)

    assert stored(events) == [MORE, ("error", "Hotel search timed out.")]
    assert choices(events).offer_id == offer.offer_id


async def test_more_options_needs_choices_to_be_showing(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_hotels: FakeHotelProvider
) -> None:
    status, detail = await status_of(client, headers, plan_id, "continue", {"refresh": True})

    assert (status, detail) == (409, "There are no choices to look for other options for.")
    assert fake_hotels.searches == []
