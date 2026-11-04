"""The flights stage over HTTP: the preferences start a search, picking the flight out lists
the flights home for it, and picking one of those finishes the stage. The flight search is a
recording fake (canned flights), the model a strict `FakeLLM`."""

from typing import Any
from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient

from features.itinerary.schemas import FlightItem
from features.planning.events import PlanningPlanEvent, PlanningStatusEvent
from features.planning.schemas import OfferChoice
from features.plans.schemas import PlanStage
from shared.flights.fake import FakeFlightProvider
from shared.llm.fake import FakeLLM, json_reply
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

ECONOMY = {"cabin": "economy"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _at_flights(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], **fields: Any
) -> str:
    plan_id = await create_plan(client, headers, **{**BASICS, **fields})
    await move_to(app, plan_id, PlanStage.FLIGHTS)
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


async def _pick_status(
    client: AsyncClient, headers: dict[str, str], plan_id: str, body: dict[str, Any]
) -> tuple[int, str]:
    response = await client.post(f"/plans/{plan_id}/planning/inputs", json=body, headers=headers)
    return response.status_code, response.json()["detail"]


def _flights(events: list[Any]) -> list[FlightItem]:
    items = done(events).plan.items
    return sorted((i for i in items if isinstance(i, FlightItem)), key=lambda f: f.start)


# ---------------------------------------------------------------------------
# The happy path
# ---------------------------------------------------------------------------


async def test_answering_the_preferences_searches_the_flights_out(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)

    events = await answer(client, headers, plan_id, "flight_preferences", ECONOMY)

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Find flights out…"
    assert stored(events) == [
        ("input", "Economy cabin, any number of stops"),
        ("choices", "Pick your flight out."),
    ]
    [search] = fake_flights.searches
    assert (search.query.origin, search.query.destination) == (("PHL",), ("LIS",))
    assert search.query.return_date is not None and search.departure_token is None
    offer = _choices(events)
    assert [c.title for c in offer.candidates] == ["Example Air EX 101", "Example Air EX 102"]


async def test_picking_the_flight_out_adds_it_and_lists_the_flights_home(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "flight_preferences", ECONOMY))

    events = await _pick(client, headers, plan_id, offer, "1")

    assert stored(events) == [
        ("pick", "Example Air EX 101"),
        ("choices", "Pick your flight home."),
    ]
    assert any(isinstance(e, PlanningPlanEvent) for e in events)
    [flight] = _flights(events)
    assert (flight.direction, flight.from_place.iata, flight.to_place.iata) == (
        "outbound",
        "PHL",
        "LIS",
    )
    plan = done(events).plan
    assert plan.preferences.flights is not None
    assert plan.preferences.flights.outbound_token == "fake-departure-0"
    assert fake_flights.searches[-1].departure_token == "fake-departure-0"
    home = _choices(events)
    assert home.slot == "flight_return" and len(home.candidates) == 1


async def test_picking_the_flight_home_finishes_the_stage(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "flight_preferences", ECONOMY))
    home = _choices(await _pick(client, headers, plan_id, offer, "1"))

    events = await _pick(client, headers, plan_id, home, "1")

    assert stored(events) == [
        ("pick", "Example Air EX 101"),
        ("stage", "Flights done. Next up: lodging."),
        ("expected_input", "What kind of place do you want to stay in?"),
    ]
    assert done(events).plan.stage == "lodging"
    out, back = _flights(events)
    assert back.direction == "return"
    # The round-trip fare sits on the flight out; the flight home costs nothing more.
    assert out.cost is not None and out.cost.amount == 1800
    assert back.cost is not None and back.cost.amount == 0


async def test_the_transcript_keeps_the_choices_and_the_pick(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "flight_preferences", ECONOMY))
    await _pick(client, headers, plan_id, offer, "2")

    *_, choices, pick, _home = await messages(client, headers, plan_id)

    assert choices["payload"]["type"] == "offer_choice"
    assert [c["id"] for c in choices["payload"]["candidates"]] == ["1", "2"]
    assert pick["payload"] == {
        "type": "pick",
        "offer_id": str(offer.offer_id),
        "slot": "flight_outbound",
        "candidate_ids": ["2"],
        "titles": ["Example Air EX 102"],
    }


# ---------------------------------------------------------------------------
# Picks that don't go through
# ---------------------------------------------------------------------------


async def test_a_pick_from_an_offer_not_being_shown_is_a_conflict(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)
    body = {"type": "pick", "offer_id": str(uuid4()), "candidate_ids": ["1"]}

    # The preferences are being asked, so there's nothing to pick from yet.
    assert (await _pick_status(client, headers, plan_id, body))[0] == 409
    await answer(client, headers, plan_id, "flight_preferences", ECONOMY)
    # Choices are showing, but from another offer.
    assert (await _pick_status(client, headers, plan_id, body))[0] == 409


async def test_a_pick_the_offer_does_not_allow_is_rejected(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "flight_preferences", ECONOMY))
    offer_id = str(offer.offer_id)

    for ids, detail in ((["9"], "There's no option 9 in this list"), (["1", "2"], "Pick 1 option")):
        body = {"type": "pick", "offer_id": offer_id, "candidate_ids": ids}
        assert await _pick_status(client, headers, plan_id, body) == (422, detail)


async def test_a_pick_that_breaks_the_budget_changes_nothing(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers, budget={"amount": 1000, "currency": "USD"})
    offer = _choices(await answer(client, headers, plan_id, "flight_preferences", ECONOMY))

    events = await _pick(client, headers, plan_id, offer, "1")

    (_, picked), (kind, note), again = stored(events)
    assert picked == "Example Air EX 101"
    assert kind == "nudge"
    assert note.startswith("That one can't be added: With Flight PHL to LIS the trip")
    # The advice written for the old chat model isn't shown to people.
    assert "ask the user" not in note
    # Nothing was written, so the same offer is still the one to pick from.
    assert again == ("choices", "Pick your flight out.")
    assert _flights(events) == []
    assert _choices(events).offer_id == offer.offer_id
    assert len(fake_flights.searches) == 1


# ---------------------------------------------------------------------------
# Free text while choices are showing
# ---------------------------------------------------------------------------


async def test_free_text_picks_from_the_choices(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    fake_flights: FakeFlightProvider,
    fake_llm: FakeLLM,
) -> None:
    plan_id = await _at_flights(app, client, headers)
    await answer(client, headers, plan_id, "flight_preferences", ECONOMY)
    fake_llm.queue(json_reply({"status": "answered", "candidate_ids": ["2"]}))

    events = await say(client, headers, plan_id, "the evening one")

    assert stored(events) == [
        ("message", "the evening one"),
        ("message", "Got it: Example Air EX 102."),
        ("choices", "Pick your flight home."),
    ]
    [flight] = _flights(events)
    assert flight.start.hour == 18


async def test_free_text_that_picks_nothing_gets_the_nudge(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    fake_flights: FakeFlightProvider,
    fake_llm: FakeLLM,
) -> None:
    plan_id = await _at_flights(app, client, headers)
    await answer(client, headers, plan_id, "flight_preferences", ECONOMY)
    fake_llm.queue(json_reply({"status": "unrelated", "candidate_ids": []}))

    events = await say(client, headers, plan_id, "tell me a joke")

    assert stored(events)[-1] == (
        "nudge",
        'Pick one of these flights out, or tell me which one ("the second one").',
    )
    assert _flights(events) == []


async def test_free_text_naming_an_option_that_is_not_there_is_explained(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    fake_flights: FakeFlightProvider,
    fake_llm: FakeLLM,
) -> None:
    plan_id = await _at_flights(app, client, headers)
    await answer(client, headers, plan_id, "flight_preferences", ECONOMY)
    fake_llm.queue(json_reply({"status": "answered", "candidate_ids": ["5"]}))

    events = await say(client, headers, plan_id, "the fifth one")

    assert stored(events)[1:] == [
        ("nudge", "That doesn't work: There's no option 5 in this list."),
        ("choices", "Pick your flight out."),
    ]


# ---------------------------------------------------------------------------
# Skipping flights
# ---------------------------------------------------------------------------


async def test_skipping_flights_moves_on_to_lodging(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)
    await answer(client, headers, plan_id, "flight_preferences", ECONOMY)

    response = await client.post(
        f"/plans/{plan_id}/stage", json={"action": "skip_flights"}, headers=headers
    )
    assert response.status_code == 200, response.text
    events = await post(client, headers, plan_id, "continue")

    assert stored(events) == [("expected_input", "What kind of place do you want to stay in?")]
    plan = done(events).plan
    assert (plan.stage, plan.flights_skipped) == ("lodging", True)
    assert len(fake_flights.searches) == 1


# ---------------------------------------------------------------------------
# More options
# ---------------------------------------------------------------------------


async def test_more_options_with_every_flight_shown_keeps_the_choices(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await _at_flights(app, client, headers)
    offer = _choices(await answer(client, headers, plan_id, "flight_preferences", ECONOMY))

    events = await post(client, headers, plan_id, "continue", {"refresh": True})

    # The canned search finds the same two flights, both already offered.
    assert [kind for kind, _ in stored(events)] == ["message", "nudge", "choices"]
    assert _choices(events).offer_id == offer.offer_id
    assert len(fake_flights.searches) == 2
    assert fake_flights.searches[1].query == fake_flights.searches[0].query
