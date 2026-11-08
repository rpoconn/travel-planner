"""Fixtures for the planning route tests: a strict model, scripted flight, hotel and place
searches, a user and a new plan; and the real model `.env` selects, for the `live_llm`
evals."""

from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from features.chat.dependencies import create_llm
from shared.config import get_settings
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.llm.fake import FakeLLM
from shared.llm.types import LLMClient
from shared.places.fake import FakePlaceProvider
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
async def llm() -> AsyncIterator[LLMClient]:
    """The real model `.env` selects; skips when there is none to reach."""
    settings = get_settings()
    if settings.llm_provider == "fake":
        pytest.skip("LLM_PROVIDER=fake; the eval needs a real model")
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key is None:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    if settings.llm_provider == "ollama":
        try:
            async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=5) as http:
                response = await http.get("/api/tags")
        except httpx.HTTPError:
            pytest.skip(f"Ollama is not reachable at {settings.ollama_url}")
        names = {model["name"] for model in response.json().get("models", [])}
        if settings.ollama_model not in names:
            pytest.skip(f"Model {settings.ollama_model} is not pulled")
    client = create_llm(settings)
    yield client
    await client.aclose()
