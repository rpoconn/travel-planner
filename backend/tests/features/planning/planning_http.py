"""HTTP helpers for the planning route tests: create a plan, post to the planning routes,
and read the streamed events back."""

import json
from typing import Any
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from features.planning.events import PlanningDoneEvent, PlanningEvent, PlanningMessageEvent
from features.plans import service as plans_service
from features.plans.models import Plan
from features.plans.schemas import PlanStage

EVENTS: TypeAdapter[PlanningEvent] = TypeAdapter(PlanningEvent)

LISBON = {"name": "Lisbon", "city": "Lisbon", "country": "Portugal"}
MAY = {"start": "2027-05-01", "end": "2027-05-08"}

# A plan with every basic but the budget, as the form would leave it.
BASICS: dict[str, Any] = {
    "destination": LISBON,
    "start_date": "2027-05-01",
    "end_date": "2027-05-08",
    "travelers": {"adults": 2},
    "origin": {"name": "Philadelphia"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def create_plan(client: AsyncClient, headers: dict[str, str], **fields: Any) -> str:
    response = await client.post("/plans", json=fields, headers=headers)
    assert response.status_code == 201, response.text
    plan_id: str = response.json()["id"]
    return plan_id


def parse(body: str) -> list[PlanningEvent]:
    events: list[PlanningEvent] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data = [
            line[len("data:") :].strip() for line in block.split("\n") if line.startswith("data:")
        ]
        if data:
            events.append(EVENTS.validate_python(json.loads("\n".join(data))))
    return events


async def post(
    client: AsyncClient, headers: dict[str, str], plan_id: str, path: str, body: Any = None
) -> list[PlanningEvent]:
    response = await client.post(f"/plans/{plan_id}/planning/{path}", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse(response.text)


async def answer(
    client: AsyncClient, headers: dict[str, str], plan_id: str, slot: str, value: dict[str, Any]
) -> list[PlanningEvent]:
    body = {"type": "answer", "slot": slot, "value": value}
    return await post(client, headers, plan_id, "inputs", body)


async def say(
    client: AsyncClient, headers: dict[str, str], plan_id: str, content: str
) -> list[PlanningEvent]:
    return await post(client, headers, plan_id, "chat", {"content": content})


async def messages(client: AsyncClient, headers: dict[str, str], plan_id: str) -> list[Any]:
    response = await client.get(f"/plans/{plan_id}/planning/messages", headers=headers)
    assert response.status_code == 200, response.text
    messages: list[Any] = response.json()
    return messages


def stored(events: list[PlanningEvent]) -> list[tuple[str, str]]:
    """(kind, content) of each message the turn stored, in order."""
    return [
        (e.message.kind, e.message.content) for e in events if isinstance(e, PlanningMessageEvent)
    ]


def done(events: list[PlanningEvent]) -> PlanningDoneEvent:
    done = events[-1]
    assert isinstance(done, PlanningDoneEvent), events
    return done


def asking(events: list[PlanningEvent]) -> str | None:
    """The slot the plan asks for once the turn is done."""
    action = done(events).plan.assessment.next_action
    return action.slot if action.type == "ask_input" else None


async def move_to(app: FastAPI, plan_id: str, stage: PlanStage) -> None:
    """Put the plan at `stage` directly, past the readiness checks."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        row = await db.get(Plan, UUID(plan_id))
        assert row is not None
        await plans_service.set_stage(db, row.user_id, row.id, stage, flights_skipped=False)
