"""Fixtures for chat tests: a scripted `FakeLLM` installed on the app, a user with a plan,
and a DB session for calling the agent / tools directly."""

import json
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from features.chat.schemas import ChatEvent
from features.chat.tools import ToolContext
from shared.llm.fake import FakeLLM
from tests.conftest import RegisterUser

PLAN: dict[str, Any] = {
    "title": "Tokyo in May",
    "start_date": "2027-05-01",
    "end_date": "2027-05-08",
    "destination": {"name": "Tokyo", "country": "Japan"},
}

# Arguments for the `add_lodging` tool (no `type`: the tool implies it).
HOTEL_ARGS: dict[str, Any] = {
    "title": "Hotel Okura",
    "start": "2027-05-02T15:00:00",
    "end": "2027-05-06T11:00:00",
    "timezone": "Asia/Tokyo",
    "place": {"name": "Hotel Okura", "city": "Tokyo"},
}

_EVENTS: TypeAdapter[ChatEvent] = TypeAdapter(ChatEvent)


# ---------------------------------------------------------------------------
# App / user / plan
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm(app: FastAPI) -> FakeLLM:
    """A strict FakeLLM (no fallback): every model call must be scripted with `queue`."""
    llm = FakeLLM()
    app.state.llm = llm
    return llm


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
    return ToolContext(session=session, user_id=UUID(plan["user_id"]), plan_id=UUID(plan["id"]))


# ---------------------------------------------------------------------------
# SSE
# ---------------------------------------------------------------------------


def parse_sse(body: str) -> list[ChatEvent]:
    """Decode a `text/event-stream` body into validated events (one `data:` line each)."""
    events: list[ChatEvent] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data = [
            line[len("data:") :].strip() for line in block.split("\n") if line.startswith("data:")
        ]
        if data:
            events.append(_EVENTS.validate_python(json.loads("\n".join(data))))
    return events


async def send(
    client: AsyncClient, headers: dict[str, str], plan_id: str, content: str
) -> list[ChatEvent]:
    response = await client.post(
        f"/plans/{plan_id}/chat", json={"content": content}, headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse_sse(response.text)
