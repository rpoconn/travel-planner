"""Offers in the database, and the assessment every plan response carries."""

from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from features.planning import offers, service
from features.planning.schemas import Candidate, CandidateChange, OfferChoice, TravelPlan
from features.plans import service as plans_service
from features.plans.schemas import PlanStage, SlotName
from tests.conftest import RegisterUser

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
        stage=PlanStage.FLIGHTS,
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

    assessment = response.json()["assessment"]
    assert assessment["stage"] == "basics"
    assert assessment["next_action"]["type"] == "ask_input"
    assert assessment["next_action"]["slot"] == "destination"
    assert assessment["can_advance"] is False
    assert response.json()["preferences"] == {
        "flights": None,
        "lodging": None,
        "activities": None,
        "declined": [],
    }


async def test_finished_basics_advance(
    client: AsyncClient, headers: dict[str, str], plan: TravelPlan
) -> None:
    response = await client.get(f"/plans/{plan.id}", headers=headers)

    assessment = response.json()["assessment"]
    assert assessment["next_action"] == {"type": "advance_stage", "to": "flights"}
    assert assessment["can_advance"] is True
    assert assessment["issues"] == []


async def test_stage_checklists_are_typed(
    client: AsyncClient, headers: dict[str, str], plan: TravelPlan
) -> None:
    response = await client.get(f"/plans/{plan.id}", headers=headers)

    flights = next(s for s in response.json()["stages"] if s["stage"] == "flights")
    assert flights["ready"] is False
    assert flights["missing"] == ["a flight out", "a flight home"]
    assert [issue["code"] for issue in flights["issues"]] == ["missing_outbound", "missing_return"]


async def test_the_snapshot_shows_a_current_offer(session: AsyncSession, plan: TravelPlan) -> None:
    plan_id, user_id = plan.id, plan.user_id
    row = await plans_service.set_stage(session, user_id, plan_id, PlanStage.FLIGHTS, False)
    row.preferences = {"flights": {"cabin": "economy"}}
    await session.commit()
    offer_id = await _open(session, plan, row.version)

    snapshot = await service.get_plan_snapshot(session, user_id, plan_id)

    action = snapshot.assessment.next_action
    assert isinstance(action, OfferChoice)
    assert action.offer_id == offer_id
    assert [c.id for c in action.candidates] == ["c1", "c2"]


async def test_a_stale_offer_is_ignored(session: AsyncSession, plan: TravelPlan) -> None:
    plan_id, user_id = plan.id, plan.user_id
    row = await plans_service.set_stage(session, user_id, plan_id, PlanStage.FLIGHTS, False)
    row.preferences = {"flights": {}}
    await session.commit()
    await _open(session, plan, row.version - 1)

    snapshot = await service.get_plan_snapshot(session, user_id, plan_id)

    assert snapshot.assessment.next_action.type == "run_search"
