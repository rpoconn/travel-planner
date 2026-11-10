"""A suggestion's Approve and Reject over HTTP: the decision is written at once, then the
planner carries on. The model is a strict `FakeLLM` scripted with tool calls.

The plan has every basic and nothing else; the suggestion is to plan it without flights.
"""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from pydantic import JsonValue

from features.planning.events import PlanningEvent
from features.planning.schemas import ChangesRecord, ReviewProposal
from shared.llm.fake import FakeLLM, text
from tests.features.planning.agent_helpers import brief_of
from tests.features.planning.planning_http import (
    BASICS,
    create_plan,
    done,
    messages,
    post,
    status_of,
    stored,
)
from tests.features.planning.scripts import NO_FLIGHTS, suggest

REASON = "You're close enough to drive."


@pytest.fixture
async def plan_id(client: AsyncClient, headers: dict[str, str]) -> str:
    return await create_plan(client, headers, **BASICS)


async def _suggested(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> ReviewProposal:
    fake_llm.queue(suggest(REASON, NO_FLIGHTS))
    events = await post(client, headers, plan_id, "continue")
    pending = done(events).plan.pending
    assert isinstance(pending, ReviewProposal)
    return pending


def _path(proposal: ReviewProposal) -> str:
    return f"proposals/{proposal.proposal_id}/decision"


async def _decide(
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    proposal: ReviewProposal,
    decision: str,
) -> list[PlanningEvent]:
    return await post(client, headers, plan_id, _path(proposal), {"decision": decision})


# ---------------------------------------------------------------------------
# Suggesting
# ---------------------------------------------------------------------------


async def test_a_suggestion_is_what_the_plan_waits_on(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    proposal = await _suggested(client, headers, plan_id, fake_llm)

    assert proposal.prompt == REASON
    assert [(change.name, change.summary) for change in proposal.changes] == [
        ("set_travel_mode", "Plan the trip without flights")
    ]
    [message] = await messages(client, headers, plan_id)
    assert (message.role, message.kind, message.content) == ("assistant", "message", REASON)


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


async def test_approving_writes_the_changes_then_the_planner_carries_on(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    proposal = await _suggested(client, headers, plan_id, fake_llm)
    fake_llm.queue(text("No flights, then."))

    events = await _decide(client, headers, plan_id, proposal, "approve")

    assert stored(events) == [
        ("message", "Yes, go ahead."),
        ("changes", "The trip has no flights."),
        ("message", "No flights, then."),
    ]
    plan = done(events).plan
    assert plan.flights_skipped is True and plan.pending is None
    assert "missing_outbound" not in [problem.id for problem in plan.problems]
    changes = (await messages(client, headers, plan_id))[2]
    assert isinstance(changes.payload, ChangesRecord)
    assert [change.name for change in changes.payload.applied] == ["set_travel_mode"]
    # The planner was asked after the write, and wasn't offered a writing tool for it.
    brief = brief_of(fake_llm.requests[-1])
    assert brief.said is None and brief.plan.trip.no_flights is True


async def test_rejecting_writes_nothing(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    proposal = await _suggested(client, headers, plan_id, fake_llm)
    fake_llm.queue(text("Flights it is."))

    events = await _decide(client, headers, plan_id, proposal, "reject")

    assert stored(events) == [
        ("message", "No, leave it as it is."),
        ("message", "Flights it is."),
    ]
    plan = done(events).plan
    assert plan.flights_skipped is False and plan.pending is None
    assert plan.version == 1


async def test_a_suggestion_is_decided_once(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    proposal = await _suggested(client, headers, plan_id, fake_llm)
    fake_llm.queue(text("Flights it is."))
    await _decide(client, headers, plan_id, proposal, "reject")
    body: dict[str, JsonValue] = {"decision": "approve"}

    status, detail = await status_of(client, headers, plan_id, _path(proposal), body)

    assert status == 409 and "isn't waiting for a decision" in detail


async def test_a_suggestion_for_an_earlier_plan_cannot_be_decided(
    client: AsyncClient, headers: dict[str, str], plan_id: str, fake_llm: FakeLLM
) -> None:
    proposal = await _suggested(client, headers, plan_id, fake_llm)
    # The plan changes under the suggestion (here, from the plan's own form).
    response = await client.patch(f"/plans/{plan_id}", json={"notes": "!"}, headers=headers)
    assert response.status_code == 200, response.text

    status, _ = await status_of(client, headers, plan_id, _path(proposal), {"decision": "approve"})

    assert status == 409
    plan = await client.get(f"/plans/{plan_id}", headers=headers)
    assert plan.json()["flights_skipped"] is False


async def test_a_suggestion_the_plan_never_had_cannot_be_decided(
    client: AsyncClient, headers: dict[str, str], plan_id: str
) -> None:
    status, _ = await status_of(
        client, headers, plan_id, f"proposals/{uuid4()}/decision", {"decision": "reject"}
    )

    assert status == 409
