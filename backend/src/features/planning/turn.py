"""One planning turn: write what the user did, then let the planner decide what is next.

A turn starts from one trigger. What the user did through the interface is written at
once, with no model call, because doing it was the approval:

- `Answer` / `Skip`: a submitted form (checked against the pending form by the router).
- `Pick`: candidates picked from the open choices (checked by the router); their changes
  are validated and written (`planning.picks`).
- `Remove`: an item's Remove button, as the `remove_item` command a typed "remove the
  hotel" becomes (`planning.commands`): the item goes with the rides to and from it.
- `Decide`: Approve or Reject on a suggestion card (`planning.proposals`). Approving checks
  the suggested changes again and writes them all or nothing.
- `Text`: free text from the composer. Nothing is written here: the planner reads it, and
  writes only what it asks for in so many words (`agent.tools`).
- `Continue`: no input. With `problem_id` ("Fix this") the planner works on that problem
  first. With `refresh` ("More options") the open choices' search runs again, leaving out
  everything it already offered, and the new choices replace the old; the planner isn't
  asked, since the user is already choosing.

Then the planner's loop runs (`agent.loop`): the model looks at the plan and its problems
and ends the turn on a question, choices, a suggestion or a short answer. Every stored
message is streamed as a `PlanningMessageEvent`; the turn ends with `done` carrying the
plan (or `error`).
"""

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

from pydantic import BaseModel

from features.chat.schemas import MessageKind
from features.planning import offers, proposals
from features.planning.agent import loop
from features.planning.agent.tools import (
    TurnContext,
    search_context,
    search_label,
    snapshot,
    store,
    tool_context,
)
from features.planning.answers import (
    SKIPPED_TEXT,
    AnswerError,
    apply_answer,
    describe_answer,
    skip_slot,
)
from features.planning.commands import check_and_apply, describe_changes, removal
from features.planning.events import (
    PlanningDoneEvent,
    PlanningErrorEvent,
    PlanningEvent,
    PlanningPlanEvent,
    PlanningStatusEvent,
)
from features.planning.pending import offer_choice
from features.planning.picks import PickContext, PickError, apply_pick, describe_pick
from features.planning.schemas import (
    AnswerRecord,
    Candidate,
    ChangesRecord,
    OfferChoice,
    PickRecord,
    RunSearch,
)
from features.planning.searches import NoMoreOptionsError, SearchFailedError, run_search
from features.plans.schemas import SlotName
from shared.llm.types import LLMError

logger = logging.getLogger(__name__)

GENERIC_FAILURE = "Something went wrong. Please try again."
# Stored as the user's message for a button that has no words of its own, so the
# transcript (and the planner, reading it) shows what they did.
MORE_OPTIONS_TEXT = "Show me other options."
APPROVE_TEXT = "Yes, go ahead."
REJECT_TEXT = "No, leave it as it is."


# ---------------------------------------------------------------------------
# Triggers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Answer:
    slot: SlotName
    answer: BaseModel  # already parsed with `answers.parse_answer`


@dataclass(frozen=True)
class Skip:
    slot: SlotName


@dataclass(frozen=True)
class Pick:
    action: OfferChoice  # the open choices, as the router checked them
    candidates: list[Candidate]  # already checked with `picks.picked_candidates`


@dataclass(frozen=True)
class Text:
    content: str


@dataclass(frozen=True)
class Remove:
    item_id: UUID  # checked by the router: in the plan's itinerary list


@dataclass(frozen=True)
class Decide:
    proposal_id: UUID  # checked by the router: the suggestion the plan is waiting on
    approve: bool


@dataclass(frozen=True)
class Continue:
    refresh: bool = False  # checked by the router: only with refreshable choices open
    problem_id: str | None = None  # checked by the router: one of the plan's problems


Trigger = Answer | Skip | Pick | Text | Remove | Decide | Continue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _error(ctx: TurnContext, detail: str) -> PlanningErrorEvent:
    try:
        event = await store(ctx, "system", detail, kind=MessageKind.ERROR)
    except Exception:
        logger.exception("Could not store the error notice for plan %s", ctx.plan_id)
        return PlanningErrorEvent(detail=detail)
    return PlanningErrorEvent(detail=detail, message=event.message)


async def _changes(ctx: TurnContext, record: ChangesRecord) -> AsyncIterator[PlanningEvent]:
    """Store what commands did (or why they did nothing)."""
    content = describe_changes(record)
    yield await store(ctx, "assistant", content, kind=MessageKind.CHANGES, payload=record)
    if record.applied:
        yield PlanningPlanEvent(plan=await snapshot(ctx))


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


async def _answer(ctx: TurnContext, trigger: Answer) -> AsyncIterator[PlanningEvent]:
    record = AnswerRecord(slot=trigger.slot, value=trigger.answer.model_dump(mode="json"))
    text = describe_answer(trigger.answer)
    yield await store(ctx, "user", text, kind=MessageKind.INPUT, payload=record)
    try:
        await apply_answer(ctx.session, ctx.user_id, await snapshot(ctx), trigger.answer)
    except AnswerError as exc:
        # A rule the answer breaks against the plan; the planner asks again.
        yield await store(ctx, "assistant", f"That doesn't work: {exc}.", kind=MessageKind.NUDGE)
        return
    yield PlanningPlanEvent(plan=await snapshot(ctx))


async def _skip(ctx: TurnContext, trigger: Skip) -> AsyncIterator[PlanningEvent]:
    record = AnswerRecord(slot=trigger.slot, skipped=True)
    yield await store(ctx, "user", SKIPPED_TEXT, kind=MessageKind.INPUT, payload=record)
    await skip_slot(ctx.session, ctx.user_id, await snapshot(ctx), trigger.slot)
    yield PlanningPlanEvent(plan=await snapshot(ctx))


# ---------------------------------------------------------------------------
# Picks
# ---------------------------------------------------------------------------


async def _pick(ctx: TurnContext, trigger: Pick) -> AsyncIterator[PlanningEvent]:
    """Write a pick; a change that doesn't fit becomes a note and nothing is written (the
    choices stay open)."""
    record = PickRecord(
        offer_id=trigger.action.offer_id,
        slot=trigger.action.slot,
        candidate_ids=[candidate.id for candidate in trigger.candidates],
        titles=[candidate.title for candidate in trigger.candidates],
    )
    content = describe_pick(trigger.candidates)
    yield await store(ctx, "user", content, kind=MessageKind.PICK, payload=record)
    pick_ctx = PickContext(
        session=ctx.session, user_id=ctx.user_id, plan_id=ctx.plan_id, today=ctx.today
    )
    try:
        await apply_pick(pick_ctx, await snapshot(ctx), trigger.action, trigger.candidates)
    except PickError as exc:
        note = f"That one can't be added: {exc}."
        yield await store(ctx, "assistant", note, kind=MessageKind.NUDGE)
        return
    yield PlanningPlanEvent(plan=await snapshot(ctx))


async def _refresh(ctx: TurnContext) -> AsyncIterator[PlanningEvent]:
    """Search again for the open choices, leaving out every candidate already offered for
    that search since the plan last changed, and replace them with what's new. When
    nothing new turns up (or the search fails) the choices stay open, with a note."""
    plan = await snapshot(ctx)
    offer = await offers.current_offer(ctx.session, ctx.plan_id)
    choice = plan.pending
    if offer is None or not isinstance(choice, OfferChoice) or offer.id != choice.offer_id:
        return  # checked by the router; the plan moved on since
    label = search_label(offer.slot)
    action = RunSearch(
        slot=offer.slot, stage=offer.stage, label=label, gap=offer.gap, nights=offer.nights
    )
    yield PlanningStatusEvent(text=f"{label}…")
    exclude = await offers.offered_titles(ctx.session, ctx.plan_id, offer.query_hash, plan.version)
    try:
        candidates = await run_search(search_context(ctx, exclude), plan, action)
    except NoMoreOptionsError as exc:
        yield await store(ctx, "assistant", str(exc), kind=MessageKind.NUDGE)
        return
    except SearchFailedError as exc:
        yield await store(ctx, "system", str(exc), kind=MessageKind.ERROR)
        return
    replaced = await offers.open_offer(
        ctx.session,
        plan_id=ctx.plan_id,
        slot=offer.slot,
        stage=offer.stage,
        plan_version=plan.version,
        candidates=candidates,
        query_hash=offer.query_hash,
        gap=offer.gap,
        nights=offer.nights,
    )
    shown = offer_choice(replaced)
    yield await store(ctx, "assistant", shown.prompt, kind=MessageKind.CHOICES, payload=shown)


# ---------------------------------------------------------------------------
# Edits made with a button
# ---------------------------------------------------------------------------


async def _remove(ctx: TurnContext, trigger: Remove) -> AsyncIterator[PlanningEvent]:
    """An item's Remove button, as the command a typed removal becomes: checked, written
    with the rides to and from the item, and stored with what the trip now needs."""
    plan = await snapshot(ctx)
    found = removal(plan, trigger.item_id)
    if found is None:  # checked by the router; gone since (another tab removed it)
        return
    command, item = found
    yield await store(ctx, "user", f"Remove “{item.title}”.")
    record = await check_and_apply(tool_context(ctx), plan, [command])
    async for event in _changes(ctx, record):
        yield event


async def _decide(ctx: TurnContext, trigger: Decide) -> AsyncIterator[PlanningEvent]:
    """Approve or Reject on a suggestion card. A suggestion that was replaced, or made for
    a plan that has changed since, is closed with a note instead."""
    yield await store(ctx, "user", APPROVE_TEXT if trigger.approve else REJECT_TEXT)
    try:
        if not trigger.approve:
            await proposals.reject_proposal(ctx.session, ctx.plan_id, trigger.proposal_id)
            return
        _, record = await proposals.approve_proposal(
            tool_context(ctx), await snapshot(ctx), trigger.proposal_id
        )
    except proposals.ProposalClosedError as exc:
        yield await store(ctx, "assistant", f"{exc}.", kind=MessageKind.NUDGE)
        return
    async for event in _changes(ctx, record):
        yield event


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _write(ctx: TurnContext, trigger: Trigger) -> AsyncIterator[PlanningEvent]:
    """Store and write what the trigger did, before the planner is asked."""
    match trigger:
        case Answer():
            events = _answer(ctx, trigger)
        case Skip():
            events = _skip(ctx, trigger)
        case Pick():
            events = _pick(ctx, trigger)
        case Remove():
            events = _remove(ctx, trigger)
        case Decide():
            events = _decide(ctx, trigger)
        case Text():
            yield await store(ctx, "user", trigger.content)
            return
        case Continue(refresh=True):
            yield await store(ctx, "user", MORE_OPTIONS_TEXT)
            events = _refresh(ctx)
        case Continue():
            return
    async for event in events:
        yield event


async def run_turn(ctx: TurnContext, trigger: Trigger) -> AsyncIterator[PlanningEvent]:
    """Handle one trigger for a plan whose ownership the caller has checked."""
    try:
        async for event in _write(ctx, trigger):
            yield event
        # "More options" already ends on choices; anything else is the planner's call.
        if not (isinstance(trigger, Continue) and trigger.refresh):
            said = trigger.content if isinstance(trigger, Text) else None
            focus = trigger.problem_id if isinstance(trigger, Continue) else None
            async for event in loop.run(ctx, said=said, focus=focus):
                yield event
        yield PlanningDoneEvent(plan=await snapshot(ctx))

    except LLMError as exc:
        yield await _error(ctx, str(exc))
    except Exception:
        logger.exception("Planning turn failed for plan %s", ctx.plan_id)
        await ctx.session.rollback()
        yield await _error(ctx, GENERIC_FAILURE)
