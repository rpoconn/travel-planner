"""Fixtures for the plan-change tests: a user with a plan, and a DB session and
`ToolContext` for calling `chat.tools` directly."""

from collections.abc import AsyncIterator
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from features.chat.tools import ToolContext
from tests.conftest import RegisterUser

# Changes are refused for dates before "today"; fixed so the dates below stay in the future.
TODAY = date(2026, 10, 1)

PLAN: dict[str, Any] = {
    "title": "Tokyo in May",
    "start_date": "2027-05-01",
    "end_date": "2027-05-08",
    "destination": {"name": "Tokyo", "country": "Japan"},
}

# Arguments of an `add_lodging` change (no `type`: the change's name implies it).
HOTEL_ARGS: dict[str, Any] = {
    "title": "Hotel Okura",
    "start": "2027-05-02T15:00:00",
    "end": "2027-05-06T11:00:00",
    "timezone": "Asia/Tokyo",
    "place": {"name": "Hotel Okura", "city": "Tokyo"},
}


# ---------------------------------------------------------------------------
# User / plan / context
# ---------------------------------------------------------------------------


@pytest.fixture
async def headers(register_user: RegisterUser) -> dict[str, str]:
    return await register_user("ada@example.com")


@pytest.fixture
async def plan(client: AsyncClient, headers: dict[str, str]) -> dict[str, Any]:
    response = await client.post("/plans", json=PLAN, headers=headers)
    assert response.status_code == 201, response.text
    created: dict[str, Any] = response.json()
    return created


@pytest.fixture
async def session(app: FastAPI, client: AsyncClient) -> AsyncIterator[AsyncSession]:
    # Depends on `client` so the app lifespan (which creates the tables) is running.
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        yield db


@pytest.fixture
def ctx(session: AsyncSession, plan: dict[str, Any]) -> ToolContext:
    return ToolContext(
        session=session,
        user_id=UUID(plan["user_id"]),
        plan_id=UUID(plan["id"]),
        today=TODAY,
    )
