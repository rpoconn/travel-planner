"""The planning flow's routes: the transcript, form answers and picks, free text, an item's
Remove button, and "continue".

Every check that can fail (the plan, the pending slot, the answer's shape) runs in a
dependency, before the event stream opens, so a failure is a plain 404 / 409 / 422.
"""

from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse

from features.auth.dependencies import CurrentUser
from features.chat import service as chat_service
from features.chat.dependencies import LLMDep, limit_chat_messages
from features.planning import service as planning_service
from features.planning.answers import AnswerError, parse_answer
from features.planning.commands import removal
from features.planning.events import (
    ContinueRequest,
    ItemRemoval,
    PlanningEvent,
    PlanningMessage,
    PlanningText,
)
from features.planning.picks import PickError, picked_candidates
from features.planning.schemas import AskInput, ChoicePick, OfferChoice, PlanningInput, SlotSkip
from features.planning.turn import (
    Answer,
    Continue,
    Pick,
    Remove,
    Skip,
    Text,
    Trigger,
    TurnContext,
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
    action = snapshot.assessment.next_action
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
    showing choices that can be searched again."""
    if body is None or not body.refresh:
        return Continue()
    snapshot = await planning_service.snapshot(session, plan)
    action = snapshot.assessment.next_action
    if not isinstance(action, OfferChoice) or not action.can_refresh:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="There are no choices to look for other options for.",
        )
    return Continue(refresh=True)


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
    responses=error_responses(status.HTTP_409_CONFLICT, status.HTTP_422_UNPROCESSABLE_CONTENT),
)
async def submit_input(trigger: CheckedInput, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Answer (or skip) the pending form, or pick from the pending choices. No model call:
    the answer or pick is stored, the plan updated, and the planner moves on (running the
    next search, if any), streaming `PlanningEvent`s that end in `done`."""
    async for event in run_turn(turn, trigger):
        yield event


@router.post(
    "/chat",
    operation_id="sendPlanningMessage",
    response_class=EventSourceResponse,
    responses=error_responses(status.HTTP_429_TOO_MANY_REQUESTS),
    dependencies=[Depends(limit_chat_messages)],
)
async def send_message(body: PlanningText, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Free text. While a form is pending it is read as that form's answer, and while
    choices are pending as a pick (or answered as a question, or nudged back to the
    question); otherwise it is a question about the trip."""
    async for event in run_turn(turn, Text(content=body.content)):
        yield event


@router.post(
    "/removals",
    operation_id="removePlanItem",
    response_class=EventSourceResponse,
)
async def remove_item(trigger: CheckedRemoval, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Take an item off the trip, as a typed "remove …" would: no model call. The item goes
    with the rides to and from it, the planner says what the trip now needs and how to fix
    it, and then asks for it (going back to that stage if need be), streaming
    `PlanningEvent`s that end in `done`."""
    async for event in run_turn(turn, trigger):
        yield event


@router.post(
    "/continue",
    operation_id="continuePlanning",
    response_class=EventSourceResponse,
    responses=error_responses(status.HTTP_409_CONFLICT, status.HTTP_429_TOO_MANY_REQUESTS),
    dependencies=[Depends(limit_chat_messages)],
)
async def continue_planning(trigger: CheckedContinue, turn: Turn) -> AsyncIterator[PlanningEvent]:
    """Let the planner act with no new input: move on from a finished stage, run (or retry)
    a search, or show the pending form again. With `refresh` ("More options"), search the
    pending choices again for ones not offered yet."""
    async for event in run_turn(turn, trigger):
        yield event
