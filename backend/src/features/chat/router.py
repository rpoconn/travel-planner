from collections.abc import AsyncIterator
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.sse import EventSourceResponse

from features.auth.dependencies import CurrentUser
from features.chat import pipeline, service
from features.chat.dependencies import LLMDep
from features.chat.schemas import ChatEvent, ChatMessage, SendMessage
from features.plans import service as plans_service
from features.plans.models import Plan
from shared.db import SessionDep
from shared.errors import error_responses

router = APIRouter(
    prefix="/plans/{plan_id}",
    tags=["chat"],
    responses=error_responses(status.HTTP_401_UNAUTHORIZED, status.HTTP_404_NOT_FOUND),
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")


async def get_owned_plan(plan_id: UUID, user: CurrentUser, session: SessionDep) -> Plan:
    # Runs before the response starts, so a missing plan is a normal 404 rather than an
    # error inside an already-open event stream.
    try:
        return await plans_service.get_plan(session, user.id, plan_id)
    except plans_service.PlanNotFoundError as exc:
        raise _not_found() from exc


OwnedPlan = Annotated[Plan, Depends(get_owned_plan)]


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/messages", operation_id="listMessages")
async def list_messages(plan_id: UUID, user: CurrentUser, session: SessionDep) -> list[ChatMessage]:
    """The plan's conversation, oldest first."""
    try:
        messages = await service.list_messages(session, user.id, plan_id)
    except plans_service.PlanNotFoundError as exc:
        raise _not_found() from exc
    return [ChatMessage.model_validate(message) for message in messages]


@router.post("/chat", operation_id="sendMessage", response_class=EventSourceResponse)
async def send_message(
    body: SendMessage,
    plan: OwnedPlan,
    user: CurrentUser,
    session: SessionDep,
    llm: LLMDep,
) -> AsyncIterator[ChatEvent]:
    """Send a message and stream the assistant's answer as server-sent events.

    Each event's `data` is a JSON `ChatEvent` discriminated by `type`: `guardrail` first,
    then `token` / `tool_call` / `tool_result` / `plan_updated` as the answer is produced,
    and finally `done` (with the stored reply) or `error`.
    """
    async for event in pipeline.answer_message(
        session=session,
        llm=llm,
        user_id=user.id,
        plan=plan,
        content=body.content,
        today=date.today(),
    ):
        yield event
