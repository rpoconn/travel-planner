from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from main import create_app
from shared.config import Settings

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
