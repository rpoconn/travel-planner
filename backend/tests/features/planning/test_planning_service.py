"""Offers in the database, and the problems and pending interaction every plan response
carries."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import service as chat_service
from features.chat.schemas import MessageKind
from features.planning import offers, service
from features.planning.schemas import Candidate, CandidateChange, OfferChoice, TravelPlan
from features.plans.schemas import SlotName
from shared.llm.fake import FakeLLM
from tests.conftest import RegisterUser
from tests.features.planning.planning_http import BASICS, LISBON, create_plan, messages, post
from tests.features.planning.scripts import ask

TRIP: dict[str, JsonValue] = {
    "title": "Tokyo in May",
    "origin": {"name": "San Francisco", "city": "San Francisco"},
    "destination": {"name": "Tokyo", "country": "Japan"},
    "start_date": "2027-05-01",
    "end_date": "2027-05-04",
    "travelers": {"adults": 2},
    "budget": {"amount": 4000},
}

CANDIDATES = [
    Candidate(
        id=f"c{index}",
        title=f"Flight {index}",
        changes=[CandidateChange(name="add_flight", arguments={}, summary=f"Add flight {index}")],
    )
    for index in (1, 2)
]


@pytest.fixture
async def headers(register_user: RegisterUser) -> dict[str, str]:
    return await register_user("ada@example.com")


@pytest.fixture
async def plan(client: AsyncClient, headers: dict[str, str]) -> TravelPlan:
    response = await client.post("/plans", json=TRIP, headers=headers)
    assert response.status_code == 201, response.text
    return TravelPlan.model_validate_json(response.content)


async def _open(session: AsyncSession, plan: TravelPlan, version: int) -> UUID:
    offer = await offers.open_offer(
        session,
        plan_id=plan.id,
        slot=SlotName.FLIGHT_OUTBOUND,
        plan_version=version,
        candidates=CANDIDATES,
        query_hash="q",
    )
    return offer.id


# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------


async def test_an_offer_round_trips(session: AsyncSession, plan: TravelPlan) -> None:
    offer_id = await _open(session, plan, 1)

    current = await offers.current_offer(session, plan.id)

    assert current is not None
    assert current.id == offer_id
    assert current.candidates == CANDIDATES
    assert current.status == "open"


async def test_opening_an_offer_dismisses_the_open_one(
    session: AsyncSession, plan: TravelPlan
) -> None:
    first = await _open(session, plan, 1)
    second = await _open(session, plan, 1)

    current = await offers.current_offer(session, plan.id)

    assert current is not None and current.id == second
    with pytest.raises(offers.OfferClosedError, match="dismissed"):
        await offers.close_offer(session, plan.id, first, "picked")


async def test_a_picked_offer_is_no_longer_current(session: AsyncSession, plan: TravelPlan) -> None:
    offer_id = await _open(session, plan, 1)

    picked = await offers.close_offer(session, plan.id, offer_id, "picked")

    assert picked.status == "picked" and picked.decided_at is not None
    assert await offers.current_offer(session, plan.id) is None


async def test_another_plans_offer_is_not_found(session: AsyncSession, plan: TravelPlan) -> None:
    offer_id = await _open(session, plan, 1)

    with pytest.raises(offers.OfferNotFoundError):
        await offers.close_offer(session, uuid4(), offer_id, "picked")


# ---------------------------------------------------------------------------
# Snapshot and API
# ---------------------------------------------------------------------------


async def test_a_new_plan_asks_where_to_go(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await client.post("/plans", json={}, headers=headers)

    pending = response.json()["pending"]
    assert (pending["type"], pending["slot"]) == ("ask_input", "destination")
    assert response.json()["preferences"] == {
        "flights": None,
        "lodging": None,
        "activities": None,
        "declined": [],
    }


async def test_every_plan_response_lists_its_problems(
    client: AsyncClient, headers: dict[str, str], plan: TravelPlan
) -> None:
    """The basics are in: everything else the trip needs is listed at once."""
    response = await client.get(f"/plans/{plan.id}", headers=headers)

    body = response.json()
    assert [problem["id"] for problem in body["problems"]] == [
        "missing_outbound",
        "missing_return",
        "uncovered_night:2027-05-01",
        "no_activities",
    ]
    assert body["problems"][2]["nights"] == {"check_in": "2027-05-01", "check_out": "2027-05-04"}
    assert body["problems"][2]["fix"] == {"how": "search", "slot": "lodging_stay"}
    assert body["complete"] is False


async def test_a_new_plan_opens_on_the_first_thing_only_the_user_can_give(
    client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    response = await client.post("/plans", json={"destination": LISBON}, headers=headers)

    pending = response.json()["pending"]
    assert (pending["type"], pending["slot"]) == ("ask_input", "dates")
    assert pending["prompt"] == "When are you going?"
    # Nothing was stored and the model wasn't asked: the form is worked out on each read.
    assert await messages(client, headers, response.json()["id"]) == []
    assert fake_llm.requests == []


async def test_a_new_plan_with_its_basics_waits_on_nothing(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)

    # What it needs next (flights, a stay) is the planner's to look for.
    assert (await client.get(f"/plans/{plan_id}", headers=headers)).json()["pending"] is None


async def test_the_plan_waits_on_the_form_its_transcript_ends_on(
    client: AsyncClient, headers: dict[str, str], session: AsyncSession, fake_llm: FakeLLM
) -> None:
    # A new plan's first turn asks where to go.
    plan_id = await create_plan(client, headers)
    fake_llm.queue(ask("Where to?", "destination"))
    await post(client, headers, plan_id, "continue")

    response = await client.get(f"/plans/{plan_id}", headers=headers)
    body = response.json()
    assert body["pending"]["type"] == "ask_input"
    assert body["pending"]["slot"] == "destination"

    # A failure notice after the form doesn't answer it; anything else does.
    await chat_service.add_message(session, UUID(plan_id), "system", "!", kind=MessageKind.ERROR)
    assert (await client.get(f"/plans/{plan_id}", headers=headers)).json()["pending"] == body[
        "pending"
    ]
    await chat_service.add_message(session, UUID(plan_id), "user", "Hm.")
    assert (await client.get(f"/plans/{plan_id}", headers=headers)).json()["pending"] is None


async def test_the_plan_waits_on_its_open_offer(session: AsyncSession, plan: TravelPlan) -> None:
    offer_id = await _open(session, plan, plan.version)

    snapshot = await service.get_plan_snapshot(session, plan.user_id, plan.id)

    assert isinstance(snapshot.pending, OfferChoice)
    assert snapshot.pending.offer_id == offer_id

    # Made for an earlier version of the plan: no longer waited on.
    await _open(session, plan, plan.version - 1)
    stale = await service.get_plan_snapshot(session, plan.user_id, plan.id)
    assert stale.pending is None
