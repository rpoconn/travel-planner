"""Fixtures for the planning route tests: a strict model, a scripted flight search, a user
and a new plan."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from shared.flights.fake import FakeFlightProvider
from shared.llm.fake import FakeLLM
from tests.conftest import RegisterUser
from tests.features.planning.planning_http import create_plan


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
async def headers(register_user: RegisterUser) -> dict[str, str]:
    return await register_user("ada@example.com")


@pytest.fixture
async def plan_id(client: AsyncClient, headers: dict[str, str]) -> str:
    """A new, empty plan: it asks for the destination."""
    return await create_plan(client, headers)
