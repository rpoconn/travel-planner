"""The planning routes over HTTP: forms, free text and continue, for the basics stage and the
move on to flights. The model is a strict `FakeLLM`: a test that reaches it unscripted fails."""

import json
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from features.planning.events import (
    PlanningDoneEvent,
    PlanningErrorEvent,
    PlanningEvent,
    PlanningMessageEvent,
    PlanningPlanEvent,
    PlanningStatusEvent,
    PlanningTokenEvent,
)
from features.planning.extractor import extraction_schema
from features.plans import service as plans_service
from features.plans.models import Plan
from features.plans.schemas import PlanStage, SlotName
from shared.llm.fake import FakeLLM, json_reply, text
from shared.llm.types import LLMError
from tests.conftest import RegisterUser

_EVENTS: TypeAdapter[PlanningEvent] = TypeAdapter(PlanningEvent)

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
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_llm(app: FastAPI) -> FakeLLM:
    llm = FakeLLM()
    app.state.llm = llm
    return llm


@pytest.fixture
async def headers(register_user: RegisterUser) -> dict[str, str]:
    return await register_user("ada@example.com")


async def _create(client: AsyncClient, headers: dict[str, str], **fields: Any) -> str:
    response = await client.post("/plans", json=fields, headers=headers)
    assert response.status_code == 201, response.text
    plan_id: str = response.json()["id"]
    return plan_id


@pytest.fixture
async def plan_id(client: AsyncClient, headers: dict[str, str]) -> str:
    """A new, empty plan: it asks for the destination."""
    return await _create(client, headers)


def _parse(body: str) -> list[PlanningEvent]:
    events: list[PlanningEvent] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        data = [
            line[len("data:") :].strip() for line in block.split("\n") if line.startswith("data:")
        ]
        if data:
            events.append(_EVENTS.validate_python(json.loads("\n".join(data))))
    return events


async def _post(
    client: AsyncClient, headers: dict[str, str], plan_id: str, path: str, body: Any = None
) -> list[PlanningEvent]:
    response = await client.post(f"/plans/{plan_id}/planning/{path}", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return _parse(response.text)


async def _answer(
    client: AsyncClient, headers: dict[str, str], plan_id: str, slot: str, value: dict[str, Any]
) -> list[PlanningEvent]:
    body = {"type": "answer", "slot": slot, "value": value}
    return await _post(client, headers, plan_id, "inputs", body)


async def _say(
    client: AsyncClient, headers: dict[str, str], plan_id: str, content: str
) -> list[PlanningEvent]:
    return await _post(client, headers, plan_id, "chat", {"content": content})


async def _messages(client: AsyncClient, headers: dict[str, str], plan_id: str) -> list[Any]:
    response = await client.get(f"/plans/{plan_id}/planning/messages", headers=headers)
    assert response.status_code == 200, response.text
    messages: list[Any] = response.json()
    return messages


def _stored(events: list[PlanningEvent]) -> list[tuple[str, str]]:
    """(kind, content) of each message the turn stored, in order."""
    return [
        (e.message.kind, e.message.content) for e in events if isinstance(e, PlanningMessageEvent)
    ]


def _done(events: list[PlanningEvent]) -> PlanningDoneEvent:
    done = events[-1]
    assert isinstance(done, PlanningDoneEvent), events
    return done


def _asking(events: list[PlanningEvent]) -> str | None:
    """The slot the plan asks for once the turn is done."""
    action = _done(events).plan.assessment.next_action
    return action.slot if action.type == "ask_input" else None


async def _move_to(app: FastAPI, plan_id: str, stage: PlanStage) -> None:
    """Put the plan at `stage` directly, past the readiness checks."""
    maker: async_sessionmaker[AsyncSession] = app.state.sessionmaker
    async with maker() as db:
        row = await db.get(Plan, UUID(plan_id))
        assert row is not None
        await plans_service.set_stage(db, row.user_id, row.id, stage, flights_skipped=False)


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


async def test_a_form_answer_is_stored_and_the_next_question_asked(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    events = await _answer(client, headers, plan_id, "destination", {"destination": LISBON})

    assert [type(e) for e in events] == [
        PlanningMessageEvent,
        PlanningPlanEvent,
        PlanningMessageEvent,
        PlanningDoneEvent,
    ]
    assert _stored(events) == [
        ("input", "Lisbon, Portugal"),
        ("expected_input", "When are you going?"),
    ]
    plan = _done(events).plan
    assert plan.destination is not None and plan.destination.name == "Lisbon"
    assert plan.title == "Trip to Lisbon"
    assert _asking(events) == "dates"


async def test_the_transcript_keeps_each_messages_payload(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    await _answer(client, headers, plan_id, "destination", {"destination": LISBON})

    answer, asked = await _messages(client, headers, plan_id)

    assert (answer["role"], answer["kind"]) == ("user", "input")
    assert answer["payload"] == {
        "type": "answer",
        "slot": "destination",
        "value": {
            "destination": {**LISBON, "iata": None, "address": None, "lat": None, "lng": None}
        },
        "skipped": False,
    }
    assert (asked["role"], asked["kind"]) == ("assistant", "expected_input")
    assert asked["payload"]["type"] == "ask_input"
    assert asked["payload"]["slot"] == "dates"
    assert asked["payload"]["fields"][0]["kind"] == "date_range"


async def test_finishing_basics_moves_on_to_flights(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    await _answer(client, headers, plan_id, "destination", {"destination": LISBON})
    await _answer(client, headers, plan_id, "dates", {"dates": MAY})
    await _answer(client, headers, plan_id, "travelers", {"adults": 2, "children": 1})
    await _answer(client, headers, plan_id, "origin", {"origin": {"name": "Philadelphia"}})

    events = await _post(client, headers, plan_id, "inputs", {"type": "skip", "slot": "budget"})

    assert _stored(events) == [
        ("input", "Skip"),
        ("stage", "Basics done. Next up: flights."),
        ("expected_input", "How do you like to fly?"),
    ]
    stage = next(
        e for e in events if isinstance(e, PlanningMessageEvent) and e.message.kind == "stage"
    )
    assert stage.message.role == "system"
    assert stage.message.payload is not None
    assert stage.message.payload.model_dump() == {
        "type": "stage",
        "from_stage": "basics",
        "to_stage": "flights",
    }
    plan = _done(events).plan
    assert plan.stage == "flights"
    assert plan.preferences.declined == ["budget"]
    assert _asking(events) == "flight_preferences"
    # The transcript is in the order the messages were stored.
    kinds = [m["kind"] for m in await _messages(client, headers, plan_id)]
    assert kinds[-3:] == ["input", "stage", "expected_input"]


async def test_preferences_are_stored_on_the_plan(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await _create(client, headers, **BASICS, budget={"amount": 5000})
    await _post(client, headers, plan_id, "continue")  # basics are done: on to flights

    events = await _answer(
        client, headers, plan_id, "flight_preferences", {"cabin": "business", "max_stops": 0}
    )

    plan = _done(events).plan
    assert plan.preferences.flights is not None
    assert plan.preferences.flights.cabin == "business"
    assert plan.preferences.flights.max_stops == 0
    assert _stored(events)[0] == ("input", "Business cabin, nonstop")


async def test_answering_a_question_not_being_asked_is_a_conflict(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    response = await client.post(
        f"/plans/{plan_id}/planning/inputs",
        json={"type": "answer", "slot": "dates", "value": {"dates": MAY}},
        headers=headers,
    )

    assert response.status_code == 409
    assert await _messages(client, headers, plan_id) == []


@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (
            {"type": "answer", "slot": "destination", "value": {"destination": {"name": ""}}},
            "destination.name: String should have at least 1 character",
        ),
        ({"type": "skip", "slot": "destination"}, "This question can't be skipped."),
    ],
)
async def test_an_answer_that_does_not_fit_is_rejected(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    body: dict[str, Any],
    detail: str,
) -> None:
    response = await client.post(f"/plans/{plan_id}/planning/inputs", json=body, headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert await _messages(client, headers, plan_id) == []


async def test_another_users_plan_is_not_found(
    client: AsyncClient, plan_id: str, register_user: RegisterUser
) -> None:
    other = await register_user("bob@example.com")

    response = await client.post(
        f"/plans/{plan_id}/planning/inputs",
        json={"type": "answer", "slot": "destination", "value": {"destination": LISBON}},
        headers=other,
    )
    listed = await client.get(f"/plans/{plan_id}/planning/messages", headers=other)

    assert response.status_code == 404
    assert listed.status_code == 404


# ---------------------------------------------------------------------------
# Free text
# ---------------------------------------------------------------------------


async def test_free_text_is_extracted_into_the_pending_slot(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(json_reply({"status": "answered", "value": {"destination": LISBON}}))

    events = await _say(client, headers, plan_id, "Thinking Lisbon this spring")

    assert _stored(events) == [
        ("message", "Thinking Lisbon this spring"),
        ("message", "Got it: Lisbon, Portugal."),
        ("expected_input", "When are you going?"),
    ]
    (request,) = fake_llm.requests
    assert request.format == extraction_schema(SlotName.DESTINATION)
    assert request.think is False
    assert _asking(events) == "dates"


async def test_unrelated_text_gets_the_slots_nudge_and_changes_nothing(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(json_reply({"status": "unrelated", "value": None}))

    events = await _say(client, headers, plan_id, "Write me a poem about cheese")

    assert _stored(events) == [
        ("message", "Write me a poem about cheese"),
        ("nudge", "Let's start with where you're going: a city, region or country."),
    ]
    assert _done(events).plan.version == 1
    assert _asking(events) == "destination"


async def test_a_question_is_answered_then_the_slot_asked_again(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    await _post(client, headers, plan_id, "continue")  # shows "Where are you going?"
    fake_llm.queue(
        json_reply({"status": "question", "value": None}),
        text("Lisbon is lovely in May."),
    )

    events = await _say(client, headers, plan_id, "Is Lisbon nice in May?")

    tokens = "".join(e.text for e in events if isinstance(e, PlanningTokenEvent))
    assert tokens == "Lisbon is lovely in May."
    assert _stored(events) == [
        ("message", "Is Lisbon nice in May?"),
        ("message", "Lisbon is lovely in May."),
        ("expected_input", "Where are you going?"),
    ]
    question = fake_llm.requests[1]
    assert question.streamed and 'waiting for an answer to: "Where are you going?"' in (
        question.messages[0].content
    )


async def test_an_extracted_answer_that_does_not_fit_is_explained(
    client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _create(client, headers, destination=LISBON)
    backwards = {"dates": {"start": "2027-05-08", "end": "2027-05-01"}}
    fake_llm.queue(json_reply({"status": "answered", "value": backwards}))

    events = await _say(client, headers, plan_id, "the 8th to the 1st")

    assert _stored(events)[1:] == [
        ("nudge", "That doesn't work: The trip has to end on or after the day it starts."),
        ("expected_input", "When are you going?"),
    ]
    assert _done(events).plan.start_date is None


async def test_with_nothing_to_answer_text_is_a_question_and_nothing_runs(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await _create(client, headers, **BASICS)
    await _move_to(app, plan_id, PlanStage.FLIGHTS)
    await _answer(client, headers, plan_id, "flight_preferences", {"cabin": "economy"})
    fake_llm.queue(text("Mornings are usually quieter."))

    events = await _say(client, headers, plan_id, "When do flights leave?")

    assert _stored(events) == [
        ("message", "When do flights leave?"),
        ("message", "Mornings are usually quieter."),
    ]
    assert not any(isinstance(e, PlanningStatusEvent) for e in events)
    assert _done(events).plan.assessment.next_action.type == "run_search"


async def test_a_model_failure_is_stored_as_an_error(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(LLMError("Could not reach Ollama"))

    events = await _say(client, headers, plan_id, "Lisbon")

    error = events[-1]
    assert isinstance(error, PlanningErrorEvent)
    assert error.detail == "Could not reach Ollama"
    assert error.message is not None and error.message.kind == "error"
    kinds = [(m["role"], m["kind"]) for m in await _messages(client, headers, plan_id)]
    assert kinds == [("user", "message"), ("system", "error")]


# ---------------------------------------------------------------------------
# Continue
# ---------------------------------------------------------------------------


async def test_continue_shows_the_pending_question_once(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    first = await _post(client, headers, plan_id, "continue")
    second = await _post(client, headers, plan_id, "continue")

    assert _stored(first) == [("expected_input", "Where are you going?")]
    assert _stored(second) == []
    assert len(await _messages(client, headers, plan_id)) == 1


async def test_continue_moves_on_from_a_finished_stage(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await _create(client, headers, **BASICS, budget={"amount": 5000})

    events = await _post(client, headers, plan_id, "continue")

    assert [kind for kind, _ in _stored(events)] == ["stage", "expected_input"]
    assert _done(events).plan.stage == "flights"


async def test_a_search_that_cannot_run_leaves_retry_pending(
    app: FastAPI, client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await _create(client, headers, **BASICS)
    await _move_to(app, plan_id, PlanStage.FLIGHTS)

    events = await _answer(client, headers, plan_id, "flight_preferences", {"cabin": "economy"})

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Find flights out…"
    assert _stored(events)[-1] == (
        "error",
        "Searching isn't available here yet (find flights out).",
    )
    assert _done(events).plan.assessment.next_action.type == "run_search"
