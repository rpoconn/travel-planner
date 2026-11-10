from collections.abc import AsyncIterator, Awaitable, Callable

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from features.chat.dependencies import create_llm
from main import create_app
from shared.config import Settings, get_settings
from shared.llm.types import LLMClient

# Registers a user and returns ready-to-use `Authorization` headers.
RegisterUser = Callable[[str], Awaitable[dict[str, str]]]

TEST_PASSWORD = "correct horse battery"


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///:memory:",
        jwt_secret="test-secret-at-least-32-bytes-long!",
        bcrypt_rounds=4,
        llm_provider="fake",
        # Searches are metered; never let a developer's `.env` key reach SerpApi from tests.
        flight_provider="fake",
        hotel_provider="fake",
        places_provider="fake",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    # httpx's ASGITransport doesn't send lifespan events, so run startup/shutdown ourselves.
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac,
    ):
        yield ac


@pytest.fixture
def register_user(client: AsyncClient) -> RegisterUser:
    async def _register(email: str) -> dict[str, str]:
        response = await client.post(
            "/auth/register", json={"email": email, "password": TEST_PASSWORD}
        )
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _register


@pytest.fixture
async def session(app: FastAPI, client: AsyncClient) -> AsyncIterator[AsyncSession]:
    """A database session for calling services directly. Depends on `client` so the app
    lifespan (which migrates the database) is running."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        yield db


@pytest.fixture
async def llm() -> AsyncIterator[LLMClient]:
    """The real model `.env` selects, for the `live_llm` tests; skips when there is none to
    reach."""
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
