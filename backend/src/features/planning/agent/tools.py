"""The planner's tools: what each call does to the plan, the transcript and the turn.

The model decides which tool to call; this module decides whether the call is allowed and
runs it through the writes that already exist (`commands`, `picks`, `proposals`, `offers`,
`searches`). Approval is enforced here, not by the prompt:

- The tools that write without a card to approve (`apply_user_changes`, `pick_choice`,
  `decide_proposal`) are only available on a turn the user typed, and `apply_user_changes`
  must quote that text.
- Items are only added from search results, named by ref; a command never carries an
  item's contents.
- Paid searches are capped per turn (`MAX_PAID_SEARCHES`).

A call that can't be made comes back as a `ToolFailure` for the model to repair; nothing
here raises for an expected failure.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat import service as chat_service
from features.chat.schemas import MessageKind, MessageRole
from features.chat.tools import ToolContext
from features.planning import offers, proposals
from features.planning import service as planning_service
from features.planning.agent.context import candidate_view, planner_context, problem_views
from features.planning.agent.tool_models import (
    SEARCH_PREFERENCES,
    SEARCH_SLOTS,
    TOOLS,
    ApplyUserChangesArgs,
    AskUserArgs,
    DecideProposalArgs,
    PickChoiceArgs,
    PresentChoicesArgs,
    SearchArgs,
    SearchKind,
    Shown,
    SuggestChangesArgs,
    ToolFailure,
    ToolName,
    ValidatePlanArgs,
    ValidationResult,
    WriteResult,
)
from features.planning.agent.tool_models import SearchResult as SearchFound
from features.planning.commands import check_and_apply, describe_changes, dry_run
from features.planning.events import (
    PlanningEvent,
    PlanningMessage,
    PlanningMessageEvent,
    PlanningPlanEvent,
)
from features.planning.pending import ask_input, offer_choice
from features.planning.picks import (
    PickContext,
    PickError,
    apply_pick,
    describe_pick,
    picked_candidates,
)
from features.planning.schemas import (
    Candidate,
    ChangesRecord,
    OfferChoice,
    Problem,
    ReviewProposal,
    RunSearch,
    TravelPlan,
)
from features.planning.search_types import title_key
from features.planning.searches import SearchContext, SearchFailedError, query_hash, run_search
from features.planning.slots import SLOTS
from features.plans.schemas import SlotName
from shared.flights.types import FlightProvider
from shared.hotels.types import HotelProvider
from shared.llm.types import LLMClient, LLMToolCall
from shared.places.types import PlaceProvider
from shared.validation import summarize_validation_error

logger = logging.getLogger(__name__)

# Flight, stay and activity searches are metered by their providers. Transfers come from
# rules and don't count.
MAX_PAID_SEARCHES = 2

# What the UI shows while a search runs (and the search's label, for its failure notice).
_SEARCH_LABELS: dict[SearchKind, str] = {
    "flight_out": "Finding flights out",
    "flight_home": "Finding flights home",
    "stay": "Finding places to stay",
    "activities": "Finding things to do",
    "transfer": "Finding a way there",
}
_FLIGHTS_IN_WORDS: dict[SearchKind, str] = {
    "flight_out": "flight out",
    "flight_home": "flight home",
}

# Searches of a turn are named a, b, c...; their candidates a1, a2...
_SEARCH_NAMES = "abcdefghijklmnopqrstuvwxyz"


# ---------------------------------------------------------------------------
# Context and state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnContext:
    """What a planning turn runs with."""

    session: AsyncSession
    llm: LLMClient
    user_id: UUID
    plan_id: UUID
    today: date
    flights: FlightProvider
    hotels: HotelProvider
    places: PlaceProvider


@dataclass(frozen=True)
class Found:
    """One search's results, kept for the turn so later calls can name them."""

    action: RunSearch
    candidates: list[Candidate]
    plan_version: int  # the plan they were found for; any change since makes them stale
    key: str  # `searches.query_hash`


@dataclass
class TurnState:
    """One turn of the loop: what set it off, and what its tools have done so far."""

    ctx: TurnContext
    # The text the user typed, when that is what started the turn. The writing tools are
    # only available with it.
    said: str | None = None
    searches: dict[str, Found] = field(default_factory=dict)
    paid_searches: int = 0

    @property
    def searches_left(self) -> int:
        return max(0, MAX_PAID_SEARCHES - self.paid_searches)

    def candidates(self, plan_version: int) -> dict[str, Candidate]:
        """The search results an `add_candidate` may name, by ref: this turn's, found for
        the plan as it stands."""
        return {
            f"{name}{candidate.id}": candidate
            for name, found in self.searches.items()
            if found.plan_version == plan_version
            for candidate in found.candidates
        }


@dataclass(frozen=True)
class ToolOutcome:
    result: BaseModel  # sent back to the model as JSON
    events: Sequence[PlanningEvent] = ()  # streamed to the client, in order
    ends_turn: bool = False  # set by `run_tool`, from the tool's definition


class _RefusedError(Exception):
    """The call can't be made. The message goes back to the model as a `ToolFailure`."""


# ---------------------------------------------------------------------------
# Helpers shared with the turn (`planning.turn`)
# ---------------------------------------------------------------------------


async def snapshot(ctx: TurnContext) -> TravelPlan:
    return await planning_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)


async def store(
    ctx: TurnContext,
    role: MessageRole,
    content: str,
    *,
    kind: MessageKind = MessageKind.MESSAGE,
    payload: BaseModel | None = None,
) -> PlanningMessageEvent:
    """Store one transcript message; the event is what the client appends."""
    row = await chat_service.add_message(
        ctx.session, ctx.plan_id, role, content, kind=kind, payload=payload
    )
    return PlanningMessageEvent(message=PlanningMessage.model_validate(row))


def search_context(ctx: TurnContext, exclude: frozenset[str] = frozenset()) -> SearchContext:
    return SearchContext(
        session=ctx.session,
        today=ctx.today,
        flights=ctx.flights,
        hotels=ctx.hotels,
        places=ctx.places,
        exclude=exclude,
    )


def search_label(slot: SlotName) -> str:
    """What the UI shows while the search for pick slot `slot` runs."""
    return next(
        (_SEARCH_LABELS[kind] for kind, searched in SEARCH_SLOTS.items() if searched == slot),
        "Searching",
    )


def tool_context(ctx: TurnContext) -> ToolContext:
    return ToolContext(
        session=ctx.session, user_id=ctx.user_id, plan_id=ctx.plan_id, today=ctx.today
    )


def _failure(message: str) -> ToolOutcome:
    return ToolOutcome(result=ToolFailure(error=message))


async def _written(
    state: TurnState, summary: str, *, changed: bool, events: Sequence[PlanningEvent] = ()
) -> ToolOutcome:
    """A write's outcome: what it did in words, and the plan it left."""
    plan = await snapshot(state.ctx)
    streamed = [*events, PlanningPlanEvent(plan=plan)] if changed else list(events)
    context = planner_context(plan, today=state.ctx.today, searches_left=state.searches_left)
    return ToolOutcome(
        result=WriteResult(changed=changed, summary=summary, plan=context), events=streamed
    )


async def _changes(state: TurnState, record: ChangesRecord) -> ToolOutcome:
    """What commands did: stored in the transcript when anything was written; rules they
    broke only go back to the model, which repairs them or tells the user."""
    summary = describe_changes(record)
    if not record.applied:
        return await _written(state, summary, changed=False)
    message = await store(state.ctx, "assistant", summary, kind=MessageKind.CHANGES, payload=record)
    return await _written(state, summary, changed=True, events=[message])


# ---------------------------------------------------------------------------
# Which tools a round offers
# ---------------------------------------------------------------------------


def available(state: TurnState, plan: TravelPlan, *, last_round: bool) -> list[ToolName]:
    """The tools the model may call now. The last round only offers the ones that end the
    turn, so the loop always finishes on something for the user. The tools that write
    without a card are offered only when the user typed this turn's trigger, and the two
    that answer something open only while it is open."""
    ending: list[ToolName] = ["ask_user", "present_choices", "suggest_changes"]
    if last_round:
        return ending
    names: list[ToolName] = ["validate_plan", "search", *ending]
    if state.said is not None:
        names.append("apply_user_changes")
        if isinstance(plan.pending, OfferChoice):
            names.append("pick_choice")
        if isinstance(plan.pending, ReviewProposal):
            names.append("decide_proposal")
    return names


def status_text(call: LLMToolCall) -> str | None:
    """What the UI shows while `call` runs; None for a call that shows its own result."""
    if call.name == "search":
        kind = call.arguments.get("kind")
        label = next((text for name, text in _SEARCH_LABELS.items() if name == kind), "Searching")
        return f"{label}…"
    if call.name == "validate_plan":
        return "Checking the trip…"
    if call.name in ("apply_user_changes", "pick_choice", "decide_proposal"):
        return "Updating the trip…"
    return None


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------


async def _validate(state: TurnState) -> ToolOutcome:
    plan = await snapshot(state.ctx)
    return ToolOutcome(
        result=ValidationResult(problems=problem_views(plan.problems), complete=plan.complete)
    )


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _problem(plan: TravelPlan, problem_id: str | None, slot: SlotName) -> Problem:
    """The problem a stay or transfer search is for: it holds the nights or the gap."""
    fixable = [p for p in plan.problems if p.fix.how == "search" and p.fix.slot == slot]
    found = next((p for p in fixable if p.id == problem_id), None)
    if found is None:
        known = ", ".join(p.id for p in fixable) or "none"
        raise _RefusedError(
            f'This search needs "problem": the id of the problem it fixes (now: {known})'
        )
    return found


def _search_action(plan: TravelPlan, args: SearchArgs) -> RunSearch:
    slot = SEARCH_SLOTS[args.kind]
    spec = SLOTS[slot]
    label = _SEARCH_LABELS[args.kind]
    if args.kind in ("stay", "transfer"):
        problem = _problem(plan, args.problem, slot)
        return RunSearch(
            slot=slot, stage=spec.stage, label=label, gap=problem.gap, nights=problem.nights
        )
    if args.kind != "activities" and not any(p.fix.slot == slot for p in plan.problems):
        # A second flight the same way would break the route's rules when added.
        flight = _FLIGHTS_IN_WORDS[args.kind]
        if plan.flights_skipped:
            raise _RefusedError(
                "The trip is planned without flights; set_travel_mode brings them back"
            )
        raise _RefusedError(f"The trip already has a {flight}; remove it to look for another")
    return RunSearch(slot=slot, stage=spec.stage, label=label)


def _check_preferences(plan: TravelPlan, kind: SearchKind) -> None:
    slot = SEARCH_PREFERENCES.get(kind)
    if slot is None:
        return
    read = SLOTS[slot].read
    if read is not None and read(plan) is None:
        raise _RefusedError(
            f"This search needs the user's preferences first: ask_user with slot {slot.value}"
        )


async def _shown(state: TurnState, plan: TravelPlan, key: str) -> frozenset[str]:
    """Titles already found for this search: offered since the plan last changed, or found
    earlier this turn."""
    ctx = state.ctx
    offered = await offers.offered_titles(ctx.session, ctx.plan_id, key, plan.version)
    earlier = {
        title_key(candidate.title)
        for found in state.searches.values()
        if found.key == key and found.plan_version == plan.version
        for candidate in found.candidates
    }
    return offered | earlier


async def _search(state: TurnState, args: SearchArgs) -> ToolOutcome:
    ctx = state.ctx
    plan = await snapshot(ctx)
    action = _search_action(plan, args)
    _check_preferences(plan, args.kind)
    paid = args.kind != "transfer"
    if paid and state.searches_left == 0:
        raise _RefusedError(
            "No searches are left this turn. Show what you found, or ask the user what next"
        )
    if len(state.searches) == len(_SEARCH_NAMES):
        raise _RefusedError("Too many searches this turn")
    key = query_hash(plan, action)
    exclude = await _shown(state, plan, key) if args.more else frozenset[str]()
    search_ctx = search_context(ctx, exclude)
    if paid:
        state.paid_searches += 1  # counted even when it fails: the provider was asked
    try:
        candidates = await run_search(search_ctx, plan, action)
    except SearchFailedError as exc:
        logger.info("Plan %s: %s search failed: %s", plan.id, args.kind, exc)
        return _failure(str(exc))
    name = _SEARCH_NAMES[len(state.searches)]
    state.searches[name] = Found(
        action=action, candidates=candidates, plan_version=plan.version, key=key
    )
    return ToolOutcome(
        result=SearchFound(
            search=name,
            candidates=[candidate_view(f"{name}{c.id}", c) for c in candidates],
            searches_left=state.searches_left,
        )
    )


# ---------------------------------------------------------------------------
# The tools that end the turn
# ---------------------------------------------------------------------------


async def _ask_user(state: TurnState, args: AskUserArgs) -> ToolOutcome:
    ctx = state.ctx
    if args.slot is None:
        message = await store(ctx, "assistant", args.question)
        return ToolOutcome(result=Shown(shown="question"), events=[message])
    form = ask_input(SlotName(args.slot), await snapshot(ctx), args.question)
    message = await store(
        ctx, "assistant", form.prompt, kind=MessageKind.EXPECTED_INPUT, payload=form
    )
    return ToolOutcome(result=Shown(shown="form"), events=[message])


async def _present_choices(state: TurnState, args: PresentChoicesArgs) -> ToolOutcome:
    ctx = state.ctx
    found = state.searches.get(args.search.strip().casefold())
    if found is None:
        ran = ", ".join(state.searches) or "none"
        raise _RefusedError(f"There's no search {args.search} this turn (searches so far: {ran})")
    plan = await snapshot(ctx)
    if found.plan_version != plan.version:
        raise _RefusedError("The trip changed since that search; search again")
    offer = await offers.open_offer(
        ctx.session,
        plan_id=ctx.plan_id,
        slot=found.action.slot,
        stage=found.action.stage,
        plan_version=plan.version,
        candidates=found.candidates,
        query_hash=found.key,
        gap=found.action.gap,
        nights=found.action.nights,
    )
    choice = offer_choice(offer)
    message = await store(ctx, "assistant", choice.prompt, kind=MessageKind.CHOICES, payload=choice)
    return ToolOutcome(result=Shown(shown="choices"), events=[message])


async def _suggest_changes(state: TurnState, args: SuggestChangesArgs) -> ToolOutcome:
    ctx = state.ctx
    plan = await snapshot(ctx)
    checked = await dry_run(tool_context(ctx), plan, args.changes, state.candidates(plan.version))
    if checked.rejected:
        broken = "; ".join(f"{r.summary}: {r.reason}" for r in checked.rejected)
        raise _RefusedError(
            f"Those changes break the trip's rules, so nothing is suggested: {broken}"
        )
    await proposals.open_proposal(
        ctx.session,
        plan_id=ctx.plan_id,
        plan_version=plan.version,
        reason=args.reason,
        changes=checked.changes,
    )
    # The card itself is drawn from the plan's `pending`; the transcript keeps the reason.
    message = await store(ctx, "assistant", args.reason)
    return ToolOutcome(result=Shown(shown="suggestion"), events=[message])


# ---------------------------------------------------------------------------
# The tools that write: only on a turn the user typed
# ---------------------------------------------------------------------------


def _words(text: str) -> str:
    """Text as quotes are compared: case and spacing don't count."""
    return " ".join(text.casefold().split())


def _said(state: TurnState) -> str:
    if state.said is None:
        raise _RefusedError("The user didn't type anything this turn; use suggest_changes")
    return state.said


async def _apply_user_changes(state: TurnState, args: ApplyUserChangesArgs) -> ToolOutcome:
    ctx = state.ctx
    if _words(args.quote) not in _words(_said(state)):
        raise _RefusedError(
            "That quote isn't in the user's latest message, so they didn't ask for this; "
            "use suggest_changes"
        )
    plan = await snapshot(ctx)
    record = await check_and_apply(
        tool_context(ctx), plan, args.changes, state.candidates(plan.version)
    )
    return await _changes(state, record)


async def _pick_choice(state: TurnState, args: PickChoiceArgs) -> ToolOutcome:
    ctx = state.ctx
    _said(state)
    plan = await snapshot(ctx)
    if not isinstance(plan.pending, OfferChoice):
        raise _RefusedError("No choices are open to pick from")
    pick_ctx = PickContext(
        session=ctx.session, user_id=ctx.user_id, plan_id=ctx.plan_id, today=ctx.today
    )
    try:
        candidates = picked_candidates(plan.pending, args.candidates)
        await apply_pick(pick_ctx, plan, plan.pending, candidates)
    except PickError as exc:
        raise _RefusedError(str(exc)) from exc
    summary = f"Got it: {describe_pick(candidates)}."
    message = await store(ctx, "assistant", summary)
    return await _written(state, summary, changed=True, events=[message])


async def _decide_proposal(state: TurnState, args: DecideProposalArgs) -> ToolOutcome:
    ctx = state.ctx
    _said(state)
    plan = await snapshot(ctx)
    if not isinstance(plan.pending, ReviewProposal):
        raise _RefusedError("No suggestion is waiting for a decision")
    proposal_id = plan.pending.proposal_id
    try:
        if args.decision == "reject":
            await proposals.reject_proposal(ctx.session, ctx.plan_id, proposal_id)
            return await _written(state, "The suggestion was rejected.", changed=False)
        _, record = await proposals.approve_proposal(tool_context(ctx), plan, proposal_id)
    except proposals.ProposalClosedError as exc:
        raise _RefusedError(str(exc)) from exc
    return await _changes(state, record)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _run(state: TurnState, args: BaseModel) -> ToolOutcome:
    match args:
        case ValidatePlanArgs():
            return await _validate(state)
        case SearchArgs():
            return await _search(state, args)
        case AskUserArgs():
            return await _ask_user(state, args)
        case PresentChoicesArgs():
            return await _present_choices(state, args)
        case SuggestChangesArgs():
            return await _suggest_changes(state, args)
        case ApplyUserChangesArgs():
            return await _apply_user_changes(state, args)
        case PickChoiceArgs():
            return await _pick_choice(state, args)
        case DecideProposalArgs():
            return await _decide_proposal(state, args)
    raise _RefusedError("That tool can't be run")


async def run_tool(state: TurnState, call: LLMToolCall, allowed: Sequence[ToolName]) -> ToolOutcome:
    """Run one of the model's tool calls. A tool that isn't offered now, arguments that
    don't fit, and a call the plan's rules turn down all come back as a `ToolFailure`."""
    definition = TOOLS.get(call.name)
    if definition is None or definition.name not in allowed:
        return _failure(f"{call.name} isn't available now; the tools are: {', '.join(allowed)}")
    try:
        args = definition.arguments.model_validate(call.arguments)
    except ValidationError as exc:
        return _failure(f"The arguments don't fit: {summarize_validation_error(exc)}")
    try:
        outcome = await _run(state, args)
    except _RefusedError as exc:
        return _failure(str(exc))
    if isinstance(outcome.result, ToolFailure):
        return outcome
    return ToolOutcome(result=outcome.result, events=outcome.events, ends_turn=definition.ends_turn)
