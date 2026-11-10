"""Commands: plan edits the user asked for in their own words, checked against the plan's
rules and written all or nothing. The words are the approval, as a pick is.

A command (`planning.schemas.Command`) names what to change, pointing at an item by its
number in the itinerary list (`numbered_items`) and at a basic by its field. This module
turns each one into the write that already exists for it and runs that write's rules:

- `remove_item` / `update_item` → a `chat.tools` change (route, overnight, timezone,
  budget). A removal deletes the item, and with it the transfers that were only there to
  get to it or away from it (`itinerary.gaps.connecting_transfers`).
- `set_dates` / `set_travelers` / `set_destination` / `set_origin` / `set_budget` →
  `answers.parse_answer` / `apply_answer`, as the slot's form.
- `set_flight_preferences` / `set_lodging_preferences` / `set_activity_preferences` → the
  fields given, over the stored preferences, checked by the preference model and written
  with `plans_service.update_preferences`. The version moves on, so choices on show for
  that stage are searched for again.
- `change_stage` → `plans.stages.apply_stage_change` / `plans_service.set_stage`.
- `redo_search` → nothing to write: it checks that the search is the one behind the
  pending choices (`engine.refresh_search`), and the turn then runs it again the way
  "More options" does, leaving out what it already offered.
- `add_candidate` → the candidate's own `chat.tools` changes, as a pick writes them. The
  caller hands over the candidates the command may name, by ref (`candidates`); the
  command never carries an item's contents.
- `set_travel_mode` → `plans_service.set_no_flights`: the trip has no flights (or has
  them again).

`dry_run` checks commands and writes nothing: it is how a suggestion is vetted before it
is stored for approval (`planning.proposals`).

Once written, what the edit left to fix is named in the outcome with how to fix it, so the
user hears it then and there: a flight, a stay or the activities the plan no longer has,
or else any change of place left without a ride (the plan's gaps, as the ground stage
counts them). The engine then asks for the fix (`turn._drive`).

An item's Remove button comes here too, as a `remove_item` command (`removal`).

Every command is checked, with the earlier ones staged, before any is written; one that
breaks a rule means nothing is written, and the outcome says which rule. No model calls
here: free text reaches this module only as commands the reader filled in.
"""

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError

from features.chat import tools
from features.chat.schemas import ProposedChange
from features.itinerary import service as itinerary_service
from features.itinerary.gaps import connecting_transfers, describe_place
from features.itinerary.schemas import Gap, ItineraryItem, active
from features.planning.answers import (
    AnswerError,
    apply_answer,
    describe_answer,
    describe_value,
    parse_answer,
)
from features.planning.engine import refresh_search
from features.planning.picks import keep_outbound_token
from features.planning.schemas import (
    BASICS_FIELDS,
    MAX_COMMANDS,
    AddCandidate,
    AgentCommand,
    AnyCommand,
    AppliedChange,
    BasicName,
    BasicsChange,
    BasicsCommand,
    Candidate,
    ChangesRecord,
    ChangeStage,
    CommandName,
    PreferencesChange,
    PreferencesCommand,
    PreferenceSection,
    ProposalChange,
    RedoSearch,
    RejectedChange,
    RemoveItem,
    SearchName,
    SetTravelMode,
    UpdateItem,
)
from features.planning.slots import SLOTS
from features.planning.validation import night_ranges, range_label
from features.plans import service as plans_service
from features.plans.schemas import (
    STAGES,
    ActivityPreferences,
    FlightPreferences,
    Issue,
    LodgingPreferences,
    PlanPreferences,
    PlanSnapshot,
    PlanStage,
    StageChange,
)
from features.plans.stages import STAGE_LABELS, StageError, apply_stage_change
from shared.domain import IssueCode
from shared.validation import summarize_validation_error

logger = logging.getLogger(__name__)

# How each basic is named in a change's summary ("Dates: … → …").
_BASICS_LABELS: dict[BasicName, str] = {
    "dates": "Dates",
    "travelers": "Travelers",
    "destination": "Destination",
    "origin": "Starting point",
    "budget": "Budget",
}

# How each kind of preferences is named in a change's summary, and the stored model the
# fields given are merged into.
_PREFERENCE_LABELS: dict[PreferenceSection, str] = {
    "flights": "Flight preferences",
    "lodging": "Lodging preferences",
    "activities": "Activity preferences",
}
_PREFERENCE_MODELS: dict[PreferenceSection, type[BaseModel]] = {
    "flights": FlightPreferences,
    "lodging": LodgingPreferences,
    "activities": ActivityPreferences,
}

# Each `StopsChoice` as the `max_stops` a flight preference stores (None: any number).
_STOPS: dict[str, int | None] = {"nonstop": 0, "up_to_1": 1, "up_to_2": 2, "any": None}

# Each search `redo_search` can run again: the stage it belongs to, and its choices in words.
_SEARCHES: dict[SearchName, tuple[PlanStage, str]] = {
    "flights": (PlanStage.FLIGHTS, "flights"),
    "lodging": (PlanStage.LODGING, "places to stay"),
    "activities": (PlanStage.ACTIVITIES, "things to do"),
}


class CommandError(ValueError):
    """A command that can't be made. The message is shown to the user (and the model)."""


# ---------------------------------------------------------------------------
# Item references
# ---------------------------------------------------------------------------


def numbered_items(plan: PlanSnapshot) -> list[ItineraryItem]:
    """The items as a command numbers them, from 1: the itinerary in time order, as the
    timeline shows it (no cancelled items). The prompt that shows the list uses this too."""
    return active(plan.items)


def removal(plan: PlanSnapshot, item_id: UUID) -> tuple[RemoveItem, ItineraryItem] | None:
    """The command that removes item `item_id` (an item's Remove button), with the item;
    None when it isn't in the list (gone, or cancelled)."""
    for ref, item in enumerate(numbered_items(plan), start=1):
        if item.id == item_id:
            return RemoveItem(item=ref), item
    return None


def _item(items: Sequence[ItineraryItem], ref: int) -> ItineraryItem:
    if not 1 <= ref <= len(items):
        raise CommandError(f"There's no item {ref} in the list")
    return items[ref - 1]


def _item_label(items: Sequence[ItineraryItem], ref: int) -> str:
    return f"“{items[ref - 1].title}”" if 1 <= ref <= len(items) else f"item {ref}"


# ---------------------------------------------------------------------------
# Resolving: each command as the write it becomes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Step:
    """A command resolved against the plan, ready to check and write."""

    name: CommandName
    asked: str  # what was asked, for a rejection: 'Remove “Dinner”'
    done: str  # past tense, for the transcript: 'Removed “Dinner”'


@dataclass(frozen=True)
class _ItemStep(_Step):
    item_id: UUID
    change: ProposedChange
    # Transfers removed along with the item, each with its removal.
    along: tuple[tuple[UUID, ProposedChange], ...] = ()
    rides: str = ""  # their titles, for the summaries: '“Taxi there”, “Taxi back”'

    @property
    def changes(self) -> tuple[ProposedChange, ...]:
        return (self.change, *(change for _, change in self.along))

    @property
    def item_ids(self) -> tuple[UUID, ...]:
        return (self.item_id, *(item_id for item_id, _ in self.along))


@dataclass(frozen=True)
class _BasicsStep(_Step):
    answer: BaseModel  # parsed with the slot's own rules


@dataclass(frozen=True)
class _PreferencesStep(_Step):
    preferences: PlanPreferences  # all of them, with this command's (and earlier ones') edits


@dataclass(frozen=True)
class _StageStep(_Step):
    stage: PlanStage
    flights_skipped: bool


@dataclass(frozen=True)
class _SearchStep(_Step):
    """Nothing to write: the turn runs the search again once the other commands are in."""


@dataclass(frozen=True)
class _AddStep(_Step):
    ref: str  # how the command named the candidate
    candidate: Candidate
    changes: tuple[ProposedChange, ...]  # the candidate's own, in order


@dataclass(frozen=True)
class _TravelStep(_Step):
    no_flights: bool


def _suggested(step: _Step) -> str:
    """The step as a suggestion words it: what was asked, and the rides that go too."""
    if isinstance(step, _ItemStep) and step.rides:
        return f"{step.asked} and {step.rides}"
    return step.asked


def _asked(
    command: AnyCommand,
    items: Sequence[ItineraryItem],
    candidates: Mapping[str, Candidate] | None = None,
) -> str:
    """What the command asks for, in words, even when it doesn't resolve."""
    match command:
        case AddCandidate():
            found = (candidates or {}).get(command.candidate)
            return f"Add “{found.title}”" if found else f"Add option {command.candidate}"
        case SetTravelMode():
            return "Plan the trip without flights" if command.no_flights else "Plan flights"
        case RemoveItem():
            return f"Remove {_item_label(items, command.item)}"
        case UpdateItem():
            return f"Change {_item_label(items, command.item)}"
        case BasicsCommand():
            return f"Change the {_BASICS_LABELS[command.basic].lower()}"
        case PreferencesCommand():
            return f"Change the {_PREFERENCE_LABELS[command.section].lower()}"
        case RedoSearch():
            return f"Look for other {_SEARCHES[command.search][1]}"
        case ChangeStage():
            if command.action == "skip_flights":
                return "Skip flights"
            if command.stage is None:
                return "Go back a stage"
            return f"Go back to {STAGE_LABELS[command.stage]}"


def _item_change(item_id: UUID, changes: dict[str, JsonValue], summary: str) -> ProposedChange:
    return ProposedChange(
        call_id=f"command-{item_id}",
        name="update_item",
        arguments={"item_id": str(item_id), "changes": changes},
        summary=summary,
    )


def _removal(item: ItineraryItem) -> ProposedChange:
    return ProposedChange(
        call_id=f"command-{item.id}",
        name="remove_item",
        arguments={"item_id": str(item.id)},
        summary=f"Remove “{item.title}”",
    )


def _remove(
    command: RemoveItem, items: Sequence[ItineraryItem], keep: AbstractSet[UUID]
) -> _ItemStep:
    """Delete the item and the transfers to and from it, except those in `keep` (changed
    or removed by an earlier command)."""
    item = _item(items, command.item)
    along = [ride for ride in connecting_transfers(item.id, items) if ride.id not in keep]
    rides = ", ".join(f"“{ride.title}”" for ride in along)
    done = f"Removed “{item.title}”"
    if along:
        done += f" and {rides}"
    return _ItemStep(
        name=command.type,
        asked=f"Remove “{item.title}”",
        done=done,
        item_id=item.id,
        change=_removal(item),
        along=tuple((ride.id, _removal(ride)) for ride in along),
        rides=rides,
    )


def _update(command: UpdateItem, items: Sequence[ItineraryItem]) -> _ItemStep:
    item = _item(items, command.item)
    changes = command.changes.model_dump(mode="json", exclude_none=True)
    if not changes:
        raise CommandError("No changes were given")
    # In the edit model's field order: title, start, end, notes, status.
    fields = ", ".join(changes)
    asked = f"Change “{item.title}”: {fields}"
    return _ItemStep(
        name=command.type,
        asked=asked,
        done=f"Changed “{item.title}”: {fields}",
        item_id=item.id,
        change=_item_change(item.id, changes, asked),
    )


def _set_basics(command: BasicsChange, plan: PlanSnapshot, today: date) -> _BasicsStep:
    field = command.basic
    slot = BASICS_FIELDS[field]
    value = command.model_dump(mode="json", exclude={"type"}, exclude_none=True)
    # Travelers is its own slot value; the other basics are wrapped in their field name.
    try:
        answer = parse_answer(slot, value if field == "travelers" else {field: value}, today)
    except AnswerError as exc:
        raise CommandError(str(exc)) from exc
    read = SLOTS[slot].read
    before = read(plan) if read is not None else None
    after = describe_value(answer)
    label = _BASICS_LABELS[field]
    done = f"{label}: {describe_value(before)} → {after}" if before else f"{label}: {after}"
    return _BasicsStep(name=command.type, asked=_asked(command, ()), done=done, answer=answer)


def _set_preferences(command: PreferencesChange, plan: PlanSnapshot) -> _PreferencesStep:
    field = command.section
    edits: dict[str, JsonValue] = command.model_dump(
        mode="json", exclude={"type"}, exclude_none=True
    )
    if not edits:
        raise CommandError("No changes were given")
    stops = edits.get("max_stops")
    if isinstance(stops, str):
        edits["max_stops"] = _STOPS[stops]
    before: BaseModel | None = getattr(plan.preferences, field)
    stored = before.model_dump(mode="json") if before is not None else {}
    # Over the stored preferences, so the rest stay as they were. For flights that keeps
    # the picked flight out's token too, so the flights home still pair with it.
    try:
        after = _PREFERENCE_MODELS[field].model_validate({**stored, **edits})
    except ValidationError as exc:
        raise CommandError(summarize_validation_error(exc)) from exc
    label = _PREFERENCE_LABELS[field]
    now = describe_answer(after)
    done = f"{label}: {now}" if before is None else f"{label}: {describe_answer(before)} → {now}"
    return _PreferencesStep(
        name=command.type,
        asked=_asked(command, ()),
        done=done,
        preferences=plan.preferences.model_copy(update={field: after}),
    )


def _redo_search(command: RedoSearch, plan: PlanSnapshot) -> _SearchStep:
    stage, choices = _SEARCHES[command.search]
    pending = refresh_search(plan)
    if pending is None or pending.stage != stage:
        # Worded for the repair pass too: a stay already picked is found again by removing it.
        raise CommandError(
            f"No {choices} are being offered right now; to find a different one, remove it"
        )
    return _SearchStep(
        name=command.type, asked=_asked(command, ()), done=f"Looking for other {choices}"
    )


def _change_stage(command: ChangeStage, plan: PlanSnapshot) -> _StageStep:
    try:
        change = StageChange(action=command.action, stage=command.stage)
        stage, skipped = apply_stage_change(plan, change)
    except ValidationError as exc:
        raise CommandError(summarize_validation_error(exc)) from exc
    except StageError as exc:
        raise CommandError(str(exc)) from exc
    done = (
        "Skipped flights" if command.action == "skip_flights" else f"Back to {STAGE_LABELS[stage]}"
    )
    return _StageStep(
        name=command.type,
        asked=_asked(command, ()),
        done=done,
        stage=stage,
        flights_skipped=skipped,
    )


def _add_candidate(command: AddCandidate, candidates: Mapping[str, Candidate]) -> _AddStep:
    candidate = candidates.get(command.candidate)
    if candidate is None:
        raise CommandError(f"There's no option {command.candidate} to add; search for it first")
    changes = tuple(
        ProposedChange(
            call_id=f"add-{command.candidate}-{index}",
            name=change.name,
            arguments=change.arguments,
            summary=change.summary,
        )
        for index, change in enumerate(candidate.changes)
    )
    return _AddStep(
        name=command.type,
        asked=f"Add “{candidate.title}”",
        done=f"Added “{candidate.title}”",
        ref=command.candidate,
        candidate=candidate,
        changes=changes,
    )


def _set_travel_mode(command: SetTravelMode, plan: PlanSnapshot) -> _TravelStep:
    if plan.flights_skipped == command.no_flights:
        already = "has no flights" if command.no_flights else "is planned with flights"
        raise CommandError(f"The trip already {already}")
    done = "The trip has no flights" if command.no_flights else "The trip has flights again"
    return _TravelStep(
        name=command.type, asked=_asked(command, ()), done=done, no_flights=command.no_flights
    )


def _resolve(
    command: AnyCommand,
    items: Sequence[ItineraryItem],
    plan: PlanSnapshot,
    today: date,
    touched: AbstractSet[UUID],
    candidates: Mapping[str, Candidate],
) -> _Step:
    match command:
        case AddCandidate():
            return _add_candidate(command, candidates)
        case SetTravelMode():
            return _set_travel_mode(command, plan)
        case RemoveItem():
            return _remove(command, items, touched)
        case UpdateItem():
            return _update(command, items)
        case BasicsCommand():
            return _set_basics(command, plan, today)
        case PreferencesCommand():
            return _set_preferences(command, plan)
        case ChangeStage():
            return _change_stage(command, plan)
        case RedoSearch():
            return _redo_search(command, plan)


# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------


@dataclass
class _Checked:
    steps: list[_Step]
    rejected: list[RejectedChange]


async def _stage(
    ctx: tools.ToolContext, staged: list[ProposedChange], changes: Sequence[ProposedChange]
) -> None:
    """Check one command's changes on top of `staged` and add them to it; `CommandError`
    (and nothing added) when one doesn't fit."""
    trial = list(staged)
    for change in changes:
        outcome = await tools.check_change(replace(ctx, staged=tuple(trial)), change)
        if not outcome.ok:
            raise CommandError(outcome.summary)
        trial.append(change)
    staged[:] = trial


async def _check(
    ctx: tools.ToolContext,
    plan: PlanSnapshot,
    commands: Sequence[AnyCommand],
    candidates: Mapping[str, Candidate],
) -> _Checked:
    """Resolve and check every command (writes nothing). Item changes are checked with the
    earlier ones staged, so "remove the hotel, then move dinner" checks the move without
    the hotel; stage, travel-mode and preference changes are checked from where the
    earlier ones left the plan. Every command is checked even after one fails, so all the
    broken rules are reported."""
    if len(commands) > MAX_COMMANDS:
        too_many = RejectedChange(
            summary="Change the trip", reason=f"At most {MAX_COMMANDS} changes at a time"
        )
        return _Checked(steps=[], rejected=[too_many])
    items = numbered_items(plan)
    checked = _Checked(steps=[], rejected=[])
    staged: list[ProposedChange] = []
    touched: set[UUID] = set()
    removed: set[UUID] = set()  # by an earlier removal, as one of its transfers
    added: set[str] = set()  # candidate refs
    at = plan  # the plan as the stage, travel-mode and preference changes so far leave it
    for command in commands:
        try:
            step = _resolve(command, items, at, ctx.today, touched, candidates)
            if isinstance(step, _ItemStep) and step.item_id in removed:
                # "Remove dinner and the taxi there": the taxi is already going.
                if not isinstance(command, RemoveItem):
                    raise CommandError("It's removed along with another item")
                step = _Step(name=step.name, asked=step.asked, done=step.done)
            elif isinstance(step, _ItemStep):
                # A second edit would be checked against the item as stored, not as the
                # first edit leaves it, so both have to come as one.
                if step.item_id in touched:
                    raise CommandError("Change each item in one command")
                await _stage(ctx, staged, step.changes)
                touched.update(step.item_ids)
                removed.update(step.item_ids[1:])
            elif isinstance(step, _AddStep):
                if step.ref in added:
                    raise CommandError("It's already being added")
                await _stage(ctx, staged, step.changes)
                added.add(step.ref)
            elif isinstance(step, _TravelStep):
                at = at.model_copy(update={"flights_skipped": step.no_flights})
            elif isinstance(step, _StageStep):
                at = at.model_copy(
                    update={"stage": step.stage, "flights_skipped": step.flights_skipped}
                )
            elif isinstance(step, _PreferencesStep):
                at = at.model_copy(update={"preferences": step.preferences})
        except CommandError as exc:
            asked = _asked(command, items, candidates)
            checked.rejected.append(RejectedChange(summary=asked, reason=str(exc)))
            continue
        checked.steps.append(step)
    return checked


@dataclass(frozen=True)
class DryRun:
    """What `dry_run` found: the commands as checked changes, or why they don't fit."""

    changes: list[ProposalChange]  # one per command, in order; empty when any is rejected
    rejected: list[RejectedChange]


async def dry_run(
    ctx: tools.ToolContext,
    plan: PlanSnapshot,
    commands: Sequence[AgentCommand],
    candidates: Mapping[str, Candidate] | None = None,
) -> DryRun:
    """Check the commands against the plan's rules as `check_and_apply` would, and write
    nothing. `candidates` are the search results an `add_candidate` may name, by ref."""
    checked = await _check(ctx, plan, commands, candidates or {})
    if checked.rejected:
        return DryRun(changes=[], rejected=checked.rejected)
    changes = [
        ProposalChange(
            summary=_suggested(step),
            command=command,
            candidate=step.candidate if isinstance(step, _AddStep) else None,
        )
        for command, step in zip(commands, checked.steps, strict=True)
    ]
    return DryRun(changes=changes, rejected=[])


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def _adds_flight_out(candidate: Candidate) -> bool:
    return any(
        change.name == "add_flight" and change.arguments.get("direction") == "outbound"
        for change in candidate.changes
    )


async def _write(ctx: tools.ToolContext, plan: PlanSnapshot, step: _Step) -> None:
    """Write one checked command; `CommandError` when the plan changed since the check."""
    if isinstance(step, _ItemStep):
        for change in step.changes:
            outcome = await tools.apply_change(ctx, change)
            if not outcome.ok:
                raise CommandError(outcome.summary)
    elif isinstance(step, _BasicsStep):
        try:
            await apply_answer(ctx.session, ctx.user_id, plan, step.answer)
        except AnswerError as exc:
            raise CommandError(str(exc)) from exc
    elif isinstance(step, _PreferencesStep):
        await plans_service.update_preferences(
            ctx.session, ctx.user_id, ctx.plan_id, step.preferences
        )
    elif isinstance(step, _StageStep):
        await plans_service.set_stage(
            ctx.session, ctx.user_id, ctx.plan_id, step.stage, step.flights_skipped
        )
    elif isinstance(step, _TravelStep):
        await plans_service.set_no_flights(ctx.session, ctx.user_id, ctx.plan_id, step.no_flights)
    elif isinstance(step, _AddStep):
        for change in step.changes:
            outcome = await tools.apply_change(ctx, change)
            if not outcome.ok:
                raise CommandError(outcome.summary)
        if _adds_flight_out(step.candidate):
            # As a pick does. Read again: an earlier command may have changed the preferences.
            row = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
            await keep_outbound_token(
                ctx.session,
                ctx.user_id,
                ctx.plan_id,
                PlanPreferences.model_validate(row.preferences),
                step.candidate.follow_up_token,
            )


async def check_and_apply(
    ctx: tools.ToolContext,
    plan: PlanSnapshot,
    commands: Sequence[AnyCommand],
    candidates: Mapping[str, Candidate] | None = None,
) -> ChangesRecord:
    """Check every command against the plan's rules and, when all fit, write them in order.
    Each write bumps the plan's version, so open offers go stale and searches run again.
    `candidates` are the search results an `add_candidate` may name, by ref. Expected
    failures come back as `rejected`; they never raise."""
    if not commands:
        return ChangesRecord()
    checked = await _check(ctx, plan, commands, candidates or {})
    if checked.rejected:
        logger.info("Plan %s: %d command(s) rejected", plan.id, len(checked.rejected))
        return ChangesRecord(rejected=checked.rejected)

    applied: list[AppliedChange] = []
    for step in checked.steps:
        try:
            await _write(ctx, plan, step)
        except CommandError as exc:
            # Writes commit one by one, so the earlier ones stay; say where it stopped.
            logger.warning("Plan %s: command %s failed: %s", plan.id, step.name, exc)
            rejected = [RejectedChange(summary=step.asked, reason=str(exc))]
            return ChangesRecord(applied=applied, rejected=rejected)
        item_id = step.item_id if isinstance(step, _ItemStep) else None
        applied.append(AppliedChange(name=step.name, summary=step.done, item_id=item_id))
    logger.info("Plan %s: applied %s", plan.id, ", ".join(step.name for step in checked.steps))
    return ChangesRecord(applied=applied, left_open=await _left_open(ctx, plan))


# ---------------------------------------------------------------------------
# What the edit left to fix
# ---------------------------------------------------------------------------

# How the user can fix each checklist issue an edit can open, put to them with it. The
# planner then asks (the choices or the form), so each names the ways to answer.
_FLIGHT_FIX = (
    "Pick another flight, tell me what you'd like instead, or skip flights if you'll get "
    "there another way"
)
_FIXES: dict[IssueCode, str] = {
    IssueCode.MISSING_OUTBOUND: _FLIGHT_FIX,
    IssueCode.MISSING_RETURN: _FLIGHT_FIX,
    IssueCode.UNCOVERED_NIGHT: "Pick another place to stay, or tell me what you'd like instead",
    IssueCode.NO_ACTIVITIES: "Pick something else to do, or tell me what you're into",
}
_RIDE_FIX = "Pick a way to get there, or tell me how you'd like to go"


def _route(gap: Gap) -> tuple[str, str]:
    return (
        describe_place(gap.from_place, gap.after_item_id),
        describe_place(gap.to_place, gap.before_item_id),
    )


def _new(before: Sequence[Issue], after: Sequence[Issue]) -> list[Issue]:
    """The issues in `after` that `before` didn't have, counted by what they say."""
    known = Counter((issue.code, issue.message) for issue in before)
    opened: list[Issue] = []
    for issue in after:
        key = (issue.code, issue.message)
        if known[key] > 0:
            known[key] -= 1
        else:
            opened.append(issue)
    return opened


def _checklist(plan: PlanSnapshot, reached: PlanStage) -> list[Issue]:
    """The blocking issues an edit can open and the user is asked to fix (flights, stays,
    activities), in stages up to `reached`. A later stage asks for its own anyway."""
    upto = STAGES[: STAGES.index(reached) + 1]
    return [
        issue
        for status in plan.stages
        if status.stage in upto
        for issue in status.issues
        if issue.code in _FIXES
    ]


def _issue_notes(opened: Sequence[Issue]) -> list[str]:
    """Each opened issue in words, with how to fix it. Uncovered nights come as runs, one
    stay each, as the lodging stage asks for them."""
    notes: list[str] = []
    nights = [i.date for i in opened if i.code == IssueCode.UNCOVERED_NIGHT and i.date]
    for issue in opened:
        if issue.code != IssueCode.UNCOVERED_NIGHT:
            notes.append(f"The trip needs {issue.message} now. {_FIXES[issue.code]}")
    for run in night_ranges(nights):
        fix = _FIXES[IssueCode.UNCOVERED_NIGHT]
        notes.append(f"The trip needs a place to stay {range_label(run)} now. {fix}")
    return notes


async def _left_open(ctx: tools.ToolContext, before: PlanSnapshot) -> list[str]:
    """What the commands left for the user to fix, in words, each with how: a flight, a
    stay or the activities the plan no longer has, else each change of place without a
    ride now that wasn't one before.

    Rides are compared by places, counted: the ids around a gap change when items go, and
    so can its day (the flat goes, so the ride from home is due at dinner instead), but a
    route missing more often than before is new. They aren't named when a flight or stay
    is missing too: replacing it moves the rides, and the ground stage finds them then."""
    after = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
    opened = _new(_checklist(before, after.stage), _checklist(after, after.stage))
    if opened:
        return _issue_notes(opened)
    known = Counter(_route(gap) for gap in before.gaps)
    # The ground stage searches for rides; before it, they wait until it comes.
    at_ground = after.stage in (PlanStage.GROUND, PlanStage.REVIEW)
    fix = _RIDE_FIX if at_ground else "I'll look for a ride at ground transport"
    notes: list[str] = []
    for gap in after.gaps:
        route = _route(gap)
        if known[route] > 0:
            known[route] -= 1
            continue
        notes.append(f"Nothing gets you from {route[0]} to {route[1]} now. {fix}")
    return notes


# ---------------------------------------------------------------------------
# Transcript text
# ---------------------------------------------------------------------------


def describe_changes(record: ChangesRecord) -> str:
    """The outcome as one transcript message: what was changed, any rides that left
    missing, then what wasn't changed and why."""
    lines = [f"{change.summary}." for change in record.applied]
    lines += [f"{note}." for note in record.left_open]
    lines += [
        f"Couldn't {change.summary[0].lower()}{change.summary[1:]}: {change.reason}."
        for change in record.rejected
    ]
    return " ".join(lines)
