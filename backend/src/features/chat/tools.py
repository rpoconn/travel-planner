"""Plan changes: validating an itinerary change against the plan, and writing it.

A change is a named write (`add_flight`, `add_lodging`, `add_activity`, `add_transfer`,
`update_item`, `remove_item`) with JSON arguments, as a planning candidate carries it
(`planning.schemas.CandidateChange`). Each one validates its arguments with the same
Pydantic schemas the REST API uses, then runs the plan rules that the API alone doesn't:
no dates in the past, lodging is overnight, an item is in the timezone the traveller is in
then, flights follow on from each other, and the trip stays within its budget and limits.

Each change is split in two. `prepare` validates and writes nothing; `apply` writes a
prepared change. `check_change` only prepares; `apply_change` prepares again (the plan may
have changed since) and writes. Expected failures come back as an unsuccessful
`ToolOutcome` whose summary is shown to the user; they never raise.
"""

import logging
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.schemas import ProposedChange
from features.itinerary import budget as budget_rules
from features.itinerary import routes as route_rules
from features.itinerary import service as itinerary_service
from features.itinerary import zones as zone_rules
from features.itinerary.schemas import (
    ActivityItemCreate,
    ActivityItemPatch,
    FlightFields,
    FlightItemCreate,
    FlightItemPatch,
    ItemFields,
    ItemType,
    ItineraryItem,
    LodgingItemCreate,
    LodgingItemPatch,
    SpendingLimit,
    TransferItemCreate,
    TransferItemPatch,
)
from features.plans import service as plans_service
from features.plans.models import Plan
from features.plans.schemas import active_budget
from shared.domain import Cost, Money, Travelers
from shared.validation import summarize_validation_error

logger = logging.getLogger(__name__)

_CreateModel = (
    type[FlightItemCreate]
    | type[LodgingItemCreate]
    | type[ActivityItemCreate]
    | type[TransferItemCreate]
)
_PatchModel = (
    type[FlightItemPatch]
    | type[LodgingItemPatch]
    | type[ActivityItemPatch]
    | type[TransferItemPatch]
)

_CREATE_MODELS: dict[ItemType, _CreateModel] = {
    "flight": FlightItemCreate,
    "lodging": LodgingItemCreate,
    "activity": ActivityItemCreate,
    "transfer": TransferItemCreate,
}
_PATCH_MODELS: dict[ItemType, _PatchModel] = {
    "flight": FlightItemPatch,
    "lodging": LodgingItemPatch,
    "activity": ActivityItemPatch,
    "transfer": TransferItemPatch,
}


# ---------------------------------------------------------------------------
# Context / outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolContext:
    session: AsyncSession
    user_id: UUID
    plan_id: UUID
    today: date
    # Changes checked before this one that will be written with it (a pick's earlier
    # changes): they count towards the budget and the route too. Empty when applying,
    # where earlier changes are already in the plan.
    staged: Sequence[ProposedChange] = ()


@dataclass(frozen=True)
class Prepared:
    """A validated plan change that hasn't been written yet."""

    name: str  # the change, e.g. "add_flight"
    # The arguments after validation (nulls dropped, defaults left out).
    arguments: dict[str, Any]
    summary: str  # imperative, e.g. 'Add flight “SFO → NRT” on 2027-05-01 11:00'
    payload: Any  # whatever `apply` needs: the validated create model, patch, item id...


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    summary: str  # shown to the user, e.g. "Added lodging “Hotel Okura”"
    # Set when `check_change` validated the change.
    prepared: tuple[Prepared, ...] = ()


class ToolError(Exception):
    """An expected failure; the message is shown to the user."""


PrepareHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[Prepared]]
ApplyHandler = Callable[[ToolContext, Prepared], Awaitable[ToolOutcome]]


def _drop_nulls(arguments: dict[str, Any]) -> dict[str, Any]:
    # A null means "not provided", so it can't clear an optional field.
    return {key: value for key, value in arguments.items() if value is not None}


def _reject_past(day: date | None, field: str, today: date) -> None:
    # Trips are planned, not logged.
    if day is not None and day < today:
        raise ToolError(f"{field} {day.isoformat()} is in the past (today is {today.isoformat()})")


# ---------------------------------------------------------------------------
# Argument models
# ---------------------------------------------------------------------------


class UpdateItemArgs(BaseModel):
    item_id: UUID
    changes: dict[str, Any] = Field(
        description="Only the fields to change, with the item type's field names"
    )


class RemoveItemArgs(BaseModel):
    item_id: UUID


# ---------------------------------------------------------------------------
# Budget rules
# ---------------------------------------------------------------------------


def _field_list(names: Iterable[str]) -> str:
    return ", ".join(name.replace("_", " ") for name in names)


def _projected_costs(
    ctx: ToolContext, items: Sequence[ItineraryItem], name: str, arguments: dict[str, Any]
) -> dict[str, Cost | None]:
    """Each active item's cost, by id, after the staged changes. The change being prepared
    (`name`, `arguments`) is skipped if it's already staged."""
    costs: dict[str, Cost | None] = {
        str(item.id): item.cost for item in items if item.status != "cancelled"
    }
    for index, change in enumerate(ctx.staged):
        if change.name == name and change.arguments == arguments:
            continue
        args = change.arguments
        if change.name.startswith("add_"):
            cost = args.get("cost")
            costs[f"staged-{index}"] = Cost.model_validate(cost) if cost is not None else None
        elif change.name == "update_item":
            changes = args["changes"]
            if changes.get("status") == "cancelled":
                costs.pop(args["item_id"], None)
            elif "cost" in changes:
                costs[args["item_id"]] = Cost.model_validate(changes["cost"])
        elif change.name == "remove_item":
            costs.pop(args["item_id"], None)
    return costs


async def _check_budget(
    ctx: ToolContext,
    plan: Plan,
    item: ItemFields,
    name: str,
    arguments: dict[str, Any],
    item_id: UUID | None = None,
) -> None:
    """Raise a ToolError if `item` (new, or item `item_id` as it would be after an update)
    breaks the plan's budget rules (see features.itinerary.budget)."""
    budget = active_budget(Money.model_validate(plan.budget) if plan.budget else None)
    limits = [SpendingLimit.model_validate(limit) for limit in plan.limits]
    if item.status == "cancelled" or not budget_rules.needs_cost(item, budget, limits):
        return
    error = budget_rules.cost_error(item, budget)
    if error is not None:
        raise ToolError(error)
    travelers = Travelers.model_validate(plan.travelers) if plan.travelers else None
    error = budget_rules.limit_error(item, limits, travelers)
    if error is not None:
        raise ToolError(error)
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    costs = _projected_costs(ctx, items, name, arguments)
    costs[str(item_id) if item_id is not None else "new"] = item.cost
    error = budget_rules.total_error(budget, costs.values())
    if error is not None:
        raise ToolError(f"With {item.title} the trip {error}")


# ---------------------------------------------------------------------------
# Timezone and route checks
# ---------------------------------------------------------------------------


def _staged_items(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> list[ItemFields]:
    """Items the staged changes add, so a hotel staged right after its flight is checked
    against that flight. The change being prepared is skipped if it's already staged.
    Staged updates and removals aren't projected: the timeline flags what slips through."""
    items: list[ItemFields] = []
    for change in ctx.staged:
        if change.name == name and change.arguments == arguments:
            continue
        item_type = change.name.removeprefix("add_")
        # `item_type` is a plain str here (not every change is an add), hence the ignore.
        model = _CREATE_MODELS.get(item_type)  # type: ignore[call-overload]
        if model is None:
            continue
        try:
            items.append(model.model_validate({**change.arguments, "type": item_type}))
        except ValidationError:
            # Staged arguments were validated when staged; skip rather than fail this call.
            logger.warning("Staged %s no longer validates", change.name)
    return items


async def _check_zone(
    ctx: ToolContext,
    item: ItemFields,
    name: str,
    arguments: dict[str, Any],
    item_id: UUID | None = None,
) -> None:
    """Raise a ToolError if `item` (new, or item `item_id` as it would be after an update)
    isn't in the timezone the traveller is in then (see features.itinerary.zones)."""
    if isinstance(item, FlightFields):
        return
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    others = [other for other in items if other.id != item_id]
    result = zone_rules.zone_error(item, [*others, *_staged_items(ctx, name, arguments)])
    if result is not None:
        problem, _expected = result
        raise ToolError(problem)


async def _check_route(
    ctx: ToolContext,
    flight: FlightFields,
    name: str,
    arguments: dict[str, Any],
    item_id: UUID | None = None,
) -> None:
    """Raise a ToolError if `flight` (new, or flight `item_id` as it would be after an
    update) overlaps another flight or breaks the route (see features.itinerary.routes).
    Flights the staged changes remove don't count, so a swap goes through."""
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    removed = {str(c.arguments.get("item_id")) for c in ctx.staged if c.name == "remove_item"}
    others = [other for other in items if other.id != item_id and str(other.id) not in removed]
    problem = route_rules.route_problem(flight, [*others, *_staged_items(ctx, name, arguments)])
    if problem is not None:
        raise ToolError(problem.message)


# ---------------------------------------------------------------------------
# Overnight stays
# ---------------------------------------------------------------------------

# A stay shorter than this isn't a night's sleep, even when it crosses midnight.
_MIN_STAY = timedelta(hours=8)

_NOT_OVERNIGHT = (
    "lodging is for overnight stays (check-out on a later day, at least 8 hours after "
    "check-in), but this one is {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M}"
)


def _check_overnight(item: ItemFields) -> None:
    """Raise a ToolError if `item` is lodging that isn't an overnight stay. Times are
    zone-aware (`ItemFields._normalize_times`), so the dates are the local ones."""
    if not isinstance(item, LodgingItemCreate):
        return
    if item.end.date() > item.start.date() and item.end - item.start >= _MIN_STAY:
        return
    raise ToolError(_NOT_OVERNIGHT.format(start=item.start, end=item.end))


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _prepare_add(item_type: ItemType) -> PrepareHandler:
    model = _CREATE_MODELS[item_type]

    async def prepare(ctx: ToolContext, arguments: dict[str, Any]) -> Prepared:
        # `type` is implied by the change's name; set it here.
        data = model.model_validate({**_drop_nulls(arguments), "type": item_type})
        _check_overnight(data)
        _reject_past(data.start.date(), "start", ctx.today)
        # Also fails a deleted plan here rather than when writing.
        plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
        # Defaults are left out to keep the stored change small; they come back on re-prepare.
        normalised = data.model_dump(mode="json", exclude_defaults=True, exclude={"type"})
        await _check_zone(ctx, data, f"add_{item_type}", normalised)
        if isinstance(data, FlightFields):
            await _check_route(ctx, data, f"add_{item_type}", normalised)
        await _check_budget(ctx, plan, data, f"add_{item_type}", normalised)
        return Prepared(
            name=f"add_{item_type}",
            arguments=normalised,
            summary=f"Add {item_type} “{data.title}” on {data.start:%Y-%m-%d %H:%M}",
            payload=data,
        )

    return prepare


async def _apply_add(ctx: ToolContext, prepared: Prepared) -> ToolOutcome:
    item = await itinerary_service.add_item(
        ctx.session, ctx.user_id, ctx.plan_id, prepared.payload, created_by="assistant"
    )
    return ToolOutcome(ok=True, summary=f"Added {item.type} “{item.title}”")


# Item fields whose change re-runs the budget checks.
_BUDGET_FIELDS = frozenset({"cost", "category", "status"})
_TIME_FIELDS = frozenset({"start", "end", "timezone", "end_timezone"})
# Item fields whose change re-runs the route check (a flight's times, airports, status).
_ROUTE_FIELDS = _TIME_FIELDS | {"from_place", "to_place", "status"}


@dataclass(frozen=True)
class _ItemChange:
    """The `update_item` / `remove_item` payload."""

    item_id: UUID
    title: str
    type: ItemType
    patch: Any = None  # the item type's *ItemPatch (update only)


async def _prepare_update(ctx: ToolContext, arguments: dict[str, Any]) -> Prepared:
    args = UpdateItemArgs.model_validate(arguments)
    current = await itinerary_service.get_item(ctx.session, ctx.user_id, ctx.plan_id, args.item_id)
    changes = _drop_nulls(args.changes)
    patch_model = _PATCH_MODELS[current.type]
    unknown = sorted(set(changes) - set(patch_model.model_fields) - {"type"})
    if unknown:
        raise ToolError(f"A {current.type} has no field(s): {', '.join(unknown)}")
    if not changes:
        raise ToolError("No changes were given")

    patch = patch_model.model_validate({**changes, "type": current.type})
    _reject_past(patch.start.date() if patch.start else None, "start", ctx.today)
    try:
        # The merged item is validated as a whole (end after start, airports...).
        merged = await itinerary_service.check_update(
            ctx.session, ctx.user_id, ctx.plan_id, args.item_id, patch
        )
    except itinerary_service.InvalidItemError as exc:
        raise ToolError(str(exc)) from exc
    normalised = patch.model_dump(mode="json", exclude_unset=True, exclude={"type"})
    arguments = {"item_id": str(args.item_id), "changes": normalised}
    # Only changes that affect what the item costs are checked: moving an item that had no
    # cost before the budget was set is fine (the timeline flags it instead).
    if patch.model_fields_set & _TIME_FIELDS:
        _check_overnight(merged)
        await _check_zone(ctx, merged, "update_item", arguments, item_id=args.item_id)
    if isinstance(merged, FlightFields) and patch.model_fields_set & _ROUTE_FIELDS:
        await _check_route(ctx, merged, "update_item", arguments, item_id=args.item_id)
    if patch.model_fields_set & _BUDGET_FIELDS:
        plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
        await _check_budget(ctx, plan, merged, "update_item", arguments, item_id=args.item_id)
    return Prepared(
        name="update_item",
        arguments=arguments,
        summary=f"Change “{current.title}”: {_field_list(sorted(normalised))}",
        payload=_ItemChange(args.item_id, current.title, current.type, patch),
    )


async def _apply_update(ctx: ToolContext, prepared: Prepared) -> ToolOutcome:
    change: _ItemChange = prepared.payload
    try:
        item = await itinerary_service.update_item(
            ctx.session, ctx.user_id, ctx.plan_id, change.item_id, change.patch
        )
    except itinerary_service.InvalidItemError as exc:
        raise ToolError(str(exc)) from exc
    return ToolOutcome(ok=True, summary=f"Updated “{item.title}”")


async def _prepare_remove(ctx: ToolContext, arguments: dict[str, Any]) -> Prepared:
    args = RemoveItemArgs.model_validate(arguments)
    item = await itinerary_service.get_item(ctx.session, ctx.user_id, ctx.plan_id, args.item_id)
    return Prepared(
        name="remove_item",
        arguments={"item_id": str(args.item_id)},
        summary=f"Remove “{item.title}”",
        payload=_ItemChange(args.item_id, item.title, item.type),
    )


async def _apply_remove(ctx: ToolContext, prepared: Prepared) -> ToolOutcome:
    change: _ItemChange = prepared.payload
    await itinerary_service.remove_item(ctx.session, ctx.user_id, ctx.plan_id, change.item_id)
    return ToolOutcome(ok=True, summary=f"Removed “{change.title}”")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WriteTool:
    """A plan change: validated by `prepare`, written by `apply`."""

    prepare: PrepareHandler
    apply: ApplyHandler


TOOLS: dict[str, WriteTool] = {
    **{f"add_{kind}": WriteTool(_prepare_add(kind), _apply_add) for kind in _CREATE_MODELS},
    "update_item": WriteTool(_prepare_update, _apply_update),
    "remove_item": WriteTool(_prepare_remove, _apply_remove),
}


# ---------------------------------------------------------------------------
# Checking and applying
# ---------------------------------------------------------------------------


def _failure(message: str) -> ToolOutcome:
    return ToolOutcome(ok=False, summary=message)


async def _attempt(name: str, action: Callable[[], Awaitable[ToolOutcome]]) -> ToolOutcome:
    """Run `action`, turning the expected failures into an unsuccessful outcome."""
    try:
        return await action()
    except ValidationError as exc:
        return _failure(f"Invalid arguments for {name}: {summarize_validation_error(exc)}")
    except ToolError as exc:
        return _failure(str(exc))
    except itinerary_service.ItemNotFoundError:
        return _failure("That item is no longer in this plan")
    except plans_service.PlanNotFoundError:
        return _failure("This plan no longer exists")


def _write_tool(name: str) -> WriteTool:
    tool = TOOLS.get(name)
    if tool is None:
        # Only reachable with a corrupted stored change: candidates are built in code.
        raise ToolError(f"{name!r} is not a plan change")
    return tool


async def check_change(ctx: ToolContext, change: ProposedChange) -> ToolOutcome:
    """Whether a change validates against the plan (writes nothing)."""

    async def action() -> ToolOutcome:
        prepared = await _write_tool(change.name).prepare(ctx, change.arguments)
        return ToolOutcome(ok=True, summary=prepared.summary, prepared=(prepared,))

    return await _attempt(change.name, action)


async def apply_change(ctx: ToolContext, change: ProposedChange) -> ToolOutcome:
    """Prepare a change again (the plan may have moved on) and write it."""

    async def action() -> ToolOutcome:
        tool = _write_tool(change.name)
        return await tool.apply(ctx, await tool.prepare(ctx, change.arguments))

    outcome = await _attempt(change.name, action)
    log = logger.info if outcome.ok else logger.warning
    log("Change %s on plan %s: %s", change.name, ctx.plan_id, outcome.summary)
    return outcome
