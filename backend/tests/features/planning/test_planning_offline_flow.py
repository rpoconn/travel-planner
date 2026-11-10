"""A whole trip over HTTP with the offline fake as the model (`LLM_PROVIDER=fake`), using
only the interface: forms, picks and buttons. This is the path the Playwright suite walks.
"""

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from features.planning.agent.offline import COMPLETE, SUGGEST_REASON
from features.planning.offline import planning_reply
from features.planning.schemas import ReviewProposal
from shared.config import Settings
from shared.llm.fake import FakeLLM
from tests.features.planning.planning_http import (
    LISBON,
    MAY,
    answer,
    asking,
    choices,
    create_plan,
    done,
    pick,
    post,
    say,
    stored,
)


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # A whole trip is more turns than a minute's worth for one user.
    return settings.model_copy(update={"chat_rate_limit_per_minute": 0})


@pytest.fixture(autouse=True)
def offline_llm(app: FastAPI) -> None:
    app.state.llm = FakeLLM(fallback=planning_reply)


async def test_a_whole_trip_is_planned_with_forms_and_picks(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await create_plan(client, headers)

    events = await post(client, headers, plan_id, "continue")
    assert asking(events) == "destination"

    await answer(client, headers, plan_id, "destination", {"destination": LISBON})
    await answer(client, headers, plan_id, "dates", {"dates": MAY})
    await answer(client, headers, plan_id, "travelers", {"adults": 2})
    events = await answer(client, headers, plan_id, "origin", {"origin": {"name": "Philadelphia"}})
    assert asking(events) == "flight_preferences"

    # Each set of preferences leads straight to its search and the choices it found.
    events = await answer(client, headers, plan_id, "flight_preferences", {"cabin": "economy"})
    assert stored(events)[-1] == ("choices", "Pick your flight out.")
    events = await pick(client, headers, plan_id, choices(events), "1")
    assert stored(events)[-1] == ("choices", "Pick your flight home.")
    events = await pick(client, headers, plan_id, choices(events), "1")
    assert asking(events) == "lodging_preferences"

    events = await answer(
        client, headers, plan_id, "lodging_preferences", {"hotel_class": "midrange"}
    )
    events = await pick(client, headers, plan_id, choices(events), "1")
    assert asking(events) == "activity_preferences"

    events = await answer(
        client, headers, plan_id, "activity_preferences", {"interests": ["museum"]}
    )
    events = await pick(client, headers, plan_id, choices(events), "1")

    # What is left are the rides between them, one pick each.
    for _ in range(12):
        if done(events).plan.complete:
            break
        assert choices(events).slot == "transfer"
        events = await pick(client, headers, plan_id, choices(events), "1")

    plan = done(events).plan
    assert plan.complete and plan.pending is None
    assert stored(events)[-1] == ("message", COMPLETE)
    assert [problem for problem in plan.problems if problem.severity == "blocking"] == []


async def test_a_suggestion_is_approved_with_its_button(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    plan_id = await create_plan(
        client,
        headers,
        destination=LISBON,
        start_date=MAY["start"],
        end_date=MAY["end"],
        travelers={"adults": 2},
        origin={"name": "Philadelphia"},
    )
    await say(client, headers, plan_id, "We're driving.")
    events = await answer(
        client, headers, plan_id, "lodging_preferences", {"hotel_class": "midrange"}
    )
    await pick(client, headers, plan_id, choices(events), "1")
    events = await answer(
        client, headers, plan_id, "activity_preferences", {"interests": ["museum"]}
    )
    events = await pick(client, headers, plan_id, choices(events), "1")
    activity = next(item.title for item in done(events).plan.items if item.type == "activity")

    events = await say(client, headers, plan_id, "What would you drop?")

    assert stored(events)[-1] == ("message", SUGGEST_REASON)
    proposal = done(events).plan.pending
    assert isinstance(proposal, ReviewProposal)

    events = await post(
        client,
        headers,
        plan_id,
        f"proposals/{proposal.proposal_id}/decision",
        {"decision": "approve"},
    )

    kind, content = stored(events)[1]
    assert kind == "changes" and content.startswith(f"Removed “{activity}”.")
    assert activity not in [item.title for item in done(events).plan.items]
