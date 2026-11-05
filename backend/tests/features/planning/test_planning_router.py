"""The planning routes over HTTP: forms, free text and continue, for the basics stage and the
move on to flights (the flights stage itself is in `test_planning_flights`). The model is a
strict `FakeLLM`: a test that reaches it unscripted fails."""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue

from features.planning.events import (
    PlanningDoneEvent,
    PlanningErrorEvent,
    PlanningMessageEvent,
    PlanningPlanEvent,
    PlanningStatusEvent,
    PlanningTokenEvent,
)
from features.planning.extractor import extraction_schema
from features.planning.schemas import AskInput
from features.plans.schemas import PlanStage, SlotName
from shared.flights.fake import FakeFlightProvider
from shared.flights.types import FlightSearchError
from shared.llm.fake import FakeLLM, json_reply, text
from shared.llm.types import LLMError
from tests.conftest import RegisterUser
from tests.features.planning.planning_http import (
    BASICS,
    LISBON,
    MAY,
    answer,
    asking,
    create_plan,
    done,
    messages,
    move_to,
    post,
    say,
    stored,
)

# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


async def test_a_form_answer_is_stored_and_the_next_question_asked(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    events = await answer(client, headers, plan_id, "destination", {"destination": LISBON})

    assert [type(e) for e in events] == [
        PlanningMessageEvent,
        PlanningPlanEvent,
        PlanningMessageEvent,
        PlanningDoneEvent,
    ]
    assert stored(events) == [
        ("input", "Lisbon, Portugal"),
        ("expected_input", "When are you going?"),
    ]
    plan = done(events).plan
    assert plan.destination is not None and plan.destination.name == "Lisbon"
    assert plan.title == "Trip to Lisbon"
    assert asking(events) == "dates"


async def test_the_transcript_keeps_each_messages_payload(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    await answer(client, headers, plan_id, "destination", {"destination": LISBON})

    reply, asked = await messages(client, headers, plan_id)

    assert (reply.role, reply.kind) == ("user", "input")
    assert reply.payload is not None
    assert reply.payload.model_dump(mode="json") == {
        "type": "answer",
        "slot": "destination",
        "value": {
            "destination": {**LISBON, "iata": None, "address": None, "lat": None, "lng": None}
        },
        "skipped": False,
    }
    assert (asked.role, asked.kind) == ("assistant", "expected_input")
    assert isinstance(asked.payload, AskInput)
    assert asked.payload.slot == "dates"
    assert asked.payload.fields[0].kind == "date_range"


async def test_finishing_basics_moves_on_to_flights(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    await answer(client, headers, plan_id, "destination", {"destination": LISBON})
    await answer(client, headers, plan_id, "dates", {"dates": MAY})
    await answer(client, headers, plan_id, "travelers", {"adults": 2, "children": 1})
    await answer(client, headers, plan_id, "origin", {"origin": {"name": "Philadelphia"}})

    events = await post(client, headers, plan_id, "inputs", {"type": "skip", "slot": "budget"})

    assert stored(events) == [
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
    plan = done(events).plan
    assert plan.stage == "flights"
    assert plan.preferences.declined == ["budget"]
    assert asking(events) == "flight_preferences"
    # The transcript is in the order the messages were stored.
    kinds = [m.kind for m in await messages(client, headers, plan_id)]
    assert kinds[-3:] == ["input", "stage", "expected_input"]


async def test_preferences_are_stored_on_the_plan(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await create_plan(client, headers, **BASICS, budget={"amount": 5000})
    await post(client, headers, plan_id, "continue")  # basics are done: on to flights

    events = await answer(
        client, headers, plan_id, "flight_preferences", {"cabin": "business", "max_stops": 0}
    )

    plan = done(events).plan
    assert plan.preferences.flights is not None
    assert plan.preferences.flights.cabin == "business"
    assert plan.preferences.flights.max_stops == 0
    assert stored(events)[0] == ("input", "Business cabin, nonstop")


async def test_answering_a_question_not_being_asked_is_a_conflict(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    response = await client.post(
        f"/plans/{plan_id}/planning/inputs",
        json={"type": "answer", "slot": "dates", "value": {"dates": MAY}},
        headers=headers,
    )

    assert response.status_code == 409
    assert await messages(client, headers, plan_id) == []


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
    body: dict[str, JsonValue],
    detail: str,
) -> None:
    response = await client.post(f"/plans/{plan_id}/planning/inputs", json=body, headers=headers)

    assert response.status_code == 422
    assert response.json()["detail"] == detail
    assert await messages(client, headers, plan_id) == []


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

    events = await say(client, headers, plan_id, "Thinking Lisbon this spring")

    assert stored(events) == [
        ("message", "Thinking Lisbon this spring"),
        ("message", "Got it: Lisbon, Portugal."),
        ("expected_input", "When are you going?"),
    ]
    (request,) = fake_llm.requests
    assert request.format == extraction_schema(SlotName.DESTINATION)
    assert request.think is False
    assert asking(events) == "dates"


async def test_unrelated_text_gets_the_slots_nudge_and_changes_nothing(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(json_reply({"status": "unrelated", "value": None}))

    events = await say(client, headers, plan_id, "Write me a poem about cheese")

    assert stored(events) == [
        ("message", "Write me a poem about cheese"),
        ("nudge", "Let's start with where you're going: a city, region or country."),
    ]
    assert done(events).plan.version == 1
    assert asking(events) == "destination"


async def test_a_question_is_answered_then_the_slot_asked_again(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    await post(client, headers, plan_id, "continue")  # shows "Where are you going?"
    fake_llm.queue(
        json_reply({"status": "question", "value": None}),
        text("Lisbon is lovely in May."),
    )

    events = await say(client, headers, plan_id, "Is Lisbon nice in May?")

    tokens = "".join(e.text for e in events if isinstance(e, PlanningTokenEvent))
    assert tokens == "Lisbon is lovely in May."
    assert stored(events) == [
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
    plan_id = await create_plan(client, headers, destination=LISBON)
    backwards: dict[str, JsonValue] = {"dates": {"start": "2027-05-08", "end": "2027-05-01"}}
    fake_llm.queue(json_reply({"status": "answered", "value": backwards}))

    events = await say(client, headers, plan_id, "the 8th to the 1st")

    assert stored(events)[1:] == [
        ("nudge", "That doesn't work: The trip has to end on or after the day it starts."),
        ("expected_input", "When are you going?"),
    ]
    assert done(events).plan.start_date is None


async def test_with_nothing_to_answer_text_is_a_question_and_nothing_runs(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    fake_llm: FakeLLM,
    fake_flights: FakeFlightProvider,
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)
    await move_to(app, plan_id, PlanStage.FLIGHTS)
    # The search fails, so a search (Retry) is what's pending.
    fake_flights.queue(FlightSearchError("Flight search is down"))
    await answer(client, headers, plan_id, "flight_preferences", {"cabin": "economy"})
    fake_llm.queue(text("Mornings are usually quieter."))

    events = await say(client, headers, plan_id, "When do flights leave?")

    assert stored(events) == [
        ("message", "When do flights leave?"),
        ("message", "Mornings are usually quieter."),
    ]
    assert not any(isinstance(e, PlanningStatusEvent) for e in events)
    assert done(events).plan.assessment.next_action.type == "run_search"


async def test_a_model_failure_is_stored_as_an_error(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(LLMError("Could not reach Ollama"))

    events = await say(client, headers, plan_id, "Lisbon")

    error = events[-1]
    assert isinstance(error, PlanningErrorEvent)
    assert error.detail == "Could not reach Ollama"
    assert error.message is not None and error.message.kind == "error"
    kinds = [(m.role, m.kind) for m in await messages(client, headers, plan_id)]
    assert kinds == [("user", "message"), ("system", "error")]


# ---------------------------------------------------------------------------
# Continue
# ---------------------------------------------------------------------------


async def test_continue_shows_the_pending_question_once(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    first = await post(client, headers, plan_id, "continue")
    second = await post(client, headers, plan_id, "continue")

    assert stored(first) == [("expected_input", "Where are you going?")]
    assert stored(second) == []
    assert len(await messages(client, headers, plan_id)) == 1


async def test_continue_moves_on_from_a_finished_stage(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await create_plan(client, headers, **BASICS, budget={"amount": 5000})

    events = await post(client, headers, plan_id, "continue")

    assert [kind for kind, _ in stored(events)] == ["stage", "expected_input"]
    assert done(events).plan.stage == "flights"


async def test_a_failed_search_leaves_retry_pending_and_continue_retries(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], fake_flights: FakeFlightProvider
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)
    await move_to(app, plan_id, PlanStage.FLIGHTS)
    fake_flights.queue(FlightSearchError("Flight search is down"))

    events = await answer(client, headers, plan_id, "flight_preferences", {"cabin": "economy"})

    status = next(e for e in events if isinstance(e, PlanningStatusEvent))
    assert status.text == "Find flights out…"
    assert stored(events)[-1] == ("error", "Flight search is down")
    assert done(events).plan.assessment.next_action.type == "run_search"

    retried = await post(client, headers, plan_id, "continue")

    assert stored(retried) == [("choices", "Pick your flight out.")]
    assert done(retried).plan.assessment.next_action.type == "offer_choice"
    assert len(fake_flights.searches) == 2
