"""An item's Remove button, over HTTP (`POST /plans/{id}/planning/removals`): the item goes
with the rides to and from it, through the same rules as a typed "remove …", with no model
call; the changes card says what the trip now needs and how to fix it, and the engine asks
for it, going back to the stage that does.

The plan is the Lisbon trip without flights (`planning_http.reach`): its items, in time
order, are "Friend's flat" (May 1-8) and "Dinner in" (May 2).
"""

from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue

from features.planning.schemas import ChangesRecord, TravelPlan
from features.plans.schemas import PlanStage
from shared.llm.fake import FakeLLM
from tests.features.planning.planning_http import (
    BASICS,
    FLAT,
    LISBON_TZ,
    add_item,
    asking,
    create_plan,
    done,
    messages,
    move_to,
    post,
    reach,
    stored,
)

LISBON_AIRPORT: dict[str, JsonValue] = {"name": "Lisbon Airport", "iata": "LIS"}
PHILADELPHIA_AIRPORT: dict[str, JsonValue] = {"name": "Philadelphia Airport", "iata": "PHL"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _plan_at(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], stage: PlanStage
) -> str:
    plan_id = await create_plan(client, headers, **BASICS)
    await reach(app, client, headers, plan_id, stage)
    return plan_id


async def _item_id(client: AsyncClient, headers: dict[str, str], plan_id: str, title: str) -> str:
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    plan = TravelPlan.model_validate_json(response.content)
    return next(str(item.id) for item in plan.items if item.title == title)


async def _titles(client: AsyncClient, headers: dict[str, str], plan_id: str) -> list[str]:
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    return [item.title for item in TravelPlan.model_validate_json(response.content).items]


async def _add_flight(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    title: str,
    direction: str,
    times: tuple[str, str],
    places: tuple[dict[str, JsonValue], dict[str, JsonValue]],
    zones: tuple[str, str],
) -> None:
    await add_item(
        client,
        headers,
        plan_id,
        type="flight",
        title=title,
        start=times[0],
        end=times[1],
        timezone=zones[0],
        end_timezone=zones[1],
        carrier="TAP",
        from_place=places[0],
        to_place=places[1],
        direction=direction,
    )


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------


async def test_removing_the_only_stay_says_what_it_needs_and_goes_back_to_lodging(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)
    flat = await _item_id(client, headers, plan_id, "Friend's flat")

    events = await post(client, headers, plan_id, "removals", {"item_id": flat})

    assert stored(events) == [
        ("message", "Remove “Friend's flat”."),
        (
            "changes",
            "Removed “Friend's flat”. The trip needs a place to stay May 1 to May 8 now. "
            "Pick another place to stay, or tell me what you'd like instead.",
        ),
        ("stage", "Back to lodging: the trip still needs a place to stay the night of May 1."),
        ("expected_input", "What kind of place do you want to stay in?"),
    ]
    assert done(events).plan.stage == PlanStage.LODGING
    assert asking(events) == "lodging_preferences"
    assert await _titles(client, headers, plan_id) == ["Dinner in"]
    changes = (await messages(client, headers, plan_id))[1]
    assert isinstance(changes.payload, ChangesRecord)
    assert [change.name for change in changes.payload.applied] == ["remove_item"]
    # The button is the command itself: nothing for the model to read.
    assert fake_llm.requests == []


async def test_removing_a_flight_asks_for_another_or_to_skip_flights(
    app: FastAPI, client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)
    await _add_flight(
        client,
        headers,
        plan_id,
        "PHL → LIS",
        "outbound",
        ("2027-04-30T18:00", "2027-05-01T07:00"),
        (PHILADELPHIA_AIRPORT, LISBON_AIRPORT),
        ("America/New_York", LISBON_TZ),
    )
    await _add_flight(
        client,
        headers,
        plan_id,
        "LIS → PHL",
        "return",
        ("2027-05-08T13:00", "2027-05-08T16:00"),
        (LISBON_AIRPORT, PHILADELPHIA_AIRPORT),
        (LISBON_TZ, "America/New_York"),
    )
    await move_to(app, plan_id, PlanStage.GROUND, flights_skipped=False)
    flight_home = await _item_id(client, headers, plan_id, "LIS → PHL")

    events = await post(client, headers, plan_id, "removals", {"item_id": flight_home})

    # The rides around the flight aren't named: a new flight moves them anyway.
    assert stored(events)[:3] == [
        ("message", "Remove “LIS → PHL”."),
        (
            "changes",
            "Removed “LIS → PHL”. The trip needs a flight home now. Pick another flight, "
            "tell me what you'd like instead, or skip flights if you'll get there another "
            "way.",
        ),
        ("stage", "Back to flights: the trip still needs a flight home."),
    ]
    # The flight preferences come first, as for any flight search.
    assert done(events).plan.stage == PlanStage.FLIGHTS
    assert asking(events) == "flight_preferences"


async def test_removing_an_item_takes_its_rides_and_names_the_one_now_missing(
    app: FastAPI, client: AsyncClient, headers: dict[str, str]
) -> None:
    # Flat → taxi → tram tour → taxi → flat, then lunch on from the flat: the tour goes with
    # both taxis, which is back to how it was before it; nothing new is missing.
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)
    tram: dict[str, JsonValue] = {"name": "Tram 28 stop", "city": "Lisbon"}
    await add_item(
        client,
        headers,
        plan_id,
        type="transfer",
        title="Taxi there",
        start="2027-05-03T09:30",
        end="2027-05-03T10:00",
        timezone=LISBON_TZ,
        mode="taxi",
        from_place=FLAT,
        to_place=tram,
    )
    await add_item(
        client,
        headers,
        plan_id,
        type="activity",
        title="Tram tour",
        start="2027-05-03T10:00",
        end="2027-05-03T12:00",
        timezone=LISBON_TZ,
        place=tram,
        category="tour",
    )
    await add_item(
        client,
        headers,
        plan_id,
        type="transfer",
        title="Taxi back",
        start="2027-05-03T12:00",
        end="2027-05-03T12:30",
        timezone=LISBON_TZ,
        mode="taxi",
        from_place=tram,
        to_place=FLAT,
    )
    tour = await _item_id(client, headers, plan_id, "Tram tour")

    events = await post(client, headers, plan_id, "removals", {"item_id": tour})

    assert stored(events)[:2] == [
        ("message", "Remove “Tram tour”."),
        ("changes", "Removed “Tram tour” and “Taxi there”, “Taxi back”."),
    ]
    assert await _titles(client, headers, plan_id) == ["Friend's flat", "Dinner in"]


async def test_an_item_not_on_the_plan_is_not_found(
    app: FastAPI, client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await _plan_at(app, client, headers, PlanStage.GROUND)

    response = await client.post(
        f"/plans/{plan_id}/planning/removals", json={"item_id": str(uuid4())}, headers=headers
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "That item is no longer in this plan"
    # Nothing was stored.
    assert await messages(client, headers, plan_id) == []
