"""The activities stage over HTTP: the preferences search for each interest, the places come
back scheduled into free windows, and picking several adds them all; then the ground stage
asks for a way across each gap, one at a time, until the trip reaches review. The place
search is a recording fake (canned places), the model a strict `FakeLLM`."""

from decimal import Decimal
from typing import Any

from fastapi import FastAPI
from httpx import AsyncClient

from features.itinerary.schemas import ActivityItem, TransferItem
from features.planning.events import PlanningStatusEvent
from features.planning.schemas import OfferChoice
from features.plans.schemas import PlanStage
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.llm.fake import FakeLLM, json_reply
from shared.places.fake import FakePlaceProvider
from shared.places.types import PlaceSearchError
from tests.features.planning.planning_http import (
    BASICS,
    answer,
    create_plan,
    done,
    messages,
    move_to,
    post,
    say,
    stored,
)

MUSEUMS_AND_FOOD = {"interests": ["museum", "food"]}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _at(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], stage: PlanStage, **fields: Any
) -> str:
    """A Lisbon trip, May 1-8 2027 for two, from Philadelphia, at `stage`."""
    plan_id = await create_plan(client, headers, **{**BASICS, **fields})
    await move_to(app, plan_id, stage)
    return plan_id


def _choices(events: list[Any]) -> OfferChoice:
    action = done(events).plan.assessment.next_action
    assert isinstance(action, OfferChoice), action
    return action


async def _pick(
    client: AsyncClient, headers: dict[str, str], plan_id: str, offer: OfferChoice, *ids: str
) -> list[Any]:
    body = {"type": "pick", "offer_id": str(offer.offer_id), "candidate_ids": list(ids)}
    return await post(client, headers, plan_id, "inputs", body)


def _activities(events: list[Any]) -> list[ActivityItem]:
    items = done(events).plan.items
    return sorted((i for i in items if isinstance(i, ActivityItem)), key=lambda a: a.start)


def _transfers(events: list[Any]) -> list[TransferItem]:
    items = done(events).plan.items
    return sorted((i for i in items if isinstance(i, TransferItem)), key=lambda t: t.start)


# ---------------------------------------------------------------------------
# Activities
# ---------------------------------------------------------------------------


async def test_answering_the_preferences_searches_each_interest(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_places: FakePlaceProvider
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES)

    events = await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Find things to do…"
    assert stored(events) == [
        ("input", "Museum, Food"),
        ("choices", "Pick the things you'd like to do."),
    ]
    assert [(q.what, q.near) for q in fake_places.searches] == [
        ("museums", "Lisbon, Portugal"),
        ("restaurants", "Lisbon, Portugal"),
    ]
    offer = _choices(events)
    # The interests take turns, best rated first within each.
    assert [c.title for c in offer.candidates] == [
        "Example Art Museum",
        "Example Bistro",
        "Example History Museum",
        "Example Tasca",
    ]
    assert (offer.min_picks, offer.max_picks) == (1, 8)
    # Each has its own window: museums in the morning (not on Monday May 3, when they're
    # closed), restaurants at dinner, spread over the trip.
    assert [c.details[0] for c in offer.candidates] == [
        "Sat May 1, 10:00-12:00",
        "Sun May 2, 19:30-21:00",
        "Tue May 4, 10:00-12:00",
        "Mon May 3, 19:30-21:00",
    ]


async def test_picking_several_adds_them_all_and_moves_on(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_places: FakePlaceProvider
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES)
    offer = _choices(
        await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)
    )

    events = await _pick(client, headers, plan_id, offer, "1", "2")

    kinds = stored(events)
    assert kinds[0] == ("pick", "Example Art Museum; Example Bistro")
    assert kinds[1] == ("stage", "Activities done. Next up: ground transport.")
    assert kinds[2][0] == "choices"
    museum, dinner = _activities(events)
    assert (museum.category, dinner.category) == ("museum", "food")
    assert museum.timezone == "Europe/Lisbon"
    assert museum.start.isoformat() == "2027-05-01T10:00:00+01:00"
    assert museum.place.city == "Lisbon"
    # Estimates for two people: the fake bistro is "$$".
    assert dinner.cost is not None
    assert (dinner.cost.amount, dinner.cost.basis) == (Decimal(70), "estimate")
    assert done(events).plan.stage == "ground"


async def test_a_pick_over_the_category_limit_changes_nothing(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_places: FakePlaceProvider
) -> None:
    limits = [{"category": "food", "max_per_person": {"amount": 20, "currency": "USD"}}]
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES, limits=limits)
    offer = _choices(
        await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)
    )
    bistro = next(c for c in offer.candidates if c.title == "Example Bistro")
    assert bistro.warnings == ["about 35.00 USD a person, over the food limit of 20.00 USD"]

    events = await _pick(client, headers, plan_id, offer, "1", bistro.id)

    (_, _), (kind, note), again = stored(events)
    assert kind == "nudge"
    assert note.startswith("That one can't be added: Example Bistro costs")
    assert again == ("choices", "Pick the things you'd like to do.")
    assert _activities(events) == []


async def test_one_failed_search_still_lists_the_others(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_places: FakePlaceProvider
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES)
    fake_places.queue(PlaceSearchError("Place search timed out."))

    events = await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)

    assert [c.title for c in _choices(events).candidates] == ["Example Bistro", "Example Tasca"]


async def test_every_search_failing_offers_retry(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_places: FakePlaceProvider
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES)
    fake_places.queue(
        PlaceSearchError("Place search timed out."), PlaceSearchError("Place search timed out.")
    )

    events = await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)

    assert stored(events)[-1] == ("error", "Place search timed out.")
    action = done(events).plan.assessment.next_action
    assert action.type == "run_search" and action.kind == "activities"

    events = await post(client, headers, plan_id, "continue")

    assert stored(events) == [("choices", "Pick the things you'd like to do.")]
    assert len(fake_places.searches) == 4


async def test_free_text_picks_several(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    fake_places: FakePlaceProvider,
    fake_llm: FakeLLM,
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES)
    await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)
    fake_llm.queue(json_reply({"status": "answered", "candidate_ids": ["1", "4"]}))

    events = await say(client, headers, plan_id, "the art museum and the tasca")

    assert stored(events)[1] == (
        "message",
        "Got it: Example Art Museum; Example Tasca.",
    )
    assert [a.title for a in _activities(events)] == ["Example Art Museum", "Example Tasca"]


# ---------------------------------------------------------------------------
# Ground transport, then review
# ---------------------------------------------------------------------------


async def test_ground_asks_for_each_gap_in_turn_then_completes(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_places: FakePlaceProvider
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES)
    offer = _choices(
        await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)
    )
    events = await _pick(client, headers, plan_id, offer, "1")
    # Home → the museum → home: two gaps.
    first = _choices(events)
    assert first.prompt == "How do you want to get there?"
    assert first.gap is not None and first.gap.to_place.name == "Example Art Museum"

    events = await _pick(client, headers, plan_id, first, "1")

    second = _choices(events)
    assert second.offer_id != first.offer_id
    assert second.gap is not None and second.gap.from_place.name == "Example Art Museum"

    events = await _pick(client, headers, plan_id, second, "1")

    assert stored(events)[-1] == ("stage", "Ground transport done. Next up: review.")
    plan = done(events).plan
    assert plan.stage == "review"
    assert plan.assessment.next_action.type == "complete"
    out, back = _transfers(events)
    museum = _activities(events)[0]
    # The ride out arrives as the museum opens its doors; the ride back leaves as it ends.
    assert out.end == museum.start
    assert back.start == museum.end


async def test_the_transcript_keeps_the_gap_with_its_choices(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_places: FakePlaceProvider
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.ACTIVITIES)
    offer = _choices(
        await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)
    )
    await _pick(client, headers, plan_id, offer, "1")

    rows = await messages(client, headers, plan_id)
    choices = [row for row in rows if row["kind"] == "choices"]

    assert choices[-1]["payload"]["slot"] == "transfer"
    assert choices[-1]["payload"]["gap"]["to_place"]["name"] == "Example Art Museum"


async def test_a_whole_trip_ends_at_review_with_nothing_to_fix(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    fake_flights: FakeFlightProvider,
    fake_hotels: FakeHotelProvider,
    fake_places: FakePlaceProvider,
) -> None:
    plan_id = await _at(app, client, headers, PlanStage.FLIGHTS)
    events = await answer(client, headers, plan_id, "flight_preferences", {"cabin": "economy"})
    events = await _pick(client, headers, plan_id, _choices(events), "1")  # out
    events = await _pick(client, headers, plan_id, _choices(events), "1")  # home
    events = await answer(
        client, headers, plan_id, "lodging_preferences", {"hotel_class": "midrange"}
    )
    events = await _pick(client, headers, plan_id, _choices(events), "1")
    events = await answer(client, headers, plan_id, "activity_preferences", MUSEUMS_AND_FOOD)
    events = await _pick(client, headers, plan_id, _choices(events), "1", "2")

    # Every gap, home to home, takes the first way offered.
    picked = 0
    while done(events).plan.stage == "ground":
        events = await _pick(client, headers, plan_id, _choices(events), "1")
        picked += 1

    plan = done(events).plan
    assert plan.stage == "review"
    assert plan.assessment.next_action.type == "complete"
    # Home → airport, airport → hotel, hotel ↔ the museum and the bistro, hotel → airport,
    # airport → home.
    assert picked == 8
    assert [(issue.code, issue.message) for issue in plan.assessment.issues] == []
