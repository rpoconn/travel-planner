"""Fixtures for the plan-change tests: a user with a plan, and a DB session and
`ToolContext` for calling `chat.tools` directly."""

from datetime import date

import pytest
from httpx import AsyncClient
from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.tools import ToolContext
from features.planning.schemas import TravelPlan
from tests.conftest import RegisterUser

# Changes are refused for dates before "today"; fixed so the dates below stay in the future.
TODAY = date(2026, 10, 1)

PLAN: dict[str, JsonValue] = {
    "title": "Tokyo in May",
    "start_date": "2027-05-01",
    "end_date": "2027-05-08",
    "destination": {"name": "Tokyo", "country": "Japan"},
}

# Arguments of an `add_lodging` change (no `type`: the change's name implies it).
HOTEL_ARGS: dict[str, JsonValue] = {
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
async def plan(client: AsyncClient, headers: dict[str, str]) -> TravelPlan:
    response = await client.post("/plans", json=PLAN, headers=headers)
    assert response.status_code == 201, response.text
    return TravelPlan.model_validate(response.json())


@pytest.fixture
def ctx(session: AsyncSession, plan: TravelPlan) -> ToolContext:
    return ToolContext(session=session, user_id=plan.user_id, plan_id=plan.id, today=TODAY)
