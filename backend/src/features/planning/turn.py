"""One planning turn: record what the user did, then let the engine drive until it needs them.

A turn starts from one trigger:

- `Answer` / `Skip`: a submitted form (already checked against the pending slot by the
  router). No model call: the answer is written to the plan as is.
- `Pick`: candidates picked from the open offer (checked by the router). Picking is the
  approval: their changes are validated and written (`planning.picks`).
- `Text`: free text from the composer. With an input pending, the extractor reads it as
  that slot's answer, and with choices pending as a pick ("the second one"). Either can
  say it is an edit instead ("remove the Friday dinner"): the command reader turns it into
  commands, which are checked against the plan's rules and written all or nothing
  (`planning.commands`); rules they break go back to the reader once, to repair. With
  nothing pending the command reader reads it first. Anything else gets the slot's fixed
  nudge, or a short answer when it is a question.
- `Continue`: no input; just drive (the "Find flights" / "Retry" / "Continue" button).
  With `refresh` ("More options"), the pending choices' search runs again first, leaving
  out everything it has already offered; the new offer replaces the old one.

Then `_drive` loops `{snapshot → assessment → act}`: it advances finished stages, goes back
to an earlier stage whose checklist has an issue again, runs searches, and stops at the
next form, the next choices, or the end of the trip. Every
stored message is streamed as a `PlanningMessageEvent`; the turn ends with `done` carrying
the plan and its assessment (or `error`).
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from pydantic import BaseModel, JsonValue
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import service as chat_service
from features.chat.schemas import MessageKind, MessageRole
from features.chat.tools import ToolContext
from features.planning import offers
from features.planning import service as planning_service
from features.planning.answers import (
    SKIPPED_TEXT,
    AnswerError,
    apply_answer,
    describe_answer,
    skip_slot,
)
from features.planning.commands import check_and_apply, describe_changes, numbered_items
from features.planning.engine import refresh_search
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
from features.planning.extractor import (
    CommandReading,
    ExtractionStatus,
    extract,
    extract_pick,
    read_commands,
)
from features.planning.picks import PickContext, PickError, apply_pick, describe_pick
from features.planning.prompts import command_messages, question_messages, repair_messages
from features.planning.schemas import (
    AdvanceStage,
    AnswerRecord,
    AskInput,
    Candidate,
    ChangesRecord,
    Complete,
    OfferChoice,
    PickRecord,
    ReturnToStage,
    RunSearch,
    StageRecord,
    TravelPlan,
)
from features.planning.searches import (
    NoMoreOptionsError,
    SearchContext,
    SearchFailedError,
    query_hash,
    run_search,
)
from features.planning.slots import SLOTS, SlotSpec
from features.plans import service as plans_service
from features.plans.schemas import PlanSnapshot, PlanStage, SlotName, StageChange
from features.plans.stages import STAGE_LABELS, StageError, apply_stage_change
from shared.flights.types import FlightProvider
from shared.hotels.types import HotelProvider
from shared.llm.types import LLMClient, LLMError, LLMMessage
from shared.places.types import PlaceProvider

logger = logging.getLogger(__name__)

# A safety stop for the drive loop. A whole trip needs far fewer steps per turn (a stage
# move, a search, then a form or choices), so hitting it means the engine is cycling.
MAX_DRIVE_STEPS = 12

GENERIC_FAILURE = "Something went wrong. Please try again."
# Stored as the user's message for "More options", so the transcript shows why the choices
# changed.
MORE_OPTIONS_TEXT = "Show me other options."
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
    flights: FlightProvider
    hotels: HotelProvider
    places: PlaceProvider


@dataclass(frozen=True)
class Answer:
    slot: SlotName
    answer: BaseModel  # already parsed with `answers.parse_answer`


@dataclass(frozen=True)
class Skip:
    slot: SlotName


@dataclass(frozen=True)
class Pick:
    action: OfferChoice  # the pending offer, as the router checked it
    candidates: list[Candidate]  # already checked with `picks.picked_candidates`


@dataclass(frozen=True)
class Text:
    content: str


@dataclass(frozen=True)
class Continue:
    refresh: bool = False  # checked by the router: only with refreshable choices pending


Trigger = Answer | Skip | Pick | Text | Continue


@dataclass
class _Outcome:
    """What free text led to: whether the engine should drive on afterwards."""

    drive: bool = False
    # A command moved the stage back. Going back to a finished stage leaves the engine
    # ready to advance again at once, so the drive stops there instead of undoing it.
    hold: bool = False


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
    kind: MessageKind = MessageKind.MESSAGE,
    payload: BaseModel | None = None,
) -> PlanningMessageEvent:
    row = await chat_service.add_message(
        ctx.session, ctx.plan_id, role, content, kind=kind, payload=payload
    )
    return PlanningMessageEvent(message=PlanningMessage.model_validate(row))


async def _last_payload(ctx: TurnContext) -> dict[str, JsonValue] | None:
    rows = await chat_service.recent_messages(ctx.session, ctx.plan_id, limit=1)
    return rows[0].payload if rows else None


async def _error(ctx: TurnContext, detail: str) -> PlanningErrorEvent:
    try:
        event = await _store(ctx, "system", detail, kind=MessageKind.ERROR)
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
        yield await _store(ctx, "assistant", f"That doesn't work: {exc}.", kind=MessageKind.NUDGE)
        return
    yield PlanningPlanEvent(plan=await _snapshot(ctx))


async def _form_answer(ctx: TurnContext, trigger: Answer) -> AsyncIterator[PlanningEvent]:
    record = AnswerRecord(slot=trigger.slot, value=trigger.answer.model_dump(mode="json"))
    text = describe_answer(trigger.answer)
    yield await _store(ctx, "user", text, kind=MessageKind.INPUT, payload=record)
    async for event in _write_answer(ctx, await _snapshot(ctx), trigger.answer):
        yield event


async def _skip(ctx: TurnContext, trigger: Skip) -> AsyncIterator[PlanningEvent]:
    record = AnswerRecord(slot=trigger.slot, skipped=True)
    yield await _store(ctx, "user", SKIPPED_TEXT, kind=MessageKind.INPUT, payload=record)
    await skip_slot(ctx.session, ctx.user_id, await _snapshot(ctx), trigger.slot)
    yield PlanningPlanEvent(plan=await _snapshot(ctx))


# ---------------------------------------------------------------------------
# Picks
# ---------------------------------------------------------------------------


async def _write_pick(
    ctx: TurnContext, plan: PlanSnapshot, action: OfferChoice, candidates: list[Candidate]
) -> AsyncIterator[PlanningEvent]:
    """Apply a pick; a change that doesn't fit becomes a note and nothing is written (the
    same choices are shown again)."""
    pick_ctx = PickContext(
        session=ctx.session,
        user_id=ctx.user_id,
        plan_id=ctx.plan_id,
        today=ctx.today,
    )
    try:
        await apply_pick(pick_ctx, plan, action, candidates)
    except PickError as exc:
        note = f"That one can't be added: {exc}. Pick another, or go back and change the trip."
        yield await _store(ctx, "assistant", note, kind=MessageKind.NUDGE)
        return
    yield PlanningPlanEvent(plan=await _snapshot(ctx))


async def _form_pick(ctx: TurnContext, trigger: Pick) -> AsyncIterator[PlanningEvent]:
    record = PickRecord(
        offer_id=trigger.action.offer_id,
        slot=trigger.action.slot,
        candidate_ids=[candidate.id for candidate in trigger.candidates],
        titles=[candidate.title for candidate in trigger.candidates],
    )
    content = describe_pick(trigger.candidates)
    yield await _store(ctx, "user", content, kind=MessageKind.PICK, payload=record)
    async for event in _write_pick(ctx, await _snapshot(ctx), trigger.action, trigger.candidates):
        yield event


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


# ---------------------------------------------------------------------------
# Edits: free text read as commands
# ---------------------------------------------------------------------------


async def _clarified(ctx: TurnContext) -> list[LLMMessage]:
    """The request and the clarification the latest text answers, when the planner's last
    message asked which change was meant ("Which dinner: Friday's or Saturday's?"); empty
    otherwise. Read together with the answer, they make one request."""
    # Oldest first: the request, the clarification, then the text this turn stored.
    rows = await chat_service.recent_messages(ctx.session, ctx.plan_id, limit=3)
    if len(rows) < 3:
        return []
    request, asked = rows[0], rows[1]
    if request.role != "user" or asked.kind != MessageKind.CHANGES or asked.payload is None:
        return []
    if ChangesRecord.model_validate(asked.payload).clarification is None:
        return []
    return [
        LLMMessage(role="user", content=request.content),
        LLMMessage(role="assistant", content=asked.content),
    ]


@dataclass(frozen=True)
class _Reading:
    """A command reading and the prompt it came from (a repair pass extends the prompt)."""

    reading: CommandReading
    messages: list[LLMMessage]

    @property
    def is_edit(self) -> bool:
        return self.reading.status in ("command", "ambiguous")


async def _read(
    ctx: TurnContext,
    plan: TravelPlan,
    content: str,
    pending: str | None,
    history: list[LLMMessage] | None = None,
) -> _Reading:
    items = numbered_items(plan)
    messages = command_messages(
        content, plan, items, ctx.today, pending=pending, history=history or []
    )
    return _Reading(await read_commands(ctx.llm, messages), messages)


async def _ask_which(ctx: TurnContext, clarification: str) -> PlanningMessageEvent:
    """Ask back which change was meant. Nothing is written; the reply is read with it."""
    record = ChangesRecord(clarification=clarification)
    return await _store(ctx, "assistant", clarification, kind=MessageKind.CHANGES, payload=record)


async def _edit(
    ctx: TurnContext, plan: TravelPlan, read: _Reading, outcome: _Outcome
) -> AsyncIterator[PlanningEvent]:
    """Check and write an edit reading's commands. When a rule turns them down, the reader
    gets the reasons and one more try; when that doesn't help, the reasons are stored and
    nothing is written. Either way the engine drives on afterwards, so the pending form or
    choices come back, or a stage the change broke is returned to."""
    reading = read.reading
    if reading.clarification is not None:
        yield await _ask_which(ctx, reading.clarification)
        return
    tool_ctx = ToolContext(
        session=ctx.session, user_id=ctx.user_id, plan_id=ctx.plan_id, today=ctx.today
    )
    record = await check_and_apply(tool_ctx, plan, reading.commands)
    if record.rejected and not record.applied and reading.reply is not None:
        # Nothing was written, so the repair is checked against the same plan.
        repair = await read_commands(
            ctx.llm, repair_messages(read.messages, reading.reply, record.rejected)
        )
        if repair.clarification is not None:
            yield await _ask_which(ctx, repair.clarification)
            return
        if repair.status == "command":
            record = await check_and_apply(tool_ctx, plan, repair.commands)
    content = describe_changes(record)
    yield await _store(ctx, "assistant", content, kind=MessageKind.CHANGES, payload=record)
    if record.applied:
        yield PlanningPlanEvent(plan=await _snapshot(ctx))
        outcome.hold = any(change.name == "change_stage" for change in record.applied)
    outcome.drive = True


async def _not_an_answer(
    ctx: TurnContext,
    plan: TravelPlan,
    content: str,
    action: AskInput | OfferChoice,
    status: ExtractionStatus,
    outcome: _Outcome,
) -> AsyncIterator[PlanningEvent]:
    """Text that doesn't answer the pending form or choices: an edit, a question, or
    anything else (the slot's nudge)."""
    spec = SLOTS[action.slot]
    if status == "command":
        read = await _read(ctx, plan, content, action.prompt)
        if read.is_edit:
            async for event in _edit(ctx, plan, read, outcome):
                yield event
            return
        status = "question" if read.reading.status == "question" else "unrelated"
    if status == "question":
        async for event in _answer_question(ctx, plan, content, spec):
            yield event
        outcome.drive = True  # asks again
    else:
        # The nudge already repeats the question, so the form isn't asked again.
        yield await _store(ctx, "assistant", spec.nudge, kind=MessageKind.NUDGE)


async def _free_text(
    ctx: TurnContext, content: str, outcome: _Outcome
) -> AsyncIterator[PlanningEvent]:
    plan = await _snapshot(ctx)
    action = plan.assessment.next_action
    pending = action.prompt if isinstance(action, AskInput | OfferChoice) else None

    history = await _clarified(ctx)
    if history:
        # The planner just asked which change was meant; this is most likely the answer.
        read = await _read(ctx, plan, content, pending, history)
        if read.is_edit:
            async for event in _edit(ctx, plan, read, outcome):
                yield event
            return
        # Not about the change after all: read it as any other text.

    if isinstance(action, AskInput):
        extraction = await extract(ctx.llm, action.slot, content, plan, ctx.today)
        if extraction.answer is not None:
            got = f"Got it: {describe_answer(extraction.answer)}."
            yield await _store(ctx, "assistant", got)
            async for event in _write_answer(ctx, plan, extraction.answer):
                yield event
            outcome.drive = True
        elif extraction.error is not None:
            note = f"That doesn't work: {extraction.error}."
            yield await _store(ctx, "assistant", note, kind=MessageKind.NUDGE)
            outcome.drive = True  # asks again
        else:
            async for event in _not_an_answer(
                ctx, plan, content, action, extraction.status, outcome
            ):
                yield event
        return

    if isinstance(action, OfferChoice):
        pick = await extract_pick(ctx.llm, action, content, plan)
        if pick.candidates is not None:
            got = f"Got it: {describe_pick(pick.candidates)}."
            yield await _store(ctx, "assistant", got)
            async for event in _write_pick(ctx, plan, action, pick.candidates):
                yield event
            outcome.drive = True
        elif pick.error is not None:
            note = f"That doesn't work: {pick.error}."
            yield await _store(ctx, "assistant", note, kind=MessageKind.NUDGE)
            outcome.drive = True  # shows the choices again
        else:
            async for event in _not_an_answer(ctx, plan, content, action, pick.status, outcome):
                yield event
        return

    # Nothing to answer (a search to run, a stage to continue, or the trip is done): an
    # edit, or else a question. Not driving after a question keeps it from setting off a
    # paid search; an edit drives, since the plan the search was for has changed.
    read = await _read(ctx, plan, content, None)
    if read.is_edit:
        async for event in _edit(ctx, plan, read, outcome):
            yield event
        return
    async for event in _answer_question(ctx, plan, content, None):
        yield event


# ---------------------------------------------------------------------------
# Driving the engine
# ---------------------------------------------------------------------------


async def _move(
    ctx: TurnContext, plan: TravelPlan, stage: PlanStage, flights_skipped: bool, text: str
) -> AsyncIterator[PlanningEvent]:
    """Put the plan at `stage` (already checked by `apply_stage_change`) with a divider."""
    await plans_service.set_stage(ctx.session, ctx.user_id, ctx.plan_id, stage, flights_skipped)
    record = StageRecord(from_stage=plan.stage, to_stage=stage)
    yield await _store(ctx, "system", text, kind=MessageKind.STAGE, payload=record)
    yield PlanningPlanEvent(plan=await _snapshot(ctx))


def _advance_text(plan: TravelPlan, stage: PlanStage) -> str:
    return f"{STAGE_LABELS[plan.stage].capitalize()} done. Next up: {STAGE_LABELS[stage]}."


def _return_text(action: ReturnToStage) -> str:
    return f"Back to {STAGE_LABELS[action.to]}: the trip still needs {action.reason}."


def _search_context(ctx: TurnContext, exclude: frozenset[str] = frozenset()) -> SearchContext:
    return SearchContext(
        session=ctx.session,
        today=ctx.today,
        flights=ctx.flights,
        hotels=ctx.hotels,
        places=ctx.places,
        exclude=exclude,
    )


async def _open(
    ctx: TurnContext, plan: TravelPlan, action: RunSearch, candidates: list[Candidate]
) -> None:
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


async def _search(
    ctx: TurnContext, plan: TravelPlan, action: RunSearch
) -> AsyncIterator[PlanningEvent]:
    """Run the search and open an offer with its candidates; on failure store why (the
    action stays a `RunSearch`, so the UI offers Retry)."""
    yield PlanningStatusEvent(text=f"{action.label}…")
    try:
        candidates = await run_search(_search_context(ctx), plan, action)
    except SearchFailedError as exc:
        yield await _store(ctx, "system", str(exc), kind=MessageKind.ERROR)
        return
    await _open(ctx, plan, action, candidates)


async def _more_options(ctx: TurnContext) -> AsyncIterator[PlanningEvent]:
    """Search again for the pending choices, leaving out every candidate already offered
    for that search, and replace the offer with what's new. When nothing new turns up (or
    the search fails) the current choices stay open, with a note saying why."""
    yield await _store(ctx, "user", MORE_OPTIONS_TEXT)
    plan = await _snapshot(ctx)
    action = refresh_search(plan)
    if action is None:  # the router checked; the plan moved on since
        return
    yield PlanningStatusEvent(text=f"{action.label}…")
    key = query_hash(plan, action)
    exclude = await offers.offered_titles(ctx.session, ctx.plan_id, key, plan.version)
    try:
        candidates = await run_search(_search_context(ctx, exclude), plan, action)
    except NoMoreOptionsError as exc:
        yield await _store(ctx, "assistant", str(exc), kind=MessageKind.NUDGE)
        return
    except SearchFailedError as exc:
        yield await _store(ctx, "system", str(exc), kind=MessageKind.ERROR)
        return
    await _open(ctx, plan, action, candidates)


async def _drive(ctx: TurnContext, *, hold: bool = False) -> AsyncIterator[PlanningEvent]:
    """Act on the engine's next action until it needs the user. With `hold` (the stage was
    just moved back on purpose), an advance as the first action is left for the user's
    Continue rather than taken at once."""
    searched = False
    for step in range(MAX_DRIVE_STEPS):
        plan = await _snapshot(ctx)
        action = plan.assessment.next_action
        match action:
            case AdvanceStage() if hold and step == 0:
                return
            case AdvanceStage():
                try:
                    # The single transition rule, as the Next button used to apply it.
                    stage, skipped = apply_stage_change(plan, StageChange(action="next"))
                except StageError:
                    # The engine only advances a ready stage, so this is a bug; stop here
                    # rather than loop.
                    logger.exception("Plan %s could not advance from %s", plan.id, plan.stage)
                    return
                async for event in _move(ctx, plan, stage, skipped, _advance_text(plan, stage)):
                    yield event
            case ReturnToStage():
                # An earlier stage's checklist has an issue again (a stay was removed, a
                # flight moved): go back the way the stepper does, and say why.
                change = StageChange(action="go_to", stage=action.to)
                try:
                    stage, skipped = apply_stage_change(plan, change)
                except StageError:
                    # The engine only returns to an earlier stage, so this is a bug too.
                    logger.exception("Plan %s could not return to %s", plan.id, action.to)
                    return
                async for event in _move(ctx, plan, stage, skipped, _return_text(action)):
                    yield event
            case AskInput() | OfferChoice():
                # Shown once: a retry or a reload must not repeat the same question.
                if await _last_payload(ctx) != action.model_dump(mode="json"):
                    kind = (
                        MessageKind.EXPECTED_INPUT
                        if isinstance(action, AskInput)
                        else MessageKind.CHOICES
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
        drive, hold = True, False
        match trigger:
            case Answer():
                async for event in _form_answer(ctx, trigger):
                    yield event
            case Skip():
                async for event in _skip(ctx, trigger):
                    yield event
            case Pick():
                async for event in _form_pick(ctx, trigger):
                    yield event
            case Text():
                yield await _store(ctx, "user", trigger.content)
                outcome = _Outcome()
                async for event in _free_text(ctx, trigger.content, outcome):
                    yield event
                drive, hold = outcome.drive, outcome.hold
            case Continue(refresh=True):
                async for event in _more_options(ctx):
                    yield event
            case Continue():
                pass
        if drive:
            async for event in _drive(ctx, hold=hold):
                yield event
        yield PlanningDoneEvent(plan=await _snapshot(ctx))

    except LLMError as exc:
        yield await _error(ctx, str(exc))
    except Exception:
        logger.exception("Planning turn failed for plan %s", ctx.plan_id)
        await ctx.session.rollback()
        yield await _error(ctx, GENERIC_FAILURE)
