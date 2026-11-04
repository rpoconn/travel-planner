"""One planning turn: record what the user did, then let the engine drive until it needs them.

A turn starts from one trigger:

- `Answer` / `Skip`: a submitted form (already checked against the pending slot by the
  router). No model call: the answer is written to the plan as is.
- `Text`: free text from the composer. With an input pending, the extractor reads it as
  that slot's answer; anything else gets the slot's fixed nudge, or a short answer when it
  is a question. With nothing pending it is a question about the trip.
- `Continue`: no input; just drive (the "Find flights" / "Retry" / "Continue" button).

Then `_drive` loops `{snapshot → assessment → act}`: it advances finished stages, runs
searches, and stops at the next form, the next choices, or the end of the trip. Every
stored message is streamed as a `PlanningMessageEvent`; the turn ends with `done` carrying
the plan and its assessment (or `error`).
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import service as chat_service
from features.chat.schemas import MessageKind, MessageRole
from features.planning import offers
from features.planning import service as planning_service
from features.planning.answers import (
    SKIPPED_TEXT,
    AnswerError,
    apply_answer,
    describe_answer,
    skip_slot,
)
from features.planning.events import (
    PlanningDoneEvent,
    PlanningErrorEvent,
    PlanningEvent,
    PlanningMessage,
    PlanningMessageEvent,
    PlanningPlanEvent,
    PlanningStatusEvent,
    PlanningTokenEvent,
)
from features.planning.extractor import extract
from features.planning.prompts import question_messages
from features.planning.schemas import (
    AdvanceStage,
    AnswerRecord,
    AskInput,
    Complete,
    OfferChoice,
    RunSearch,
    StageRecord,
    TravelPlan,
)
from features.planning.searches import SearchContext, SearchFailedError, query_hash, run_search
from features.planning.slots import SLOTS, SlotSpec
from features.plans import service as plans_service
from features.plans.schemas import PlanSnapshot, PlanStage, SlotName, StageChange
from features.plans.stages import STAGE_LABELS, StageError, apply_stage_change
from shared.llm.types import LLMClient, LLMError

logger = logging.getLogger(__name__)

# A safety stop for the drive loop. A whole trip needs far fewer steps per turn (a stage
# move, a search, then a form or choices), so hitting it means the engine is cycling.
MAX_DRIVE_STEPS = 12

GENERIC_FAILURE = "Something went wrong. Please try again."
EMPTY_ANSWER = "Sorry, I don't have an answer for that."


# ---------------------------------------------------------------------------
# Context and triggers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnContext:
    session: AsyncSession
    llm: LLMClient
    user_id: UUID
    plan_id: UUID
    today: date


@dataclass(frozen=True)
class Answer:
    slot: SlotName
    answer: BaseModel  # already parsed with `answers.parse_answer`


@dataclass(frozen=True)
class Skip:
    slot: SlotName


@dataclass(frozen=True)
class Text:
    content: str


@dataclass(frozen=True)
class Continue:
    pass


Trigger = Answer | Skip | Text | Continue


@dataclass
class _Outcome:
    """What free text led to: whether the engine should drive on afterwards."""

    drive: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _snapshot(ctx: TurnContext) -> TravelPlan:
    return await planning_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)


async def _store(
    ctx: TurnContext,
    role: MessageRole,
    content: str,
    *,
    kind: MessageKind = "message",
    payload: BaseModel | None = None,
) -> PlanningMessageEvent:
    row = await chat_service.add_message(
        ctx.session, ctx.plan_id, role, content, kind=kind, payload=payload
    )
    return PlanningMessageEvent(message=PlanningMessage.model_validate(row))


async def _last_payload(ctx: TurnContext) -> dict[str, Any] | None:
    rows = await chat_service.recent_messages(ctx.session, ctx.plan_id, limit=1)
    return rows[0].payload if rows else None


async def _error(ctx: TurnContext, detail: str) -> PlanningErrorEvent:
    try:
        event = await _store(ctx, "system", detail, kind="error")
    except Exception:
        logger.exception("Could not store the error notice for plan %s", ctx.plan_id)
        return PlanningErrorEvent(detail=detail)
    return PlanningErrorEvent(detail=detail, message=event.message)


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


async def _write_answer(
    ctx: TurnContext, plan: PlanSnapshot, answer: BaseModel
) -> AsyncIterator[PlanningEvent]:
    """Apply an answer; a rule it breaks against the plan becomes a note (the engine then
    asks again)."""
    try:
        await apply_answer(ctx.session, ctx.user_id, plan, answer)
    except AnswerError as exc:
        yield await _store(ctx, "assistant", f"That doesn't work: {exc}.", kind="nudge")
        return
    yield PlanningPlanEvent(plan=await _snapshot(ctx))


async def _form_answer(ctx: TurnContext, trigger: Answer) -> AsyncIterator[PlanningEvent]:
    record = AnswerRecord(slot=trigger.slot, value=trigger.answer.model_dump(mode="json"))
    yield await _store(ctx, "user", describe_answer(trigger.answer), kind="input", payload=record)
    async for event in _write_answer(ctx, await _snapshot(ctx), trigger.answer):
        yield event


async def _skip(ctx: TurnContext, trigger: Skip) -> AsyncIterator[PlanningEvent]:
    record = AnswerRecord(slot=trigger.slot, skipped=True)
    yield await _store(ctx, "user", SKIPPED_TEXT, kind="input", payload=record)
    await skip_slot(ctx.session, ctx.user_id, await _snapshot(ctx), trigger.slot)
    yield PlanningPlanEvent(plan=await _snapshot(ctx))


# ---------------------------------------------------------------------------
# Free text
# ---------------------------------------------------------------------------


async def _answer_question(
    ctx: TurnContext, plan: PlanSnapshot, content: str, pending: SlotSpec | None
) -> AsyncIterator[PlanningEvent]:
    """A short streamed answer. The text is shown and stored, never parsed."""
    reply = ""
    async for chunk in ctx.llm.stream(question_messages(plan, content, pending)):
        if chunk.content:
            reply += chunk.content
            yield PlanningTokenEvent(text=chunk.content)
    yield await _store(ctx, "assistant", reply.strip() or EMPTY_ANSWER)


async def _free_text(
    ctx: TurnContext, content: str, outcome: _Outcome
) -> AsyncIterator[PlanningEvent]:
    plan = await _snapshot(ctx)
    action = plan.assessment.next_action

    if isinstance(action, AskInput):
        spec = SLOTS[action.slot]
        extraction = await extract(ctx.llm, action.slot, content, plan, ctx.today)
        if extraction.answer is not None:
            got = f"Got it: {describe_answer(extraction.answer)}."
            yield await _store(ctx, "assistant", got)
            async for event in _write_answer(ctx, plan, extraction.answer):
                yield event
            outcome.drive = True
        elif extraction.error is not None:
            note = f"That doesn't work: {extraction.error}."
            yield await _store(ctx, "assistant", note, kind="nudge")
            outcome.drive = True  # asks again
        elif extraction.status == "question":
            async for event in _answer_question(ctx, plan, content, spec):
                yield event
            outcome.drive = True  # asks again
        else:
            # The nudge already repeats the question, so the form isn't asked again.
            yield await _store(ctx, "assistant", spec.nudge, kind="nudge")
        return

    if isinstance(action, OfferChoice):
        # Reading a pick from free text ("the second one") arrives with the pick path.
        yield await _store(ctx, "assistant", SLOTS[action.slot].nudge, kind="nudge")
        return

    # Nothing to answer (a search to run, a stage to continue, or the trip is done). Not
    # driving afterwards keeps a question from setting off a paid search.
    async for event in _answer_question(ctx, plan, content, None):
        yield event


# ---------------------------------------------------------------------------
# Driving the engine
# ---------------------------------------------------------------------------


async def _advance(
    ctx: TurnContext, plan: TravelPlan, stage: PlanStage, flights_skipped: bool
) -> AsyncIterator[PlanningEvent]:
    await plans_service.set_stage(ctx.session, ctx.user_id, ctx.plan_id, stage, flights_skipped)
    text = f"{STAGE_LABELS[plan.stage].capitalize()} done. Next up: {STAGE_LABELS[stage]}."
    record = StageRecord(from_stage=plan.stage, to_stage=stage)
    yield await _store(ctx, "system", text, kind="stage", payload=record)
    yield PlanningPlanEvent(plan=await _snapshot(ctx))


async def _search(
    ctx: TurnContext, plan: TravelPlan, action: RunSearch
) -> AsyncIterator[PlanningEvent]:
    """Run the search and open an offer with its candidates; on failure store why (the
    action stays a `RunSearch`, so the UI offers Retry)."""
    yield PlanningStatusEvent(text=f"{action.label}…")
    try:
        candidates = await run_search(SearchContext(ctx.session, ctx.today), plan, action)
    except SearchFailedError as exc:
        yield await _store(ctx, "system", str(exc), kind="error")
        return
    await offers.open_offer(
        ctx.session,
        plan_id=ctx.plan_id,
        slot=action.slot,
        stage=action.stage,
        plan_version=plan.version,
        candidates=candidates,
        query_hash=query_hash(plan, action),
        gap=action.gap,
        nights=action.nights,
    )


async def _drive(ctx: TurnContext) -> AsyncIterator[PlanningEvent]:
    searched = False
    for _ in range(MAX_DRIVE_STEPS):
        plan = await _snapshot(ctx)
        action = plan.assessment.next_action
        match action:
            case AdvanceStage():
                try:
                    # The single transition rule, as the Next button used to apply it.
                    stage, skipped = apply_stage_change(plan, StageChange(action="next"))
                except StageError:
                    # The engine only advances a ready stage, so this is a bug; stop here
                    # rather than loop.
                    logger.exception("Plan %s could not advance from %s", plan.id, plan.stage)
                    return
                async for event in _advance(ctx, plan, stage, skipped):
                    yield event
            case AskInput() | OfferChoice():
                # Shown once: a retry or a reload must not repeat the same question.
                if await _last_payload(ctx) != action.model_dump(mode="json"):
                    kind: MessageKind = (
                        "expected_input" if isinstance(action, AskInput) else "choices"
                    )
                    yield await _store(ctx, "assistant", action.prompt, kind=kind, payload=action)
                return
            case RunSearch():
                if searched:
                    return  # it failed; the user retries with Continue
                searched = True
                async for event in _search(ctx, plan, action):
                    yield event
            case Complete():
                return
    logger.error(
        "Plan %s: the engine was still acting after %d steps", ctx.plan_id, MAX_DRIVE_STEPS
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_turn(ctx: TurnContext, trigger: Trigger) -> AsyncIterator[PlanningEvent]:
    """Handle one trigger for a plan whose ownership the caller has checked."""
    try:
        drive = True
        match trigger:
            case Answer():
                async for event in _form_answer(ctx, trigger):
                    yield event
            case Skip():
                async for event in _skip(ctx, trigger):
                    yield event
            case Text():
                yield await _store(ctx, "user", trigger.content)
                outcome = _Outcome()
                async for event in _free_text(ctx, trigger.content, outcome):
                    yield event
                drive = outcome.drive
            case Continue():
                pass
        if drive:
            async for event in _drive(ctx):
                yield event
        yield PlanningDoneEvent(plan=await _snapshot(ctx))

    except LLMError as exc:
        yield await _error(ctx, str(exc))
    except Exception:
        logger.exception("Planning turn failed for plan %s", ctx.plan_id)
        await ctx.session.rollback()
        yield await _error(ctx, GENERIC_FAILURE)
