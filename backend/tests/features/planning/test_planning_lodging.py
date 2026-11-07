"""The lodging stage over HTTP: the preferences start a search for the first run of nights
without a place to stay, picking one adds it, and the planner searches the next run until
every night is covered. The hotel search is a recording fake (canned hotels), the model a
strict `FakeLLM`."""

from decimal import Decimal

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue

from features.itinerary.schemas import LodgingItem
from features.planning.events import PlanningEvent, PlanningStatusEvent
from features.planning.schemas import NightRange, OfferChoice, PickRecord
from features.plans.schemas import PlanStage, SlotName
from shared.domain import Money
from shared.hotels.fake import FakeHotelProvider
from shared.hotels.types import HotelOption, HotelSearchError
from shared.llm.fake import FakeLLM, json_reply
from tests.features.planning.planning_http import (
    BASICS,
    answer,
    create_plan,
    done,
    messages,
    post,
    reach,
    say,
    stored,
)

MIDRANGE: dict[str, JsonValue] = {"hotel_class": "midrange", "kind": "hotel"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _at_lodging(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], **fields: JsonValue
) -> str:
    """A Lisbon trip, May 1-8 for two, with no flights, at the lodging stage."""
    plan_id = await create_plan(client, headers, **{**BASICS, **fields})
    await reach(app, client, headers, plan_id, PlanStage.LODGING)
    return plan_id


def _choices(events: list[PlanningEvent]) -> OfferChoice:
    action = done(events).plan.assessment.next_action
    assert isinstance(action, OfferChoice), action
    return action


async def _pick(
    client: AsyncClient, headers: dict[str, str], plan_id: str, offer: OfferChoice, *ids: str
) -> list[PlanningEvent]:
    body: dict[str, JsonValue] = {
        "type": "pick",
        "offer_id": str(offer.offer_id),
        "candidate_ids": list(ids),
    }
    return await post(client, headers, plan_id, "inputs", body)


def _nights(check_in: str, check_out: str) -> NightRange:
    return NightRange.model_validate({"check_in": check_in, "check_out": check_out})


def _stays(events: list[PlanningEvent]) -> list[LodgingItem]:
    items = done(events).plan.items
    return sorted((i for i in items if isinstance(i, LodgingItem)), key=lambda s: s.start)


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


async def _more(client: AsyncClient, headers: dict[str, str], plan_id: str) -> list[PlanningEvent]:
    return await post(client, headers, plan_id, "continue", {"refresh": True})


async def _add_stay(
    client: AsyncClient, headers: dict[str, str], plan_id: str, start: str, end: str
) -> None:
    body = {
        "type": "lodging",
        "title": "Friend's flat",
        "start": start,
        "end": end,
        "timezone": "Europe/Lisbon",
        "place": {"name": "Friend's flat", "city": "Lisbon"},
    }
    response = await client.post(f"/plans/{plan_id}/items", json=body, headers=headers)
    assert response.status_code == 201, response.text


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_answering_the_preferences_searches_every_night(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)

    events = await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE)

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Find a place to stay May 1 to May 8…"
    assert stored(events) == [
        ("input", "Midrange hotel"),
        ("choices", "Pick a place to stay."),
    ]
    [query] = fake_hotels.searches
    assert query.location == "Lisbon, Portugal"
    assert (str(query.check_in), str(query.check_out), query.adults) == (
        "2027-05-01",
        "2027-05-08",
        2,
    )
    assert query.stars == (3,)
    offer = _choices(events)
    assert offer.nights == _nights("2027-05-01", "2027-05-08")
    assert [c.title for c in offer.candidates] == [
        "Example Riverside Hotel",
        "Example Midtown Hotel",
    ]


async def test_picking_a_stay_covers_the_nights_and_moves_on(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE))

    events = await _pick(client, headers, plan_id, offer, "2")

    assert stored(events) == [
        ("pick", "Example Midtown Hotel"),
        ("stage", "Lodging done. Next up: activities."),
        ("expected_input", "What do you like to do?"),
    ]
    [stay] = _stays(events)
    assert stay.timezone == "Europe/Lisbon"
    assert (stay.start.isoformat(), stay.end.isoformat()) == (
        "2027-05-01T15:00:00+01:00",
        "2027-05-08T11:00:00+01:00",
    )
    assert (stay.kind, stay.place.city, stay.place.country) == ("hotel", "Lisbon", "Portugal")
    # Seven nights at the fake's 160 a night, for the whole party.
    assert stay.cost is not None and stay.cost.amount == 1120 and stay.cost.basis == "exact"
    assert stay.nightly_rate is not None and stay.nightly_rate.amount == 160
    assert done(events).plan.stage == "activities"


async def test_each_run_of_uncovered_nights_is_searched_in_turn(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    # The nights of May 3 and 4 are already covered.
    await _add_stay(client, headers, plan_id, "2027-05-03T15:00", "2027-05-05T10:00")
    first = _choices(await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE))
    assert first.nights == _nights("2027-05-01", "2027-05-03")

    events = await _pick(client, headers, plan_id, first, "1")

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Find a place to stay May 5 to May 8…"
    second = _choices(events)
    assert second.offer_id != first.offer_id
    assert second.nights == _nights("2027-05-05", "2027-05-08")
    assert [str(q.check_in) for q in fake_hotels.searches] == ["2027-05-01", "2027-05-05"]

    events = await _pick(client, headers, plan_id, second, "1")

    assert done(events).plan.stage == "activities"
    assert len(_stays(events)) == 3


async def test_the_transcript_keeps_the_choices_and_the_pick(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE))
    await _pick(client, headers, plan_id, offer, "1")

    rows = await messages(client, headers, plan_id)
    choices = next(row for row in rows if row.kind == "choices")
    pick = next(row for row in rows if row.kind == "pick")

    assert isinstance(choices.payload, OfferChoice)
    assert choices.payload.nights == _nights("2027-05-01", "2027-05-08")
    assert isinstance(pick.payload, PickRecord)
    assert pick.payload.slot == "lodging_stay"
    assert pick.payload.titles == ["Example Riverside Hotel"]


# ---------------------------------------------------------------------------
# When it doesn't go through
# ---------------------------------------------------------------------------


async def test_a_stay_that_breaks_the_budget_changes_nothing(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers, budget={"amount": 500, "currency": "USD"})
    offer = _choices(await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE))

    events = await _pick(client, headers, plan_id, offer, "1")

    (_, picked), (kind, note), again = stored(events)
    assert picked == "Example Riverside Hotel"
    assert kind == "nudge"
    assert note.startswith("That one can't be added: With Example Riverside Hotel the trip")
    assert again == ("choices", "Pick a place to stay.")
    assert _stays(events) == []
    assert _choices(events).offer_id == offer.offer_id


async def test_a_failed_search_offers_retry(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    fake_hotels.queue(HotelSearchError("Hotel search timed out."))

    events = await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE)

    assert stored(events)[-1] == ("error", "Hotel search timed out.")
    action = done(events).plan.assessment.next_action
    assert action.type == "run_search" and action.slot == SlotName.LODGING_STAY

    events = await post(client, headers, plan_id, "continue")

    assert stored(events) == [("choices", "Pick a place to stay.")]
    assert len(fake_hotels.searches) == 2


async def test_free_text_picks_a_stay(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    fake_hotels: FakeHotelProvider,
    fake_llm: FakeLLM,
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE)
    fake_llm.queue(json_reply({"status": "answered", "candidate_ids": ["2"]}))

    events = await say(client, headers, plan_id, "the cheaper one")

    assert stored(events)[:2] == [
        ("message", "the cheaper one"),
        ("message", "Got it: Example Midtown Hotel."),
    ]
    assert [stay.title for stay in _stays(events)] == ["Example Midtown Hotel"]


# ---------------------------------------------------------------------------
# More options
# ---------------------------------------------------------------------------

MORE = ("message", "Show me other options.")


async def test_more_options_lists_stays_not_offered_yet(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    fake_hotels.queue(_hotels(7), _hotels(7), _hotels(7))
    first = _choices(await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE))
    assert first.can_refresh is True
    assert [c.title for c in first.candidates] == [f"Hotel {n}" for n in range(1, 6)]

    events = await _more(client, headers, plan_id)

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Find a place to stay May 1 to May 8…"
    assert stored(events) == [MORE, ("choices", "Pick a place to stay.")]
    second = _choices(events)
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
        ("choices", "Pick a place to stay."),
    ]
    assert _choices(events).offer_id == second.offer_id
    assert len(fake_hotels.searches) == 3


async def test_a_stay_from_more_options_can_be_picked(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    fake_hotels.queue(_hotels(6), _hotels(6))
    await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE)
    offer = _choices(await _more(client, headers, plan_id))

    events = await _pick(client, headers, plan_id, offer, "1")

    [stay] = _stays(events)
    assert stay.title == "Hotel 6"


async def test_more_options_keeps_the_choices_when_the_search_fails(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "lodging_preferences", MIDRANGE))
    fake_hotels.queue(HotelSearchError("Hotel search timed out."))

    events = await _more(client, headers, plan_id)

    assert stored(events) == [
        MORE,
        ("error", "Hotel search timed out."),
        ("choices", "Pick a place to stay."),
    ]
    assert _choices(events).offer_id == offer.offer_id


async def test_more_options_needs_choices_to_be_showing(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_hotels: FakeHotelProvider
) -> None:
    plan_id = await _at_lodging(app, client, headers)  # asking for the preferences

    response = await client.post(
        f"/plans/{plan_id}/planning/continue", json={"refresh": True}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "There are no choices to look for other options for."
    assert fake_hotels.searches == []
