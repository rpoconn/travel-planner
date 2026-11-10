"""Proposals: storing dry-run changes for the user's decision, and approving or rejecting
them.

The plan is the Lisbon trip without flights (`planning_http.reach`): its items, in
time order, are 1 "Friend's flat" (May 1-8) and 2 "Dinner in" (May 2).
"""

from datetime import date
from uuid import uuid4

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue, TypeAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.tools import ToolContext
from features.planning import proposals
from features.planning import service as planning_service
from features.planning.commands import dry_run
from features.planning.schemas import (
    AgentCommand,
    Candidate,
    CandidateChange,
    Proposal,
    ReviewProposal,
    SuggestedChange,
    TravelPlan,
)
from features.plans import service as plans_service
from features.plans.schemas import PlanUpdate
from tests.features.planning.planning_http import BASICS, FLAT, LISBON_TZ, create_plan, reach

# Fixed so the trip (May 2027) stays in the future.
TODAY = date(2026, 11, 1)

COMMANDS: TypeAdapter[list[AgentCommand]] = TypeAdapter(list[AgentCommand])
DINNER_REF = 2
REMOVE_DINNER: dict[str, JsonValue] = {"type": "remove_item", "item": DINNER_REF}
ADD_TOUR: dict[str, JsonValue] = {"type": "add_candidate", "candidate": "a1"}

TOUR = Candidate(
    id="1",
    title="Tram tour",
    changes=[
        CandidateChange(
            name="add_activity",
            arguments={
                "title": "Tram tour",
                "start": "2027-05-03T10:00",
                "end": "2027-05-03T12:00",
                "timezone": LISBON_TZ,
                "place": FLAT,
                "category": "tour",
            },
            summary="Add Tram tour",
        )
    ],
)


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def ctx(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], session: AsyncSession
) -> ToolContext:
    plan_id = await create_plan(client, headers, **BASICS)
    await reach(app, client, headers, plan_id)
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    plan = TravelPlan.model_validate_json(response.content)
    return ToolContext(session=session, user_id=plan.user_id, plan_id=plan.id, today=TODAY)


async def _plan(ctx: ToolContext) -> TravelPlan:
    return await planning_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)


async def _suggest(
    ctx: ToolContext, *commands: dict[str, JsonValue], found: Candidate = TOUR
) -> Proposal:
    """Dry-run the commands and store them as a proposal, as `suggest_changes` will. `found`
    is the search result "a1" names."""
    plan = await _plan(ctx)
    checked = await dry_run(ctx, plan, COMMANDS.validate_python(commands), {"a1": found})
    assert checked.rejected == []
    return await proposals.open_proposal(
        ctx.session,
        plan_id=plan.id,
        plan_version=plan.version,
        reason="A quieter second day.",
        changes=checked.changes,
    )


async def _titles(ctx: ToolContext) -> list[str]:
    return [item.title for item in (await _plan(ctx)).items]


async def _touch(ctx: ToolContext) -> None:
    """Change the plan (its notes), so its version moves on."""
    await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, PlanUpdate(notes="!"))


# ---------------------------------------------------------------------------
# Storing
# ---------------------------------------------------------------------------


async def test_a_proposal_round_trips(ctx: ToolContext) -> None:
    proposal = await _suggest(ctx, REMOVE_DINNER, ADD_TOUR)

    current = await proposals.current_proposal(ctx.session, ctx.plan_id)

    assert current == proposal
    assert (current.status, current.reason) == ("pending", "A quieter second day.")
    assert [change.summary for change in current.changes] == [
        "Remove “Dinner in”",
        "Add “Tram tour”",
    ]
    # The candidate is kept with its command, so approving needs no search.
    assert current.changes[1].candidate == TOUR
    assert current.plan_version == (await _plan(ctx)).version


async def test_opening_a_proposal_supersedes_the_pending_one(ctx: ToolContext) -> None:
    first = await _suggest(ctx, REMOVE_DINNER)
    second = await _suggest(ctx, ADD_TOUR)

    current = await proposals.current_proposal(ctx.session, ctx.plan_id)

    assert current is not None and current.id == second.id
    with pytest.raises(proposals.ProposalClosedError, match="superseded"):
        await proposals.reject_proposal(ctx.session, ctx.plan_id, first.id)


async def test_another_plans_proposal_is_not_found(ctx: ToolContext) -> None:
    proposal = await _suggest(ctx, REMOVE_DINNER)

    with pytest.raises(proposals.ProposalNotFoundError):
        await proposals.reject_proposal(ctx.session, uuid4(), proposal.id)


# ---------------------------------------------------------------------------
# Deciding
# ---------------------------------------------------------------------------


async def test_approving_writes_every_change(ctx: ToolContext) -> None:
    proposal = await _suggest(ctx, REMOVE_DINNER, ADD_TOUR)

    decided, record = await proposals.approve_proposal(ctx, await _plan(ctx), proposal.id)

    assert decided.status == "approved" and decided.decided_at is not None
    assert [change.summary for change in record.applied] == [
        "Removed “Dinner in”",
        "Added “Tram tour”",
    ]
    assert await _titles(ctx) == ["Friend's flat", "Tram tour"]
    assert await proposals.current_proposal(ctx.session, ctx.plan_id) is None


async def test_rejecting_writes_nothing(ctx: ToolContext) -> None:
    proposal = await _suggest(ctx, REMOVE_DINNER)
    version = (await _plan(ctx)).version

    decided = await proposals.reject_proposal(ctx.session, ctx.plan_id, proposal.id)

    assert decided.status == "rejected"
    assert (await _plan(ctx)).version == version
    assert await proposals.current_proposal(ctx.session, ctx.plan_id) is None


async def test_a_decided_proposal_cannot_be_decided_again(ctx: ToolContext) -> None:
    proposal = await _suggest(ctx, REMOVE_DINNER)
    await proposals.reject_proposal(ctx.session, ctx.plan_id, proposal.id)

    with pytest.raises(proposals.ProposalClosedError, match="already rejected"):
        await proposals.approve_proposal(ctx, await _plan(ctx), proposal.id)

    assert await _titles(ctx) == ["Friend's flat", "Dinner in"]


async def test_a_proposal_for_an_earlier_plan_cannot_be_approved(ctx: ToolContext) -> None:
    # Item numbers are only good for the plan they were read from.
    proposal = await _suggest(ctx, REMOVE_DINNER)
    await _touch(ctx)

    with pytest.raises(proposals.ProposalStaleError, match="changed since"):
        await proposals.approve_proposal(ctx, await _plan(ctx), proposal.id)

    assert await _titles(ctx) == ["Friend's flat", "Dinner in"]
    assert await proposals.current_proposal(ctx.session, ctx.plan_id) is None


async def test_an_approval_that_no_longer_fits_fails_whole(ctx: ToolContext) -> None:
    tour = TOUR.changes[0]
    cost: dict[str, JsonValue] = {"amount": 80, "currency": "USD"}
    priced = tour.model_copy(update={"arguments": {**tour.arguments, "cost": cost}})
    proposal = await _suggest(
        ctx, REMOVE_DINNER, ADD_TOUR, found=TOUR.model_copy(update={"changes": [priced]})
    )
    # A budget the tour breaks, set behind the plan's back (no new version), stands in for
    # a rule that held at the dry run and no longer does.
    row = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    row.budget = {"amount": "50", "currency": "USD"}
    await ctx.session.commit()

    decided, record = await proposals.approve_proposal(ctx, await _plan(ctx), proposal.id)

    assert decided.status == "failed"
    assert record.applied == []
    assert [rejection.summary for rejection in record.rejected] == ["Add “Tram tour”"]
    # The removal that did fit isn't written either.
    assert await _titles(ctx) == ["Friend's flat", "Dinner in"]


# ---------------------------------------------------------------------------
# Pending on the plan
# ---------------------------------------------------------------------------


async def test_the_plan_waits_on_its_pending_proposal(ctx: ToolContext) -> None:
    proposal = await _suggest(ctx, REMOVE_DINNER, ADD_TOUR)

    plan = await _plan(ctx)

    assert plan.pending == ReviewProposal(
        proposal_id=proposal.id,
        prompt="A quieter second day.",
        changes=[
            SuggestedChange(name="remove_item", summary="Remove “Dinner in”"),
            SuggestedChange(name="add_candidate", summary="Add “Tram tour”", candidate=TOUR),
        ],
    )


async def test_a_stale_or_decided_proposal_is_not_waited_on(ctx: ToolContext) -> None:
    await _suggest(ctx, REMOVE_DINNER)
    await _touch(ctx)
    assert (await _plan(ctx)).pending is None

    proposal = await _suggest(ctx, REMOVE_DINNER)
    await proposals.reject_proposal(ctx.session, ctx.plan_id, proposal.id)
    assert (await _plan(ctx)).pending is None
