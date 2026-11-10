"""The planning routes over HTTP: forms, free text and continue, the checks that run before a
stream opens, and how a turn that fails is reported. The model is a strict `FakeLLM`
scripted with tool calls (`scripts`): a test that reaches it unscripted fails.

Picks are in `test_planning_picks`, suggestions in `test_planning_decisions`, the Remove
button in `test_planning_removals`.
"""

from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue

from features.planning.agent.context import FormView
from features.planning.events import (
    PlanningErrorEvent,
    PlanningTokenEvent,
)
from features.planning.schemas import AskInput
from features.planning.turn import GENERIC_FAILURE
from features.plans.schemas import SlotName
from shared.llm.fake import FakeLLM, FakeRequest, text
from shared.llm.types import LLMError, LLMMessage
from tests.conftest import RegisterUser
from tests.features.planning.agent_helpers import brief_of, tools_of
from tests.features.planning.planning_http import (
    BASICS,
    LISBON,
    MAY,
    answer,
    asking,
    create_plan,
    done,
    messages,
    post,
    say,
    status_of,
    stored,
)
from tests.features.planning.scripts import apply, ask

ASK_DESTINATION = ask("Where are you going?", "destination")
ASK_DATES = ask("When are you going?", "dates")
SET_LISBON: dict[str, JsonValue] = {
    "type": "set_destination",
    "name": "Lisbon",
    "country": "Portugal",
}


async def _asked(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    fake_llm: FakeLLM,
    reply: LLMMessage = ASK_DESTINATION,
) -> None:
    """Start the plan: the planner asks its first question."""
    fake_llm.queue(reply)
    await post(client, headers, plan_id, "continue")


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


async def test_a_form_answer_is_written_then_the_planner_carries_on(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    await _asked(client, headers, plan_id, fake_llm)
    fake_llm.queue(ASK_DATES)

    events = await answer(client, headers, plan_id, "destination", {"destination": LISBON})

    assert [event.type for event in events] == ["message", "plan_updated", "message", "done"]
    assert stored(events) == [
        ("input", "Lisbon, Portugal"),
        ("expected_input", "When are you going?"),
    ]
    plan = done(events).plan
    assert plan.destination is not None and plan.destination.name == "Lisbon"
    assert plan.title == "Trip to Lisbon"
    assert asking(events) == "dates"
    # The planner is asked once the answer is written, with it in the conversation.
    brief = brief_of(fake_llm.requests[-1])
    assert brief.said is None
    assert (brief.conversation[-1].who, brief.conversation[-1].text) == (
        "user",
        "Lisbon, Portugal",
    )
    assert brief.plan.trip.destination == "Lisbon, Portugal"


async def test_the_transcript_keeps_each_messages_payload(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    await _asked(client, headers, plan_id, fake_llm)
    fake_llm.queue(ASK_DATES)
    await answer(client, headers, plan_id, "destination", {"destination": LISBON})

    _, reply, asked = await messages(client, headers, plan_id)

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


async def test_an_optional_question_can_be_skipped(
    client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)
    await _asked(client, headers, plan_id, fake_llm, ask("A budget for the trip?", "budget"))
    fake_llm.queue(text("No budget it is."))

    events = await post(client, headers, plan_id, "inputs", {"type": "skip", "slot": "budget"})

    assert stored(events) == [("input", "Skip"), ("message", "No budget it is.")]
    assert done(events).plan.preferences.declined == ["budget"]
    assert brief_of(fake_llm.requests[-1]).plan.trip.declined == [SlotName.BUDGET]


async def test_preferences_are_stored_on_the_plan(
    client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)
    await _asked(
        client, headers, plan_id, fake_llm, ask("How do you like to fly?", "flight_preferences")
    )
    fake_llm.queue(text("Noted."))

    events = await answer(
        client, headers, plan_id, "flight_preferences", {"cabin": "business", "max_stops": 0}
    )

    plan = done(events).plan
    assert plan.preferences.flights is not None
    assert plan.preferences.flights.cabin == "business"
    assert plan.preferences.flights.max_stops == 0
    assert stored(events)[0] == ("input", "Business cabin, nonstop")


async def test_answering_a_question_not_being_asked_is_a_conflict(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    dates: dict[str, JsonValue] = {"type": "answer", "slot": "dates", "value": {"dates": MAY}}

    # Nothing has been asked yet.
    assert (await status_of(client, headers, plan_id, "inputs", dates))[0] == 409

    await _asked(client, headers, plan_id, fake_llm)  # the destination, not the dates
    assert (await status_of(client, headers, plan_id, "inputs", dates))[0] == 409
    assert len(await messages(client, headers, plan_id)) == 1


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
    fake_llm: FakeLLM,
    body: dict[str, JsonValue],
    detail: str,
) -> None:
    await _asked(client, headers, plan_id, fake_llm)

    assert await status_of(client, headers, plan_id, "inputs", body) == (422, detail)
    assert len(await messages(client, headers, plan_id)) == 1


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


async def test_free_text_goes_to_the_planner_with_the_form_it_was_typed_under(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    await _asked(client, headers, plan_id, fake_llm)
    fake_llm.queue(apply("Lisbon", SET_LISBON), ASK_DATES)

    events = await say(client, headers, plan_id, "Lisbon, I think")

    assert stored(events) == [
        ("message", "Lisbon, I think"),
        ("changes", "Destination: Lisbon, Portugal."),
        ("expected_input", "When are you going?"),
    ]
    assert done(events).plan.title == "Trip to Lisbon"
    request = fake_llm.requests[1]
    brief = brief_of(request)
    assert brief.said == "Lisbon, I think"
    assert brief.plan.pending == FormView(
        slot=SlotName.DESTINATION, question="Where are you going?"
    )
    # Only a typed turn may write what the text asks for.
    assert "apply_user_changes" in tools_of(request)
    assert "apply_user_changes" not in tools_of(fake_llm.requests[0])


async def test_a_question_is_answered_and_nothing_changes(
    client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)
    fake_llm.queue(text("May is mild and dry."))

    events = await say(client, headers, plan_id, "What's Lisbon like in May?")

    tokens = [event.text for event in events if isinstance(event, PlanningTokenEvent)]
    assert "".join(tokens) == "May is mild and dry."
    assert stored(events) == [
        ("message", "What's Lisbon like in May?"),
        ("message", "May is mild and dry."),
    ]
    plan = done(events).plan
    assert plan.version == 1 and plan.pending is None


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_a_model_failure_is_stored_and_continue_tries_again(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(LLMError("Could not reach the model"))

    events = await say(client, headers, plan_id, "Lisbon")

    error = events[-1]
    assert isinstance(error, PlanningErrorEvent)
    assert error.detail == "Could not reach the model"
    assert error.message is not None and error.message.kind == "error"
    kinds = [(m.role, m.kind) for m in await messages(client, headers, plan_id)]
    assert kinds == [("user", "message"), ("system", "error")]

    fake_llm.queue(ASK_DESTINATION)
    events = await post(client, headers, plan_id, "continue")

    assert stored(events) == [("expected_input", "Where are you going?")]
    # The failure notice isn't part of the conversation the planner reads.
    assert [line.text for line in brief_of(fake_llm.requests[-1]).conversation] == ["Lisbon"]


async def test_an_unexpected_failure_is_reported_without_its_details(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    # Nothing is scripted, so the fake raises a plain RuntimeError.
    events = await post(client, headers, plan_id, "continue")

    error = events[-1]
    assert isinstance(error, PlanningErrorEvent)
    assert error.detail == GENERIC_FAILURE


# ---------------------------------------------------------------------------
# Continue
# ---------------------------------------------------------------------------


async def test_continue_starts_a_new_plan(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    fake_llm.queue(ASK_DESTINATION)

    events = await post(client, headers, plan_id, "continue")

    assert stored(events) == [("expected_input", "Where are you going?")]
    assert asking(events) == "destination"
    brief = brief_of(fake_llm.requests[0])
    assert (brief.said, brief.focus, brief.conversation) == (None, None, [])
    assert [problem.id for problem in brief.plan.problems][:2] == [
        "missing_destination",
        "missing_dates",
    ]


async def test_fix_this_tells_the_planner_which_problem(
    client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)
    fake_llm.queue(ask("What do you like to do?", "activity_preferences"))

    events = await post(client, headers, plan_id, "continue", {"problem_id": "no_activities"})

    assert asking(events) == "activity_preferences"
    assert brief_of(fake_llm.requests[0]).focus == "no_activities"


async def test_a_problem_the_plan_no_longer_has_is_a_conflict(
    client: AsyncClient, headers: dict[str, str], fake_llm: FakeLLM
) -> None:
    plan_id = await create_plan(client, headers, **BASICS)

    status, detail = await status_of(
        client, headers, plan_id, "continue", {"problem_id": "missing_dates"}
    )

    assert status == 409 and "no longer" in detail
    assert fake_llm.requests == []


# ---------------------------------------------------------------------------
# One turn at a time
# ---------------------------------------------------------------------------


async def test_a_plan_takes_one_turn_at_a_time(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    running: set[UUID] = app.state.planning_turns
    held: list[bool] = []

    def reply(request: FakeRequest) -> LLMMessage:
        held.append(UUID(plan_id) in running)
        return text("Hello.")

    app.state.llm = FakeLLM(fallback=reply)

    await post(client, headers, plan_id, "continue")

    # Held while the turn streams, and let go once it is done.
    assert held == [True]
    assert running == set()

    running.add(UUID(plan_id))  # another turn is still streaming
    status, detail = await status_of(client, headers, plan_id, "continue")
    assert status == 409 and "still working" in detail

    # Another plan isn't held up.
    running.add(uuid4())
    running.discard(UUID(plan_id))
    await post(client, headers, plan_id, "continue")
