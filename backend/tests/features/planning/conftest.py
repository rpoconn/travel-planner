"""Fixtures for the planning route tests: a strict model, scripted flight, hotel and place
searches, a user and a new plan."""

from datetime import date

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from features.planning.agent.tools import TurnContext
from features.planning.schemas import TravelPlan
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.llm.fake import FakeLLM
from shared.places.fake import FakePlaceProvider
from tests.conftest import RegisterUser
from tests.features.planning.planning_http import BASICS, create_plan

# Fixed so the trip (May 2027) stays in the future.
AGENT_TODAY = date(2026, 11, 1)


@pytest.fixture
def fake_llm(app: FastAPI) -> FakeLLM:
    """A model with no fallback: a test that reaches it unscripted fails."""
    llm = FakeLLM()
    app.state.llm = llm
    return llm


@pytest.fixture
def fake_flights(app: FastAPI) -> FakeFlightProvider:
    """The flight search, recording each search; canned flights unless replies are queued."""
    flights = FakeFlightProvider()
    app.state.flights = flights
    return flights


@pytest.fixture
def fake_hotels(app: FastAPI) -> FakeHotelProvider:
    """The hotel search, recording each query; canned hotels unless replies are queued."""
    hotels = FakeHotelProvider()
    app.state.hotels = hotels
    return hotels


@pytest.fixture
def fake_places(app: FastAPI) -> FakePlaceProvider:
    """The place search, recording each query; canned places unless replies are queued."""
    places = FakePlaceProvider()
    app.state.places = places
    return places


@pytest.fixture
async def headers(register_user: RegisterUser) -> dict[str, str]:
    return await register_user("ada@example.com")


@pytest.fixture
async def plan_id(client: AsyncClient, headers: dict[str, str]) -> str:
    """A new, empty plan: it asks for the destination."""
    return await create_plan(client, headers)


@pytest.fixture
async def turn(
    client: AsyncClient,
    headers: dict[str, str],
    session: AsyncSession,
    fake_llm: FakeLLM,
    fake_flights: FakeFlightProvider,
    fake_hotels: FakeHotelProvider,
    fake_places: FakePlaceProvider,
) -> TurnContext:
    """A planning turn's context on a plan with every basic (`planning_http.BASICS`) and
    nothing else, for the planner's loop and its tools."""
    plan_id = await create_plan(client, headers, **BASICS)
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    plan = TravelPlan.model_validate_json(response.content)
    return TurnContext(
        session=session,
        llm=fake_llm,
        user_id=plan.user_id,
        plan_id=plan.id,
        today=AGENT_TODAY,
        flights=fake_flights,
        hotels=fake_hotels,
        places=fake_places,
    )
