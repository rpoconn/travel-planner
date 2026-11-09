"""Commands: plan edits the user asked for in their own words, checked against the plan's
rules and written all or nothing. The words are the approval, as a pick is.

A command (`planning.schemas.Command`) names what to change, pointing at an item by its
number in the itinerary list (`numbered_items`) and at a basic by its field. This module
turns each one into the write that already exists for it and runs that write's rules:

- `remove_item` / `update_item` → a `chat.tools` change (route, overnight, timezone,
  budget). A removal cancels the item rather than deleting it, so it can come back.
- `set_basics` → `answers.parse_answer` / `apply_answer`, as the slot's form.
- `set_preferences` → the fields given, over the stored preferences, checked by the
  preference model and written with `plans_service.update_preferences`. The version moves
  on, so choices on show for that stage are searched for again.
- `change_stage` → `plans.stages.apply_stage_change` / `plans_service.set_stage`.
- `redo_search` → nothing to write: it checks that the search is the one behind the
  pending choices (`engine.refresh_search`), and the turn then runs it again the way
  "More options" does, leaving out what it already offered.

Every command is checked, with the earlier ones staged, before any is written; one that
breaks a rule means nothing is written, and the outcome says which rule. No model calls
here: free text reaches this module only as commands the reader filled in.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from uuid import UUID

from pydantic import BaseModel, JsonValue, ValidationError

from features.chat import tools
from features.chat.schemas import ProposedChange
from features.itinerary.schemas import ItineraryItem
from features.planning.answers import (
    AnswerError,
    apply_answer,
    describe_answer,
    describe_value,
    parse_answer,
)
from features.planning.engine import refresh_search
from features.planning.schemas import (
    BASICS_FIELDS,
    MAX_COMMANDS,
    PREFERENCE_FIELDS,
    AppliedChange,
    ChangesRecord,
    ChangeStage,
    Command,
    CommandName,
    RedoSearch,
    RejectedChange,
    RemoveItem,
    SearchName,
    SetBasics,
    SetPreferences,
    UpdateItem,
)
from features.planning.slots import SLOTS
from features.plans import service as plans_service
from features.plans.schemas import (
    ActivityPreferences,
    FlightPreferences,
    LodgingPreferences,
    PlanPreferences,
    PlanSnapshot,
    PlanStage,
    StageChange,
)
from features.plans.stages import STAGE_LABELS, StageError, apply_stage_change
from shared.validation import summarize_validation_error

logger = logging.getLogger(__name__)

# How each basic is named in a change's summary ("Dates: … → …").
_BASICS_LABELS: dict[str, str] = {
    "dates": "Dates",
    "travelers": "Travelers",
    "destination": "Destination",
    "origin": "Starting point",
    "budget": "Budget",
}

# How each kind of preferences is named in a change's summary, and the stored model the
# fields given are merged into.
_PREFERENCE_LABELS: dict[str, str] = {
    "flights": "Flight preferences",
    "lodging": "Lodging preferences",
    "activities": "Activity preferences",
}
_PREFERENCE_MODELS: dict[str, type[BaseModel]] = {
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
    """The items as a command numbers them, from 1: the itinerary in time order, cancelled
    ones included so they can be restored. The prompt that shows the list uses this too."""
    return list(plan.items)


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


def _asked(command: Command, items: Sequence[ItineraryItem]) -> str:
    """What the command asks for, in words, even when it doesn't resolve."""
    match command:
        case RemoveItem():
            return f"Remove {_item_label(items, command.item)}"
        case UpdateItem():
            return f"Change {_item_label(items, command.item)}"
        case SetBasics():
            return f"Change the {_BASICS_LABELS[_basics_field(command)].lower()}"
        case SetPreferences():
            return f"Change the {_PREFERENCE_LABELS[_preferences_field(command)].lower()}"
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


def _remove(command: RemoveItem, items: Sequence[ItineraryItem]) -> _ItemStep:
    item = _item(items, command.item)
    if item.status == "cancelled":
        raise CommandError(f"“{item.title}” is already removed")
    asked = f"Remove “{item.title}”"
    return _ItemStep(
        name=command.type,
        asked=asked,
        done=f"Removed “{item.title}”",
        item_id=item.id,
        change=_item_change(item.id, {"status": "cancelled"}, asked),
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


def _basics_field(command: SetBasics) -> str:
    # The model validator guarantees exactly one.
    return next(name for name in BASICS_FIELDS if getattr(command, name) is not None)


def _set_basics(command: SetBasics, plan: PlanSnapshot, today: date) -> _BasicsStep:
    field = _basics_field(command)
    slot = BASICS_FIELDS[field]
    value = getattr(command, field).model_dump(mode="json")
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


def _preferences_field(command: SetPreferences) -> str:
    # The model validator guarantees exactly one.
    return next(name for name in PREFERENCE_FIELDS if getattr(command, name) is not None)


def _set_preferences(command: SetPreferences, plan: PlanSnapshot) -> _PreferencesStep:
    field = _preferences_field(command)
    edits: dict[str, JsonValue] = getattr(command, field).model_dump(mode="json", exclude_none=True)
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


def _resolve(
    command: Command, items: Sequence[ItineraryItem], plan: PlanSnapshot, today: date
) -> _Step:
    match command:
        case RemoveItem():
            return _remove(command, items)
        case UpdateItem():
            return _update(command, items)
        case SetBasics():
            return _set_basics(command, plan, today)
        case SetPreferences():
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


async def _check(
    ctx: tools.ToolContext, plan: PlanSnapshot, commands: Sequence[Command]
) -> _Checked:
    """Resolve and check every command (writes nothing). Item changes are checked with the
    earlier ones staged, so "remove the hotel, then move dinner" checks the move without
    the hotel; stage and preference changes are checked from where the earlier ones left
    the plan. Every command is checked even after one fails, so all the broken rules are
    reported."""
    items = numbered_items(plan)
    checked = _Checked(steps=[], rejected=[])
    staged: list[ProposedChange] = []
    touched: set[UUID] = set()
    at = plan  # the plan as the stage and preference changes so far leave it
    for command in commands:
        try:
            step = _resolve(command, items, at, ctx.today)
            if isinstance(step, _ItemStep):
                # A second edit would be checked against the item as stored, not as the
                # first edit leaves it, so both have to come as one.
                if step.item_id in touched:
                    raise CommandError("Change each item in one command")
                outcome = await tools.check_change(replace(ctx, staged=tuple(staged)), step.change)
                if not outcome.ok:
                    raise CommandError(outcome.summary)
                staged.append(step.change)
                touched.add(step.item_id)
            elif isinstance(step, _StageStep):
                at = at.model_copy(
                    update={"stage": step.stage, "flights_skipped": step.flights_skipped}
                )
            elif isinstance(step, _PreferencesStep):
                at = at.model_copy(update={"preferences": step.preferences})
        except CommandError as exc:
            checked.rejected.append(RejectedChange(summary=_asked(command, items), reason=str(exc)))
            continue
        checked.steps.append(step)
    return checked


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


async def _write(ctx: tools.ToolContext, plan: PlanSnapshot, step: _Step) -> None:
    """Write one checked command; `CommandError` when the plan changed since the check."""
    if isinstance(step, _ItemStep):
        outcome = await tools.apply_change(ctx, step.change)
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


async def check_and_apply(
    ctx: tools.ToolContext, plan: PlanSnapshot, commands: Sequence[Command]
) -> ChangesRecord:
    """Check every command against the plan's rules and, when all fit, write them in order.
    Each write bumps the plan's version, so open offers go stale and searches run again.
    Expected failures come back as `rejected`; they never raise."""
    if not commands:
        return ChangesRecord()
    if len(commands) > MAX_COMMANDS:
        reason = f"At most {MAX_COMMANDS} changes at a time"
        return ChangesRecord(rejected=[RejectedChange(summary="Change the trip", reason=reason)])
    checked = await _check(ctx, plan, commands)
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
    return ChangesRecord(applied=applied)


# ---------------------------------------------------------------------------
# Transcript text
# ---------------------------------------------------------------------------


def describe_changes(record: ChangesRecord) -> str:
    """The outcome as one transcript message: what was changed, then what wasn't and why."""
    lines = [f"{change.summary}." for change in record.applied]
    lines += [
        f"Couldn't {change.summary[0].lower()}{change.summary[1:]}: {change.reason}."
        for change in record.rejected
    ]
    return " ".join(lines)
