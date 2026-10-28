"""One chat turn, end to end: store the user's message, run the guardrail, then either
redirect (off-topic) or run the planning agent, and store the assistant's reply.

Everything the client needs is yielded as `ChatEvent`s; the router only encodes them as SSE.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import agent, guardrail, service
from features.chat.models import Message
from features.chat.schemas import (
    ChatEvent,
    ChatMessage,
    DoneEvent,
    ErrorEvent,
    GuardrailEvent,
    MessageKind,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolTrace,
)
from features.chat.tools import ToolContext
from features.itinerary import service as itinerary_service
from features.plans.models import Plan
from features.plans.schemas import TravelPlan
from shared.llm.types import LLMClient, LLMError, LLMMessage

logger = logging.getLogger(__name__)

# Earlier messages given to the agent / the guardrail as context.
AGENT_HISTORY_MESSAGES = 12
GUARDRAIL_HISTORY_MESSAGES = 6

GENERIC_FAILURE = "Something went wrong while answering. Please try again."
EMPTY_REPLY_AFTER_TOOLS = "I've updated your itinerary."
EMPTY_REPLY = "Sorry, I don't have an answer for that. Could you rephrase?"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _llm_history(rows: Sequence[Message]) -> list[LLMMessage]:
    # Off-topic exchanges and error notices are left out so they don't steer the agent.
    return [
        LLMMessage(role="user" if row.role == "user" else "assistant", content=row.content)
        for row in rows
        if row.kind == "message" and row.role in ("user", "assistant")
    ]


@dataclass
class _Reply:
    """Accumulates the assistant message from the events streamed to the client."""

    text: str = ""
    traces: list[ToolTrace] = field(default_factory=list)
    _calls: dict[str, ToolCallEvent] = field(default_factory=dict)

    def record(self, event: ChatEvent) -> None:
        if isinstance(event, TokenEvent):
            self.text += event.text
        elif isinstance(event, ToolCallEvent):
            self._calls[event.call_id] = event
        elif isinstance(event, ToolResultEvent):
            call = self._calls.pop(event.call_id)
            self.traces.append(
                ToolTrace(
                    call_id=event.call_id,
                    name=event.name,
                    arguments=call.arguments,
                    ok=event.ok,
                    summary=event.summary,
                )
            )


async def _redirect_events(
    llm: LLMClient, plan: TravelPlan, latest: str
) -> AsyncIterator[ChatEvent]:
    async for text in guardrail.stream_redirect(llm, plan, latest):
        yield TokenEvent(text=text)


async def _error_event(session: AsyncSession, plan_id: UUID, detail: str) -> ErrorEvent:
    try:
        notice = await service.add_message(session, plan_id, "system", detail, kind="error")
    except Exception:
        logger.exception("Could not store the error notice for plan %s", plan_id)
        return ErrorEvent(detail=detail)
    return ErrorEvent(detail=detail, message=ChatMessage.model_validate(notice))


# ---------------------------------------------------------------------------
# Turn
# ---------------------------------------------------------------------------


async def answer_message(
    *,
    session: AsyncSession,
    llm: LLMClient,
    user_id: UUID,
    plan: Plan,
    content: str,
    today: date,
) -> AsyncIterator[ChatEvent]:
    """Handle one user message for a plan the caller has already checked ownership of."""
    # Read once: after a rollback the ORM object is expired, and reloading `plan.id`
    # lazily would need sync IO (MissingGreenlet) right when we're reporting an error.
    plan_id = plan.id
    history = _llm_history(
        await service.recent_messages(session, plan_id, limit=AGENT_HISTORY_MESSAGES)
    )
    user_message = await service.add_message(session, plan_id, "user", content)
    reply = _Reply()

    try:
        snapshot = await itinerary_service.snapshot(session, plan)
        decision = await guardrail.check_topic(llm, history[-GUARDRAIL_HISTORY_MESSAGES:], content)
        yield GuardrailEvent(on_topic=decision.allowed, topic=decision.topic)

        kind: MessageKind
        events: AsyncIterator[ChatEvent]
        if decision.allowed:
            kind = "message"
            events = agent.run_agent(
                llm=llm,
                ctx=ToolContext(session=session, user_id=user_id, plan_id=plan_id),
                plan=snapshot,
                history=history,
                latest=content,
                today=today,
            )
        else:
            kind = "redirect"
            # Marks the exchange so it's left out of the agent's future context.
            await service.set_kind(session, user_message, "redirect")
            events = _redirect_events(llm, snapshot, content)

        async for event in events:
            reply.record(event)
            yield event

        if not reply.text.strip():
            # Small models sometimes stop after tool calls without saying anything.
            fallback = EMPTY_REPLY_AFTER_TOOLS if reply.traces else EMPTY_REPLY
            reply.record(TokenEvent(text=fallback))
            yield TokenEvent(text=fallback)

        stored = await service.add_message(
            session, plan_id, "assistant", reply.text, kind=kind, tool_calls=reply.traces
        )
        yield DoneEvent(message=ChatMessage.model_validate(stored))

    except LLMError as exc:
        yield await _error_event(session, plan_id, str(exc))
    except Exception:
        logger.exception("Chat turn failed for plan %s", plan_id)
        await session.rollback()
        yield await _error_event(session, plan_id, GENERIC_FAILURE)
