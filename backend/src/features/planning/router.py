"""The planning flow's routes, beside the chat flow's until it replaces them.

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
from features.planning.events import PlanningEvent, PlanningMessage, PlanningText
from features.planning.schemas import AskInput, PlanningInput, SlotSkip
from features.planning.turn import Answer, Continue, Skip, Text, Trigger, TurnContext, run_turn
from features.plans import service as plans_service
from features.plans.models import Plan
from shared.db import SessionDep
from shared.errors import error_responses

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


async def get_checked_input(body: PlanningInput, plan: OwnedPlan, session: SessionDep) -> Trigger:
    """The submitted form as a turn trigger: 409 unless its slot is the one being asked,
    422 when the value doesn't fit the slot (or a required slot is skipped)."""
    snapshot = await planning_service.snapshot(session, plan)
    action = snapshot.assessment.next_action
    if not isinstance(action, AskInput) or action.slot != body.slot:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That question isn't the one being asked any more. Reload to see the latest.",
        )
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


def _context(session: SessionDep, llm: LLMDep, user: CurrentUser, plan: Plan) -> TurnContext:
    return TurnContext(
        session=session, llm=llm, user_id=user.id, plan_id=plan.id, today=date.today()
    )


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
async def submit_input(
    trigger: CheckedInput, plan: OwnedPlan, user: CurrentUser, session: SessionDep, llm: LLMDep
) -> AsyncIterator[PlanningEvent]:
    """Answer (or skip) the pending form. No model call: the answer is stored, the plan
    updated, and the planner moves on, streaming `PlanningEvent`s that end in `done`."""
    async for event in run_turn(_context(session, llm, user, plan), trigger):
        yield event


@router.post(
    "/chat",
    operation_id="sendPlanningMessage",
    response_class=EventSourceResponse,
    responses=error_responses(status.HTTP_429_TOO_MANY_REQUESTS),
    dependencies=[Depends(limit_chat_messages)],
)
async def send_message(
    body: PlanningText, plan: OwnedPlan, user: CurrentUser, session: SessionDep, llm: LLMDep
) -> AsyncIterator[PlanningEvent]:
    """Free text. While a form is pending it is read as that form's answer (or answered as
    a question, or nudged back to the question); otherwise it is a question about the trip."""
    async for event in run_turn(_context(session, llm, user, plan), Text(content=body.content)):
        yield event


@router.post(
    "/continue",
    operation_id="continuePlanning",
    response_class=EventSourceResponse,
    responses=error_responses(status.HTTP_429_TOO_MANY_REQUESTS),
    dependencies=[Depends(limit_chat_messages)],
)
async def continue_planning(
    plan: OwnedPlan, user: CurrentUser, session: SessionDep, llm: LLMDep
) -> AsyncIterator[PlanningEvent]:
    """Let the planner act with no new input: move on from a finished stage, run (or retry)
    a search, or show the pending form again."""
    async for event in run_turn(_context(session, llm, user, plan), Continue()):
        yield event
