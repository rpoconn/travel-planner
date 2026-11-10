"""An item's Remove button, over HTTP (`POST /plans/{id}/planning/removals`): the item goes
at once, with the rides to and from it, through the same rules as a typed "remove …"; the
changes card says what the trip now needs, and then the planner decides what is next. The
model is a strict `FakeLLM` scripted with tool calls.

The plan is the Lisbon trip without flights (`planning_http.reach`): its items, in time
order, are "Friend's flat" (May 1-8) and "Dinner in" (May 2).
"""

from uuid import uuid4

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue

from features.planning.schemas import ChangesRecord, TravelPlan
from shared.llm.fake import FakeLLM, text
from tests.features.planning.agent_helpers import brief_of
from tests.features.planning.planning_http import (
    BASICS,
    FLAT,
    LISBON_TZ,
    add_item,
    asking,
    create_plan,
    done,
    messages,
    post,
    reach,
    set_no_flights,
    stored,
)
from tests.features.planning.scripts import ask

LISBON_AIRPORT: dict[str, JsonValue] = {"name": "Lisbon Airport", "iata": "LIS"}
PHILADELPHIA_AIRPORT: dict[str, JsonValue] = {"name": "Philadelphia Airport", "iata": "PHL"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _settled_plan(app: FastAPI, client: AsyncClient, headers: dict[str, str]) -> str:
    plan_id = await create_plan(client, headers, **BASICS)
    await reach(app, client, headers, plan_id)
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


async def test_removing_the_only_stay_says_what_it_needs_then_the_planner_asks(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _settled_plan(app, client, headers)
    flat = await _item_id(client, headers, plan_id, "Friend's flat")
    fake_llm.queue(ask("What kind of place do you want to stay in?", "lodging_preferences"))

    events = await post(client, headers, plan_id, "removals", {"item_id": flat})

    assert stored(events) == [
        ("message", "Remove “Friend's flat”."),
        (
            "changes",
            "Removed “Friend's flat”. The trip needs a place to stay May 1 to May 8 now. "
            "Pick another place to stay, or tell me what you'd like instead.",
        ),
        ("expected_input", "What kind of place do you want to stay in?"),
    ]
    assert asking(events) == "lodging_preferences"
    assert await _titles(client, headers, plan_id) == ["Dinner in"]
    changes = (await messages(client, headers, plan_id))[1]
    assert isinstance(changes.payload, ChangesRecord)
    assert [change.name for change in changes.payload.applied] == ["remove_item"]
    # The button is the command itself: the planner is only asked once it is written.
    [request] = fake_llm.requests
    brief = brief_of(request)
    assert brief.said is None
    assert [item.title for item in brief.plan.items] == ["Dinner in"]
    assert brief.plan.problems[0].id == "uncovered_night:2027-05-01"


async def test_removing_a_flight_says_the_trip_needs_another(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _settled_plan(app, client, headers)
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
    await set_no_flights(app, plan_id, False)
    flight_home = await _item_id(client, headers, plan_id, "LIS → PHL")
    fake_llm.queue(text("Shall I look for another flight home?"))

    events = await post(client, headers, plan_id, "removals", {"item_id": flight_home})

    # The rides around the flight aren't named: a new flight moves them anyway.
    assert stored(events) == [
        ("message", "Remove “LIS → PHL”."),
        (
            "changes",
            "Removed “LIS → PHL”. The trip needs a flight home now. Pick another flight, "
            "tell me what you'd like instead, or say so if you'll get there another "
            "way.",
        ),
        ("message", "Shall I look for another flight home?"),
    ]
    assert "missing_return" in [problem.id for problem in done(events).plan.problems]


async def test_removing_an_item_takes_its_rides_and_names_the_one_now_missing(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    # Flat → taxi → tram tour → taxi → flat, then lunch on from the flat: the tour goes with
    # both taxis, which is back to how it was before it; nothing new is missing.
    plan_id = await _settled_plan(app, client, headers)
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
    fake_llm.queue(text("The tour is off."))

    events = await post(client, headers, plan_id, "removals", {"item_id": tour})

    assert stored(events)[:2] == [
        ("message", "Remove “Tram tour”."),
        ("changes", "Removed “Tram tour” and “Taxi there”, “Taxi back”."),
    ]
    assert await _titles(client, headers, plan_id) == ["Friend's flat", "Dinner in"]


async def test_an_item_not_on_the_plan_is_not_found(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _settled_plan(app, client, headers)

    response = await client.post(
        f"/plans/{plan_id}/planning/removals", json={"item_id": str(uuid4())}, headers=headers
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "That item is no longer in this plan"
    # Nothing was stored, and the planner wasn't asked.
    assert await messages(client, headers, plan_id) == []
    assert fake_llm.requests == []
