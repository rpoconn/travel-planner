from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from main import create_app
from shared.config import Settings


@pytest.fixture
def settings() -> Settings:
    return Settings(environment="test", database_url="sqlite+aiosqlite:///:memory:")


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[AsyncClient]:
    app = create_app(settings)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
