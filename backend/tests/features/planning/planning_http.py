"""HTTP helpers for the planning route tests: create a plan, post to the planning routes,
and read the streamed events back."""

import json
from collections.abc import Mapping
from uuid import UUID

from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from features.planning.events import (
    PlanningDoneEvent,
    PlanningEvent,
    PlanningMessage,
    PlanningMessageEvent,
)
from features.planning.schemas import AskInput, OfferChoice
from features.plans import service as plans_service
from features.plans.models import Plan
from features.plans.schemas import PlanStage

EVENTS: TypeAdapter[PlanningEvent] = TypeAdapter(PlanningEvent)
MESSAGES: TypeAdapter[list[PlanningMessage]] = TypeAdapter(list[PlanningMessage])

LISBON: dict[str, JsonValue] = {"name": "Lisbon", "city": "Lisbon", "country": "Portugal"}
MAY: dict[str, JsonValue] = {"start": "2027-05-01", "end": "2027-05-08"}

# A plan with every basic but the budget, as the form would leave it.
BASICS: dict[str, JsonValue] = {
    "destination": LISBON,
    "start_date": "2027-05-01",
    "end_date": "2027-05-08",
    "travelers": {"adults": 2},
    "origin": {"name": "Philadelphia"},
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def create_plan(client: AsyncClient, headers: dict[str, str], **fields: JsonValue) -> str:
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
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    path: str,
    body: Mapping[str, JsonValue] | None = None,
) -> list[PlanningEvent]:
    response = await client.post(f"/plans/{plan_id}/planning/{path}", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return parse(response.text)


async def answer(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    slot: str,
    value: Mapping[str, JsonValue],
) -> list[PlanningEvent]:
    body: dict[str, JsonValue] = {"type": "answer", "slot": slot, "value": dict(value)}
    return await post(client, headers, plan_id, "inputs", body)


async def say(
    client: AsyncClient, headers: dict[str, str], plan_id: str, content: str
) -> list[PlanningEvent]:
    return await post(client, headers, plan_id, "chat", {"content": content})


async def messages(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> list[PlanningMessage]:
    response = await client.get(f"/plans/{plan_id}/planning/messages", headers=headers)
    assert response.status_code == 200, response.text
    return MESSAGES.validate_json(response.content)


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
    """The slot whose form the plan waits on once the turn is done."""
    pending = done(events).plan.pending
    return pending.slot if isinstance(pending, AskInput) else None


def choices(events: list[PlanningEvent]) -> OfferChoice:
    """The choices the plan waits on once the turn is done."""
    pending = done(events).plan.pending
    assert isinstance(pending, OfferChoice), pending
    return pending


async def pick(
    client: AsyncClient, headers: dict[str, str], plan_id: str, offer: OfferChoice, *ids: str
) -> list[PlanningEvent]:
    body: dict[str, JsonValue] = {
        "type": "pick",
        "offer_id": str(offer.offer_id),
        "candidate_ids": list(ids),
    }
    return await post(client, headers, plan_id, "inputs", body)


async def status_of(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    path: str,
    body: Mapping[str, JsonValue] | None = None,
) -> tuple[int, str]:
    """A planning POST that is turned down before its stream opens: (status, detail)."""
    response = await client.post(f"/plans/{plan_id}/planning/{path}", json=body, headers=headers)
    return response.status_code, response.json()["detail"]


async def add_item(
    client: AsyncClient, headers: dict[str, str], plan_id: str, **item: JsonValue
) -> str:
    """Add an item through the items API (as the item sheet does); returns its id."""
    response = await client.post(f"/plans/{plan_id}/items", json=item, headers=headers)
    assert response.status_code == 201, response.text
    item_id: str = response.json()["id"]
    return item_id


LISBON_TZ = "Europe/Lisbon"
FLAT: dict[str, JsonValue] = {"name": "Friend's flat", "city": "Lisbon"}


async def move_to(
    app: FastAPI, plan_id: str, stage: PlanStage, *, flights_skipped: bool = False
) -> None:
    """Put the plan at `stage` directly, past the readiness checks."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        row = await db.get(Plan, UUID(plan_id))
        assert row is not None
        await plans_service.set_stage(db, row.user_id, row.id, stage, flights_skipped)


async def reach(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], plan_id: str, stage: PlanStage
) -> None:
    """Put a `BASICS` plan at `stage` the way a trip without flights gets there: flights
    skipped past the flights stage, every night (May 1-8) at a friend's flat past lodging,
    and a dinner there past activities. The engine goes back to any earlier stage whose
    checklist isn't empty, so a plan can't just be set to a later stage."""
    stages = list(PlanStage)
    index = stages.index(stage)
    if index > stages.index(PlanStage.LODGING):
        await add_item(
            client,
            headers,
            plan_id,
            type="lodging",
            title="Friend's flat",
            start="2027-05-01T15:00",
            end="2027-05-08T11:00",
            timezone=LISBON_TZ,
            place=FLAT,
        )
    if index > stages.index(PlanStage.ACTIVITIES):
        await add_item(
            client,
            headers,
            plan_id,
            type="activity",
            title="Dinner in",
            start="2027-05-02T19:30",
            end="2027-05-02T21:00",
            timezone=LISBON_TZ,
            place=FLAT,
            category="food",
        )
    skipped = index > stages.index(PlanStage.FLIGHTS)
    await move_to(app, plan_id, stage, flights_skipped=skipped)
