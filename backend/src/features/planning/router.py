"""The planning flow's routes: the transcript, form answers and picks, free text, an item's
Remove button, a suggestion's Approve / Reject, and "continue".

Every check that can fail (the plan, what it is waiting on, the answer's shape) runs in a
dependency, before the event stream opens, so a failure is a plain 404 / 409 / 422.

Every POST runs the planner's loop, so each calls the model: all of them are rate limited,
and a plan takes one turn at a time (a second one while the first is streaming is a 409).
"""

from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.sse import EventSourceResponse

from features.auth.dependencies import CurrentUser
from features.chat import service as chat_service
from features.chat.dependencies import LLMDep, limit_chat_messages
from features.planning import service as planning_service
from features.planning.agent.tools import TurnContext
from features.planning.answers import AnswerError, parse_answer
from features.planning.commands import removal
from features.planning.events import (
    ContinueRequest,
    ItemRemoval,
    PlanningEvent,
    PlanningMessage,
    PlanningText,
    ProposalDecision,
)
from features.planning.picks import PickError, picked_candidates
from features.planning.schemas import (
    AskInput,
    ChoicePick,
    OfferChoice,
    PlanningInput,
    ReviewProposal,
    SlotSkip,
)
from features.planning.turn import (
    Answer,
    Continue,
    Decide,
    Pick,
    Remove,
    Skip,
    Text,
    Trigger,
    run_turn,
)
from features.plans import service as plans_service
from features.plans.models import Plan
from shared.db import SessionDep
from shared.errors import error_responses
from shared.flights.provider import FlightsDep
from shared.hotels.provider import HotelsDep
from shared.places.provider import PlacesDep

router = APIRouter(
    prefix="/plans/{plan_id}/planning",
    tags=["planning"],
    responses=error_responses(status.HTTP_401_UNAUTHORIZED, status.HTTP_404_NOT_FOUND),
)

# What every route that runs a turn can answer besides its own checks.
_TURN_RESPONSES = error_responses(status.HTTP_409_CONFLICT, status.HTTP_429_TOO_MANY_REQUESTS)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------


async def get_owned_plan(plan_id: UUID, user: CurrentUser, session: SessionDep) -> Plan:
    try:
        return await plans_service.get_plan(session, user.id, plan_id)
    except plans_service.PlanNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found") from exc


OwnedPlan = Annotated[Plan, Depends(get_owned_plan)]


_STALE = "That question isn't the one being asked any more. Reload to see the latest."


def _checked_pick(body: ChoicePick, action: OfferChoice) -> Pick:
    try:
        candidates = picked_candidates(action, body.candidate_ids)
    except PickError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return Pick(action=action, candidates=candidates)


async def get_checked_input(body: PlanningInput, plan: OwnedPlan, session: SessionDep) -> Trigger:
    """The submitted form or pick as a turn trigger: 409 unless it answers what is being
    asked (the slot, or the open offer), 422 when the value doesn't fit the slot, a required
    slot is skipped, or the pick isn't one the offer allows."""
    snapshot = await planning_service.snapshot(session, plan)
    action = snapshot.pending
    if isinstance(body, ChoicePick):
        if not isinstance(action, OfferChoice) or action.offer_id != body.offer_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE)
        return _checked_pick(body, action)
    if not isinstance(action, AskInput) or action.slot != body.slot:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE)
    if isinstance(body, SlotSkip):
        if not action.optional:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="This question can't be skipped.",
            )
        return Skip(slot=body.slot)
    try:
        answer = parse_answer(body.slot, body.value, date.today())
    except AnswerError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    return Answer(slot=body.slot, answer=answer)


CheckedInput = Annotated[Trigger, Depends(get_checked_input)]


async def get_checked_continue(
    plan: OwnedPlan, session: SessionDep, body: ContinueRequest | None = None
) -> Continue:
    """Continue as a turn trigger: 409 when it asks for more options and the plan isn't
    showing choices that can be searched again, or names a problem the plan no longer has."""
    if body is None or not (body.refresh or body.problem_id):
        return Continue()
    snapshot = await planning_service.snapshot(session, plan)
    if body.refresh:
        action = snapshot.pending
        if not isinstance(action, OfferChoice) or not action.can_refresh:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="There are no choices to look for other options for.",
            )
        return Continue(refresh=True)
    if all(problem.id != body.problem_id for problem in snapshot.problems):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That's no longer something the trip needs. Reload to see the latest.",
        )
    return Continue(problem_id=body.problem_id)


CheckedContinue = Annotated[Continue, Depends(get_checked_continue)]


async def get_checked_removal(body: ItemRemoval, plan: OwnedPlan, session: SessionDep) -> Remove:
    """The Remove button as a turn trigger: 404 unless the item is on the plan's itinerary."""
    snapshot = await planning_service.snapshot(session, plan)
    if removal(snapshot, body.item_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="That item is no longer in this plan"
        )
    return Remove(item_id=body.item_id)


CheckedRemoval = Annotated[Remove, Depends(get_checked_removal)]


async def get_checked_decision(
    proposal_id: UUID, body: ProposalDecision, plan: OwnedPlan, session: SessionDep
) -> Decide:
    """Approve / Reject as a turn trigger: 409 unless the suggestion is the one the plan is
    waiting on (it was decided, replaced, or the plan changed under it)."""
    snapshot = await planning_service.snapshot(session, plan)
    waiting = snapshot.pending
    if not isinstance(waiting, ReviewProposal) or waiting.proposal_id != proposal_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "That suggestion isn't waiting for a decision any more. Reload to see the latest."
            ),
        )
    return Decide(proposal_id=proposal_id, approve=body.decision == "approve")


CheckedDecision = Annotated[Decide, Depends(get_checked_decision)]


async def hold_turn(request: Request, plan: OwnedPlan) -> AsyncIterator[None]:
    """One turn per plan at a time: 409 while another is still streaming. Two turns at once
    would read the same plan and write over each other (and both would call the model).

    Kept in memory, which fits this single-process app, as the rate limiter does."""
    running: set[UUID] = request.app.state.planning_turns
    # Read now: a turn that fails rolls the session back, and the row can't be read after.
    plan_id = plan.id
    if plan_id in running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The planner is still working on this trip. Give it a moment.",
        )
    running.add(plan_id)
    try:
        # Held until the response has been streamed: FastAPI runs a dependency's exit code
        # after a streaming response finishes.
        yield
    finally:
        running.discard(plan_id)


# Order matters: the rate limit is checked before the plan is held.
_TURN_GUARDS = [Depends(limit_chat_messages), Depends(hold_turn)]


def get_turn_context(
    session: SessionDep,
    llm: LLMDep,
    flights: FlightsDep,
    hotels: HotelsDep,
    places: PlacesDep,
    user: CurrentUser,
    plan: OwnedPlan,
) -> TurnContext:
    return TurnContext(
        session=session,
        llm=llm,
        user_id=user.id,
        plan_id=plan.id,
        today=date.today(),
        flights=flights,
        hotels=hotels,
        places=places,
    )


Turn = Annotated[TurnContext, Depends(get_turn_context)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/messages", operation_id="listPlanningMessages")
async def list_messages(
    plan: OwnedPlan, user: CurrentUser, session: SessionDep
) -> list[PlanningMessage]:
    """The plan's transcript, oldest first, with each message's form, choices or answer."""
    rows = await chat_service.list_messages(session, user.id, plan.id)
    return [PlanningMessage.model_validate(row) for row in rows]


@router.post(
    "/inputs",
    operation_id="submitPlanningInput",
    response_class=EventSourceResponse,
    responses={**_TURN_RESPONSES, **error_responses(status.HTTP_422_UNPROCESSABLE_CONTENT)},
    dependencies=_TURN_GUARDS,
)
async def submit_input(trigger: CheckedInput, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Answer (or skip) the pending form, or pick from the open choices. The answer or
    pick is stored and written at once; then the planner decides what is next, streaming
    `PlanningEvent`s that end in `done`."""
    async for event in run_turn(turn, trigger):
        yield event


@router.post(
    "/chat",
    operation_id="sendPlanningMessage",
    response_class=EventSourceResponse,
    responses=_TURN_RESPONSES,
    dependencies=_TURN_GUARDS,
)
async def send_message(body: PlanningText, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Free text: an answer in the user's own words, a pick, a change to make, or a
    question. The planner reads it and writes only what it asks for."""
    async for event in run_turn(turn, Text(content=body.content)):
        yield event


@router.post(
    "/removals",
    operation_id="removePlanItem",
    response_class=EventSourceResponse,
    responses=_TURN_RESPONSES,
    dependencies=_TURN_GUARDS,
)
async def remove_item(trigger: CheckedRemoval, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Take an item off the trip, as a typed "remove …" would. The item goes at once, with
    the rides to and from it; then the planner decides what the trip needs next, streaming
    `PlanningEvent`s that end in `done`."""
    async for event in run_turn(turn, trigger):
        yield event


@router.post(
    "/proposals/{proposal_id}/decision",
    operation_id="decidePlanningProposal",
    response_class=EventSourceResponse,
    responses=_TURN_RESPONSES,
    dependencies=_TURN_GUARDS,
)
async def decide_proposal(trigger: CheckedDecision, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Approve or reject the suggestion the plan is waiting on. Approving checks its
    changes again and writes them all or nothing; either way the planner then carries on,
    streaming `PlanningEvent`s that end in `done`."""
    async for event in run_turn(turn, trigger):
        yield event


@router.post(
    "/continue",
    operation_id="continuePlanning",
    response_class=EventSourceResponse,
    responses=_TURN_RESPONSES,
    dependencies=_TURN_GUARDS,
)
async def continue_planning(trigger: CheckedContinue, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Let the planner act with no new input: start a new plan, or try again after a
    failure. With `problem_id` ("Fix this"), it works on that problem first. With `refresh`
    ("More options"), the open choices are searched again for ones not offered yet."""
    async for event in run_turn(turn, trigger):
        yield event
