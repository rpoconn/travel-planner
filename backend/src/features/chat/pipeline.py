"""One chat turn, end to end.

- `answer_message`: store the user's message, run the guardrail, then either redirect
  (off-topic) or run the planning agent, and store the assistant's reply together with the
  proposal of any plan changes it staged.
- `decide_proposal`: the user answered a proposal card. Approve applies its changes and
  lets the agent confirm; deny stores a canned reply; revise ("Other…") hands the user's
  text to the agent, which proposes again.

Everything the client needs is yielded as `ChatEvent`s; the router only encodes them as SSE.
"""

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import agent, guardrail, service, tools
from features.chat.models import Message, Proposal
from features.chat.prompts import (
    FIX_GUARDRAIL_TOPIC,
    approval_applied_message,
    approval_failed_message,
    fix_request_message,
    proposal_note,
    revise_request_message,
    strip_proposal_notes,
)
from features.chat.schemas import (
    ChatEvent,
    ChatMessage,
    DoneEvent,
    ErrorEvent,
    GuardrailEvent,
    MessageKind,
    PlanUpdatedEvent,
    ProposalDecision,
    ProposalStatus,
    ProposedChange,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolTrace,
)
from features.chat.tools import ToolContext
from features.itinerary import service as itinerary_service
from features.itinerary.schemas import TimelineWarning
from features.plans.models import Plan
from features.plans.schemas import TravelPlan
from shared.llm.types import LLMClient, LLMError, LLMMessage
from shared.search.types import SearchProvider

logger = logging.getLogger(__name__)

# Earlier messages given to the agent / the guardrail as context.
AGENT_HISTORY_MESSAGES = 12
GUARDRAIL_HISTORY_MESSAGES = 6

GENERIC_FAILURE = "Something went wrong while answering. Please try again."
EMPTY_REPLY_AFTER_TOOLS = "I've updated your itinerary."
EMPTY_REPLY_AFTER_PROPOSAL = "Here's what I'd change — approve the card to apply it."
EMPTY_REPLY = "Sorry, I don't have an answer for that. Could you rephrase?"

# Stored (visible) user messages for the card's buttons, and the reply to "Deny".
APPROVED_TEXT = "Approved the proposed changes."
DENIED_TEXT = "Denied the proposed changes."
DENIED_REPLY = "No problem — nothing was changed. Tell me what you'd like instead."
# A decision is a reply to the assistant's own card, so the topic classifier is skipped.
DECISION_GUARDRAIL_TOPIC = "proposal decision"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _history_content(row: Message) -> str:
    # A reply that staged changes carries them (and what the user decided) as a note. Notes
    # the model wrote itself are dropped first, also from rows stored before they were
    # stripped on the way in, so the agent doesn't take them for real cards.
    content = strip_proposal_notes(row.content) if row.role == "assistant" else row.content
    if row.proposal is None:
        return content
    summaries = [change["summary"] for change in row.proposal.changes]
    return f"{content}\n\n{proposal_note(row.proposal.status, summaries)}"


def _llm_history(rows: Sequence[Message]) -> list[LLMMessage]:
    # Off-topic exchanges and error notices are left out so they don't steer the agent.
    return [
        LLMMessage(
            role="user" if row.role == "user" else "assistant", content=_history_content(row)
        )
        for row in rows
        if row.kind in ("message", "decision") and row.role in ("user", "assistant")
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
                    proposed=event.proposed,
                )
            )

    def applied_changes(self) -> bool:
        return any(
            trace.ok and not trace.proposed and tools.changes_plan(trace.name)
            for trace in self.traces
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


async def _store_reply(
    session: AsyncSession,
    plan_id: UUID,
    reply: _Reply,
    *,
    kind: MessageKind = "message",
    staged: Sequence[ProposedChange] = (),
    plan_version: int = 0,
) -> AsyncIterator[ChatEvent]:
    """Store the assistant's reply (and its proposal), ending with `done`."""
    # A note copied from the history looks like a card but isn't one; the client shows the
    # stored text once the turn ends, so the streamed copy goes away too.
    reply.text = strip_proposal_notes(reply.text)
    if not reply.text:
        # Small models sometimes stop after tool calls without saying anything.
        if staged:
            fallback = EMPTY_REPLY_AFTER_PROPOSAL
        elif reply.applied_changes():
            fallback = EMPTY_REPLY_AFTER_TOOLS
        else:
            fallback = EMPTY_REPLY
        reply.record(TokenEvent(text=fallback))
        yield TokenEvent(text=fallback)

    stored = await service.add_message(
        session, plan_id, "assistant", reply.text, kind=kind, tool_calls=reply.traces
    )
    if staged:
        await service.add_proposal(session, stored, staged, plan_version)
    yield DoneEvent(message=ChatMessage.model_validate(stored))


async def _agent_turn(
    *,
    session: AsyncSession,
    llm: LLMClient,
    ctx: ToolContext,
    plan: Plan,
    history: list[LLMMessage],
    latest: str,
    reply: _Reply,
) -> AsyncIterator[ChatEvent]:
    """Run the agent on `latest` and store its reply; the changes it stages become the
    reply's proposal."""
    snapshot = await itinerary_service.snapshot(session, plan)
    staged: list[ProposedChange] = []
    async for event in agent.run_agent(
        llm=llm,
        ctx=ctx,
        plan=snapshot,
        history=history,
        latest=latest,
        today=ctx.today,
        staged=staged,
    ):
        reply.record(event)
        yield event
    async for event in _store_reply(
        session, ctx.plan_id, reply, staged=staged, plan_version=snapshot.version
    ):
        yield event


# ---------------------------------------------------------------------------
# Turn
# ---------------------------------------------------------------------------


async def answer_message(
    *,
    session: AsyncSession,
    llm: LLMClient,
    search: SearchProvider,
    user_id: UUID,
    plan: Plan,
    content: str,
    today: date,
    fix: TimelineWarning | None = None,
) -> AsyncIterator[ChatEvent]:
    """Handle one user message for a plan the caller has already checked ownership of.

    With `fix` (the itinerary's "Fix it" button), the agent is asked to propose a fix for
    that warning, which the user then approves like any other proposal."""
    # Read once: after a rollback the ORM object is expired, and reloading `plan.id`
    # lazily would need sync IO (MissingGreenlet) right when we're reporting an error.
    plan_id = plan.id
    history = _llm_history(
        await service.recent_messages(session, plan_id, limit=AGENT_HISTORY_MESSAGES)
    )
    user_message = await service.add_message(session, plan_id, "user", content)
    ctx = ToolContext(session=session, user_id=user_id, plan_id=plan_id, search=search, today=today)
    reply = _Reply()

    try:
        latest = content
        if fix is None:
            decision = await guardrail.check_topic(
                llm, history[-GUARDRAIL_HISTORY_MESSAGES:], content
            )
            allowed, topic = decision.allowed, decision.topic
        else:
            # A server-composed request is on topic by construction, and a keyword
            # classifier could still misjudge "Fix: Hotel Okura overlaps Senso-ji Temple".
            allowed, topic = True, FIX_GUARDRAIL_TOPIC
            latest = fix_request_message(
                content, fix, await itinerary_service.snapshot(session, plan)
            )
        yield GuardrailEvent(on_topic=allowed, topic=topic)

        events: AsyncIterator[ChatEvent]
        if allowed:
            events = _agent_turn(
                session=session,
                llm=llm,
                ctx=ctx,
                plan=plan,
                history=history,
                latest=latest,
                reply=reply,
            )
        else:
            # Marks the exchange so it's left out of the agent's future context.
            await service.set_kind(session, user_message, "redirect")
            events = _redirect(session, llm, plan, content, reply)
        async for event in events:
            yield event

    except LLMError as exc:
        yield await _error_event(session, plan_id, str(exc))
    except Exception:
        logger.exception("Chat turn failed for plan %s", plan_id)
        await session.rollback()
        yield await _error_event(session, plan_id, GENERIC_FAILURE)


async def _redirect(
    session: AsyncSession, llm: LLMClient, plan: Plan, content: str, reply: _Reply
) -> AsyncIterator[ChatEvent]:
    snapshot = await itinerary_service.snapshot(session, plan)
    async for event in _redirect_events(llm, snapshot, content):
        reply.record(event)
        yield event
    async for event in _store_reply(session, plan.id, reply, kind="redirect"):
        yield event


# ---------------------------------------------------------------------------
# Decision on a proposal card
# ---------------------------------------------------------------------------


async def _apply_proposal(
    ctx: ToolContext,
    changes: Sequence[ProposedChange],
    reply: _Reply,
    applied: list[str],
    failures: list[str],
) -> AsyncIterator[ChatEvent]:
    """Apply `changes` all-or-nothing as far as possible: every change is validated against
    the current plan first, and nothing is written if any fails. Yields the tool events of
    the writes, then one `plan_updated`. The tool results of what was applied and the
    reasons for failures are appended to the out-parameters `applied` and `failures`."""
    for index, change in enumerate(changes):
        # Checked like it was staged: with the card's earlier changes counted, so a swap
        # (remove the old flight, add the new one) or two hotels sharing the budget is
        # judged as the user saw it, not against the plan alone.
        outcome = await tools.check_change(replace(ctx, staged=changes[:index]), change)
        if not outcome.ok:
            failures.append(f"{change.summary}: {outcome.summary}")
    if not failures:
        for change in changes:
            call_id = agent.new_call_id()
            call = ToolCallEvent(call_id=call_id, name=change.name, arguments=change.arguments)
            reply.record(call)
            yield call
            # Writes commit one by one, so a later failure (say, "update X" after "remove
            # X" in the same proposal) leaves the earlier ones applied; the agent is told.
            outcome = await tools.apply_change(ctx, change)
            result = ToolResultEvent(
                call_id=call_id, name=change.name, ok=outcome.ok, summary=outcome.summary
            )
            reply.record(result)
            yield result
            if not outcome.ok:
                failures.append(f"{change.summary}: {outcome.summary}")
                break
            applied.append(outcome.content)
        if applied:
            plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
            yield PlanUpdatedEvent(plan=plan)


async def decide_proposal(
    *,
    session: AsyncSession,
    llm: LLMClient,
    search: SearchProvider,
    user_id: UUID,
    plan: Plan,
    proposal: Proposal,
    decision: ProposalDecision,
    today: date,
) -> AsyncIterator[ChatEvent]:
    """Handle the user's answer to a pending proposal in a plan they own (both checked by
    the caller)."""
    plan_id = plan.id  # read once, as in `answer_message`
    changes = [ProposedChange.model_validate(change) for change in proposal.changes]
    # Rendered later (`_llm_history`), so the proposal shows its new status.
    rows = await service.recent_messages(session, plan_id, limit=AGENT_HISTORY_MESSAGES)
    ctx = ToolContext(session=session, user_id=user_id, plan_id=plan_id, search=search, today=today)
    reply = _Reply()

    try:
        yield GuardrailEvent(on_topic=True, topic=DECISION_GUARDRAIL_TOPIC)

        if decision.action == "deny":
            await service.add_message(session, plan_id, "user", DENIED_TEXT, kind="decision")
            await service.set_proposal_status(session, proposal, "denied")
            reply.record(TokenEvent(text=DENIED_REPLY))
            yield TokenEvent(text=DENIED_REPLY)
            async for event in _store_reply(session, plan_id, reply):
                yield event
            return

        if decision.action == "revise":
            assert decision.message is not None  # ProposalDecision requires it
            await service.add_message(session, plan_id, "user", decision.message, kind="decision")
            await service.set_proposal_status(session, proposal, "revised")
            latest = revise_request_message(decision.message, changes)
        else:
            await service.add_message(session, plan_id, "user", APPROVED_TEXT, kind="decision")
            applied: list[str] = []
            failures: list[str] = []
            async for event in _apply_proposal(ctx, changes, reply, applied, failures):
                yield event
            status: ProposalStatus = "failed" if failures else "approved"
            await service.set_proposal_status(session, proposal, status)
            latest = (
                approval_failed_message(applied, failures)
                if failures
                else approval_applied_message(applied)
            )

        async for event in _agent_turn(
            session=session,
            llm=llm,
            ctx=ctx,
            plan=plan,
            history=_llm_history(rows),
            latest=latest,
            reply=reply,
        ):
            yield event

    except LLMError as exc:
        yield await _error_event(session, plan_id, str(exc))
    except Exception:
        logger.exception("Proposal decision failed for plan %s", plan_id)
        await session.rollback()
        yield await _error_event(session, plan_id, GENERIC_FAILURE)
