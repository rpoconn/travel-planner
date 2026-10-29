"""Tools the planning agent can call, and the executor that runs them.

Each tool validates the model's arguments with the same Pydantic schemas the REST API uses,
so the model can't write anything the API wouldn't accept. Expected failures (bad
arguments, unknown ids) come back as an unsuccessful `ToolOutcome` whose text is fed to the
model so it can correct itself; they never raise.

Item creation is split into one tool per item type (rather than one `add_item` taking a
discriminated union): a flat schema per type is much easier for a small local model.

Write tools are split in two. `prepare` validates a call against the plan (same models,
past-date check, item lookup, flight direction) and writes nothing; `apply` writes a
prepared change. The agent only prepares: each prepared change is staged into a proposal
the user approves or denies, and approving runs `apply_change`, which prepares again (the
plan may have changed since) before writing.

`add_round_trip` is a shortcut, not a change of its own: it prepares the flight out and
the flight home as two `add_flight` changes, so a trip gets both legs on one card by
default. `add_flight` stays for the legs of a multi-city trip, connections and one-ways.

`web_search` is the only tool that reads instead of writing: it shapes the query with the
plan's destination/dates and returns the top results as text for the model to cite.
"""

import logging
import re
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.prompts import RETURN_FLIGHT_NUDGE
from features.chat.schemas import ProposedChange
from features.itinerary import budget as budget_rules
from features.itinerary import service as itinerary_service
from features.itinerary import zones as zone_rules
from features.itinerary.airports import same_area
from features.itinerary.schemas import (
    ActivityItemCreate,
    ActivityItemPatch,
    FlightDirection,
    FlightFields,
    FlightItem,
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
from features.plans.schemas import PlanUpdate, TravelPlan
from shared.domain import Cost, Money, Place, Travelers
from shared.llm.schema import inline_schema
from shared.llm.types import LLMToolCall, ToolSpec
from shared.search.query import shape_query
from shared.search.types import (
    SearchContext,
    SearchError,
    SearchKind,
    SearchProvider,
    SearchResult,
)
from shared.validation import summarize_validation_error

logger = logging.getLogger(__name__)

# What the model is told after staging a change. Small models tend to call the same tool
# again when a result doesn't say it worked, hence the explicit "don't repeat".
STAGED_CONTENT = (
    "Staged for the user's approval: {summary}. Nothing is applied until they approve the "
    "card. Do not call this tool again with the same change. When you have staged "
    "everything for this step, stop calling tools and briefly summarise what you proposed "
    "plus any questions you still have."
)

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
    search: SearchProvider
    today: date
    # This turn's proposal card so far: its changes count towards the budget too. Empty
    # when an approved card is applied, where earlier changes are already in the plan.
    staged: Sequence[ProposedChange] = ()


@dataclass(frozen=True)
class Prepared:
    """A validated plan change that hasn't been written yet."""

    name: str  # the tool
    # The call's arguments after validation (nulls dropped, defaults left out, a flight's
    # direction filled in): what is stored in the proposal and prepared again on approval.
    arguments: dict[str, Any]
    summary: str  # imperative, e.g. 'Add flight “SFO → NRT” on 2027-05-01 11:00'
    payload: Any  # whatever `apply` needs: the validated create model, patch, item id...


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    content: str  # fed back to the model
    summary: str  # shown to the user
    plan_changed: bool = False
    # Set when a write tool's call validated: one change, or two for add_round_trip.
    prepared: tuple[Prepared, ...] = ()


class ToolError(Exception):
    """An expected failure; the message is shown to both the model and the user."""


RunHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutcome]]
PrepareHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[Prepared]]
BundleHandler = Callable[[ToolContext, dict[str, Any]], Awaitable[tuple[Prepared, ...]]]
ApplyHandler = Callable[[ToolContext, Prepared], Awaitable[ToolOutcome]]


# Keys of a JSON schema. A value made only of these is the parameter's schema echoed back
# (`"budget": {"type": "object", "description": "Total trip budget"}`), not a value.
_SCHEMA_KEYWORDS = frozenset({"type", "description", "title", "properties", "required"})


def _is_schema_echo(value: Any) -> bool:
    return isinstance(value, dict) and "type" in value and set(value) <= _SCHEMA_KEYWORDS


def _drop_nulls(arguments: dict[str, Any]) -> dict[str, Any]:
    # Small models often send every parameter, with null (or the parameter's own schema)
    # for "leave unchanged". Treat both as "not provided" (the agent can't clear optional
    # fields, which is fine).
    return {
        key: value
        for key, value in arguments.items()
        if value is not None and not _is_schema_echo(value)
    }


def _next_occurrence(day: date, today: date) -> date:
    """The first date on or after `today` with `day`'s month and day."""
    for year in range(today.year, today.year + 5):
        try:
            candidate = day.replace(year=year)
        except ValueError:  # Feb 29 in a non-leap year
            continue
        if candidate >= today:
            return candidate
    return day  # unreachable: a leap year falls within any 5 years


def _reject_past(day: date | None, field: str, today: date) -> None:
    # Small models often take the year from their training data ("in May" -> last May), so
    # past dates are refused with the year to use instead. Trips are planned, not logged.
    if day is None or day >= today:
        return
    suggestion = _next_occurrence(day, today)
    raise ToolError(
        f"{field} {day.isoformat()} is in the past (today is {today.isoformat()}). A month "
        f"or date without a year means its next occurrence, e.g. {suggestion.isoformat()}"
    )


# ---------------------------------------------------------------------------
# Argument models
# ---------------------------------------------------------------------------


class SetPlanDetailsArgs(BaseModel):
    title: str | None = Field(default=None, description="Short trip name")
    origin: Place | None = Field(default=None, description="Where the travelers start from")
    destination: Place | None = Field(default=None, description="Main destination")
    start_date: str | None = Field(default=None, description="First day, YYYY-MM-DD")
    end_date: str | None = Field(default=None, description="Last day, YYYY-MM-DD")
    travelers: Travelers | None = None
    budget: Money | None = Field(default=None, description="Total trip budget")
    limits: list[SpendingLimit] | None = Field(
        default=None,
        description=(
            "Per-person caps the user gave for one activity of a category, e.g. "
            '[{"category": "food", "max_per_person": {"amount": 200, "currency": "USD"}}] '
            'for "dinners under $200 a person". Send the whole list: it replaces the '
            "current one"
        ),
    )


class RoundTripArgs(BaseModel):
    # Each leg is an add_flight call's arguments, validated when that leg is prepared.
    # `return` is a keyword in Python, hence the alias.
    model_config = ConfigDict(populate_by_name=True)

    outbound: dict[str, Any]
    return_flight: dict[str, Any] = Field(alias="return")


class UpdateItemArgs(BaseModel):
    item_id: UUID
    changes: dict[str, Any] = Field(
        description=(
            "Only the fields to change, using the same field names as the add_* tools, "
            'e.g. {"start": "2027-05-03T15:00:00", "end": "2027-05-07T11:00:00"}'
        )
    )


class RemoveItemArgs(BaseModel):
    item_id: UUID


class WebSearchArgs(BaseModel):
    query: str = Field(
        min_length=2,
        max_length=300,
        description='What to look for, e.g. "ryokan with private onsen in Hakone"',
    )
    kind: SearchKind = Field(
        default="general",
        description="flights, hotels, activities, transport (getting around), or general",
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _field_list(names: Iterable[str]) -> str:
    return ", ".join(name.replace("_", " ") for name in names)


async def _prepare_plan_details(ctx: ToolContext, arguments: dict[str, Any]) -> Prepared:
    args = SetPlanDetailsArgs.model_validate(_drop_nulls(arguments))
    fields = args.model_dump(mode="json", exclude_unset=True)
    if not fields:
        raise ToolError("No plan details were given")
    # Dates are strings in the tool schema (simpler for the model); PlanUpdate parses them.
    patch = PlanUpdate.model_validate(fields)
    _reject_past(patch.start_date, "start_date", ctx.today)
    _reject_past(patch.end_date, "end_date", ctx.today)
    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    try:
        plans_service.check_update(plan, patch)
    except plans_service.InvalidPlanError as exc:
        raise ToolError(str(exc)) from exc
    return Prepared(
        name="set_plan_details",
        arguments=fields,
        summary=f"Set trip {_field_list(fields)}",
        payload=patch,
    )


async def _apply_plan_details(ctx: ToolContext, prepared: Prepared) -> ToolOutcome:
    patch: PlanUpdate = prepared.payload
    try:
        await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, patch)
    except plans_service.InvalidPlanError as exc:
        raise ToolError(str(exc)) from exc
    changed = _field_list(prepared.arguments)
    return ToolOutcome(
        ok=True,
        content=f"Plan updated ({changed}).",
        summary=f"Updated trip {changed}",
        plan_changed=True,
    )


# Appended to a budget rule's message to tell the model what to do about it.
_NO_COST_HELP = (
    "This plan has a budget or spending limits, so every item needs a cost in the budget's "
    'currency: the price from the user or a web_search result (basis "exact"), or your best '
    'estimate (basis "estimate"). Use 0 for something free'
)
_OVER_LIMIT_HELP = "Choose a cheaper option, or ask the user whether to raise that limit"
_OVER_BUDGET_HELP = (
    "Choose a cheaper option, or ask the user whether to raise the budget. Do not lower the "
    "price unless you found a cheaper one"
)


def _projected_costs(
    ctx: ToolContext, items: Sequence[ItineraryItem], name: str, arguments: dict[str, Any]
) -> dict[str, Cost | None]:
    """Each active item's cost, by id, after this turn's staged changes. The change being
    prepared (`name`, `arguments`) is skipped if it's already staged: models re-send them."""
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
    budget = Money.model_validate(plan.budget) if plan.budget else None
    limits = [SpendingLimit.model_validate(limit) for limit in plan.limits]
    if item.status == "cancelled" or not budget_rules.needs_cost(item, budget, limits):
        return
    error = budget_rules.cost_error(item, budget)
    if error is not None:
        raise ToolError(f"{error}. {_NO_COST_HELP}")
    travelers = Travelers.model_validate(plan.travelers) if plan.travelers else None
    error = budget_rules.limit_error(item, limits, travelers)
    if error is not None:
        raise ToolError(f"{error}. {_OVER_LIMIT_HELP}")
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    costs = _projected_costs(ctx, items, name, arguments)
    costs[str(item_id) if item_id is not None else "new"] = item.cost
    error = budget_rules.total_error(budget, costs.values())
    if error is not None:
        raise ToolError(f"With {item.title} the trip {error}. {_OVER_BUDGET_HELP}")


# ---------------------------------------------------------------------------
# Timezone check
# ---------------------------------------------------------------------------

_WRONG_ZONE_HELP = (
    "Times are local to where the traveller is: give start and end as local times in "
    "{zone} and set timezone to {zone}"
)


def _staged_items(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> list[ItemFields]:
    """Items this turn's proposal adds, so a hotel staged right after its flight is checked
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
        problem, expected = result
        raise ToolError(f"{problem}. {_WRONG_ZONE_HELP.format(zone=expected)}")


def _prepare_add(item_type: ItemType) -> PrepareHandler:
    model = _CREATE_MODELS[item_type]

    async def prepare(ctx: ToolContext, arguments: dict[str, Any]) -> Prepared:
        # `type` is implied by the tool, so it's not in the schema; set it here.
        fields = {**_drop_nulls(arguments), "type": item_type}
        # Small models often leave out a flight's `direction`, or label the trip home
        # "outbound". Validate with a placeholder when it's missing, then reconcile the
        # label with the plan rather than trusting it or failing the call.
        has_direction = "direction" in fields
        if item_type == "flight" and not has_direction:
            fields["direction"] = "internal"
        data = model.model_validate(fields)
        _reject_past(data.start.date(), "start", ctx.today)
        # Also fails a deleted plan here rather than on approval.
        plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
        if isinstance(data, FlightItemCreate):
            given = data.direction if has_direction else None
            items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
            others = [item for item in items if isinstance(item, FlightItem)]
            direction = _flight_direction(data, given, plan, others)
            if direction != given:
                logger.info("Flight %r direction %s -> %s", data.title, given, direction)
            data = data.model_copy(update={"direction": direction})
        # Defaults are left out to keep the proposal small; they come back on re-prepare.
        normalised = data.model_dump(mode="json", exclude_defaults=True, exclude={"type"})
        await _check_zone(ctx, data, f"add_{item_type}", normalised)
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
    return ToolOutcome(
        ok=True,
        content=f'Added {item.type} "{item.title}" with id {item.id}.',
        summary=f"Added {item.type} “{item.title}”",
        plan_changed=True,
    )


_prepare_flight = _prepare_add("flight")


async def _prepare_round_trip(ctx: ToolContext, arguments: dict[str, Any]) -> tuple[Prepared, ...]:
    args = RoundTripArgs.model_validate(_drop_nulls(arguments))
    outbound = await _prepare_flight(ctx, {**args.outbound, "direction": "outbound"})
    # The flight home is checked with the flight out already on the card, so the pair has
    # to fit the budget together. The call id is a placeholder: this change isn't kept.
    with_outbound = replace(
        ctx,
        staged=[
            *ctx.staged,
            ProposedChange(
                call_id="round-trip",
                name=outbound.name,
                arguments=outbound.arguments,
                summary=outbound.summary,
            ),
        ],
    )
    home = await _prepare_flight(with_outbound, {**args.return_flight, "direction": "return"})
    # Aware datetimes (each in its own zone), so they compare as instants.
    if home.payload.start <= outbound.payload.end:
        raise ToolError(
            "The return flight must leave after the outbound flight lands; usually it "
            "leaves on the trip's last day"
        )
    return outbound, home


# ---------------------------------------------------------------------------
# Flight direction
# ---------------------------------------------------------------------------


def _mentions(haystack: str | None, needle: str | None) -> bool:
    """`needle` appears as whole words in `haystack` ("Philadelphia" in "Philadelphia
    International Airport"), ignoring case."""
    if not haystack or not needle:
        return False
    pattern = rf"\b{re.escape(needle.casefold())}\b"
    return re.search(pattern, haystack.casefold()) is not None


def _is_origin(place: Place, origin: Place | None) -> bool:
    if origin is None:
        return False
    if place.iata and origin.iata:
        return same_area(place.iata, origin.iata)
    # The origin is usually a city ("Philadelphia") and the flight an airport.
    return any(
        _mentions(haystack, needle)
        for needle in (origin.name, origin.city)
        for haystack in (place.name, place.city)
    )


def _same_airport(a: Place, b: Place) -> bool:
    return a.iata is not None and b.iata is not None and same_area(a.iata, b.iata)


def _direction_from_origin(flight: FlightItemCreate, plan: Plan) -> FlightDirection | None:
    origin = Place.model_validate(plan.origin) if plan.origin else None
    if _is_origin(flight.from_place, origin):
        return "outbound"
    if _is_origin(flight.to_place, origin):
        return "return"
    return None


def _direction_from_dates(flight: FlightItemCreate, plan: Plan) -> FlightDirection | None:
    # On (or outside) the trip's first day is outbound, on its last day a return. A one-day
    # trip is both, so the date says nothing.
    day = flight.start.date()
    first = plan.start_date is not None and day <= plan.start_date
    last = plan.end_date is not None and day >= plan.end_date
    if first == last:
        return None
    return "outbound" if first else "return"


def _direction_from_other_flights(
    flight: FlightItemCreate, others: list[FlightItem]
) -> FlightDirection | None:
    # Mirror the flights already planned: landing where the outbound flight left from is
    # the trip home, and leaving from where the return lands is the way out.
    for other in others:
        if other.direction == "outbound" and _same_airport(flight.to_place, other.from_place):
            return "return"
        if other.direction == "return" and _same_airport(flight.from_place, other.to_place):
            return "outbound"
    return None


def _flight_direction(
    flight: FlightItemCreate,
    given: FlightDirection | None,
    plan: Plan,
    others: list[FlightItem],
) -> FlightDirection:
    """The direction to store, strongest evidence first.

    1. Leaving / landing at the trip's origin, whatever the model said.
    2. The trip's first / last day, unless the model said "internal" (a domestic hop on
       arrival day is legitimate; "outbound" on the last day is a mislabelled trip home).
    3. Mirroring the other flights in the plan.
    4. The model's label, else "internal".
    """
    by_origin = _direction_from_origin(flight, plan)
    if by_origin is not None:
        return by_origin
    by_date = _direction_from_dates(flight, plan)
    if by_date is not None and given != "internal":
        return by_date
    mirrored = _direction_from_other_flights(flight, others)
    if mirrored is not None:
        return mirrored
    return given or "internal"


# ---------------------------------------------------------------------------
# Return flight
# ---------------------------------------------------------------------------


def _flights_after(
    items: Sequence[ItineraryItem], staged: Sequence[ProposedChange]
) -> list[FlightFields]:
    """The plan's active flights once `staged` is applied: added, removed, cancelled or
    relabelled ones (no other update changes whether a flight goes out or home)."""
    flights: dict[str, FlightFields] = {
        str(item.id): item
        for item in items
        if isinstance(item, FlightItem) and item.status != "cancelled"
    }
    for index, change in enumerate(staged):
        args = change.arguments
        if change.name == "add_flight":
            try:
                flights[f"staged-{index}"] = FlightItemCreate.model_validate(
                    {**args, "type": "flight"}
                )
            except ValidationError:
                # Validated when staged; skip rather than fail the turn.
                logger.warning("Staged add_flight no longer validates")
        elif change.name == "remove_item":
            flights.pop(args["item_id"], None)
        elif change.name == "update_item" and args["item_id"] in flights:
            changes = args["changes"]
            if changes.get("status") == "cancelled":
                flights.pop(args["item_id"])
            elif "direction" in changes:
                flight = flights[args["item_id"]]
                flights[args["item_id"]] = flight.model_copy(
                    update={"direction": changes["direction"]}
                )
    return list(flights.values())


def _outbound_without_return(flights: Sequence[FlightFields]) -> list[FlightFields]:
    """The outbound flights (in order) when no flight goes home, else []."""
    if any(flight.direction == "return" for flight in flights):
        return []
    return sorted((f for f in flights if f.direction == "outbound"), key=lambda f: f.start)


def _airport(place: Place) -> str:
    return place.iata or place.name


def return_flight_nudge(plan: TravelPlan, staged: Sequence[ProposedChange]) -> str | None:
    """What to tell the model when this turn's proposal leaves the trip with a flight out
    but none home, else None. Only a gap this turn opens counts: one already in the plan
    was raised when it was proposed, and repeating it every turn would derail the talk
    about hotels or dinners."""
    if _outbound_without_return(_flights_after(plan.items, ())):
        return None
    outbound = _outbound_without_return(_flights_after(plan.items, staged))
    if not outbound:
        return None
    # A connection is several outbound legs: home is where the first leaves, the
    # destination where the last lands.
    return RETURN_FLIGHT_NUDGE.format(
        away=_airport(outbound[-1].to_place),
        home=_airport(outbound[0].from_place),
        when=f" on {plan.end_date.isoformat()}" if plan.end_date else "",
    )


# Item fields whose change re-runs the budget checks.
_BUDGET_FIELDS = frozenset({"cost", "category", "status"})
_TIME_FIELDS = frozenset({"start", "end", "timezone", "end_timezone"})


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
        await _check_zone(ctx, merged, "update_item", arguments, item_id=args.item_id)
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
    fields = ", ".join(sorted(prepared.arguments["changes"]))
    return ToolOutcome(
        ok=True,
        content=f'Updated {item.type} "{item.title}" ({fields}).',
        summary=f"Updated “{item.title}”",
        plan_changed=True,
    )


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
    return ToolOutcome(
        ok=True,
        content=f'Removed {change.type} "{change.title}".',
        summary=f"Removed “{change.title}”",
        plan_changed=True,
    )


def _search_context(plan: Plan) -> SearchContext:
    return SearchContext(
        origin=Place.model_validate(plan.origin) if plan.origin else None,
        destination=Place.model_validate(plan.destination) if plan.destination else None,
        travel_date=plan.start_date,
    )


def _format_results(query: str, results: list[SearchResult]) -> str:
    lines = [f'Web results for "{query}":']
    for number, result in enumerate(results, start=1):
        lines.append(f"{number}. {result.title}\n   URL: {result.url}")
        if result.snippet:
            lines.append(f"   {result.snippet}")
    lines.append(
        "Prices and schedules in results can be out of date. When you add an item based on "
        "a result, set its source_url to that result's URL."
    )
    return "\n".join(lines)


async def _web_search(ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    args = WebSearchArgs.model_validate(_drop_nulls(arguments))
    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    query = shape_query(args.query, args.kind, _search_context(plan))
    try:
        results = await ctx.search.search(query, args.kind)
    except SearchError as exc:
        # Built here rather than via ToolError: the model gets extra guidance the user
        # doesn't need to see.
        return ToolOutcome(
            ok=False,
            content=(
                f"Error: {exc} Don't repeat this search. If web search is unavailable, "
                "answer from general knowledge and say that details need checking."
            ),
            summary=str(exc),
        )
    if not results:
        return ToolOutcome(
            ok=True,
            content=f'No web results for "{query}". Try fewer or different words.',
            summary=f"No web results for “{query}”",
        )
    return ToolOutcome(
        ok=True,
        content=_format_results(query, results),
        summary=f"Searched the web for “{query}”",
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReadTool:
    """A tool that only reads (web_search): it runs as soon as the model calls it."""

    spec: ToolSpec
    run: RunHandler


@dataclass(frozen=True)
class WriteTool:
    """A tool that changes the plan: validated by `prepare`, written by `apply`."""

    spec: ToolSpec
    prepare: PrepareHandler
    apply: ApplyHandler


@dataclass(frozen=True)
class BundleTool:
    """A tool that stages several changes of other write tools (add_round_trip: two
    add_flight). Proposals only ever hold those changes, so it has no `apply`."""

    spec: ToolSpec
    prepare: BundleHandler


Tool = ReadTool | WriteTool | BundleTool


# Fields the model shouldn't fill, so they're left out of the prompt: it can't know
# coordinates, status should stay "proposed" (update_item can change it later), and
# pickup/dropoff duplicate from/to. `type` is implied by the add_* tool. The models still
# accept these if sent; they just aren't advertised.
_HIDDEN_PLACE_FIELDS = frozenset({"lat", "lng", "country"})
_HIDDEN_ITEM_FIELDS = frozenset({"type", "status", "pickup_location", "dropoff_location"})
_HIDDEN_BY_TYPE: dict[ItemType, frozenset[str]] = {
    "lodging": frozenset({"end_timezone"}),
    "activity": frozenset({"end_timezone"}),
}


def _hide_fields(schema: dict[str, Any], names: frozenset[str]) -> dict[str, Any]:
    """`schema` without the `names` properties, and with places slimmed at any depth."""
    if isinstance(schema.get("items"), dict):
        schema = {**schema, "items": _hide_fields(schema["items"], frozenset())}
    properties: dict[str, Any] | None = schema.get("properties")
    if properties is None:
        return schema
    # A Place is the only object with an `iata` property.
    if "iata" in properties:
        names = names | _HIDDEN_PLACE_FIELDS
    slimmed = {
        name: _hide_fields(value, frozenset())
        for name, value in properties.items()
        if name not in names
    }
    required = [name for name in schema.get("required", []) if name not in names]
    hidden = {**schema, "properties": slimmed}
    if required:
        hidden["required"] = required
    else:
        hidden.pop("required", None)
    return hidden


def _item_parameters(item_type: ItemType) -> dict[str, Any]:
    hidden = _HIDDEN_ITEM_FIELDS | _HIDDEN_BY_TYPE.get(item_type, frozenset())
    return _hide_fields(inline_schema(_CREATE_MODELS[item_type]), hidden)


def _round_trip_parameters() -> dict[str, Any]:
    # Both legs are add_flight's parameters; the direction comes from the leg's key.
    leg = _hide_fields(_item_parameters("flight"), frozenset({"direction"}))
    return {
        "type": "object",
        "properties": {
            "outbound": {**leg, "description": "The flight from home to the destination"},
            "return": {**leg, "description": "The flight home, usually on the trip's last day"},
        },
        "required": ["outbound", "return"],
    }


def _add_tool(item_type: ItemType, description: str) -> WriteTool:
    return WriteTool(
        spec=ToolSpec(
            name=f"add_{item_type}",
            description=description,
            parameters=_item_parameters(item_type),
        ),
        prepare=_prepare_add(item_type),
        apply=_apply_add,
    )


_ALL_TOOLS: list[Tool] = [
    WriteTool(
        spec=ToolSpec(
            name="set_plan_details",
            description=(
                "Set trip-level details: title, origin, destination, start/end dates, "
                "travelers, budget. Send only the fields that change."
            ),
            parameters=_hide_fields(inline_schema(SetPlanDetailsArgs), frozenset()),
        ),
        prepare=_prepare_plan_details,
        apply=_apply_plan_details,
    ),
    BundleTool(
        spec=ToolSpec(
            name="add_round_trip",
            description=(
                "Add the flight out and the flight home together. Use this for a trip's "
                "flights unless the user asked for a one-way or multi-city trip. Each leg: "
                "from_place/to_place need IATA codes; timezone = departure airport's IANA "
                "zone, end_timezone = arrival airport's; end is the local arrival time "
                "there, often the next day; cost is that leg's total for everyone. With "
                "only a round-trip price, put it on the outbound leg and cost the return 0."
            ),
            parameters=_round_trip_parameters(),
        ),
        prepare=_prepare_round_trip,
    ),
    _add_tool(
        "flight",
        "Add one flight leg: for a multi-city trip, a connection, or a one-way trip the "
        "user asked for (otherwise use add_round_trip). from_place/to_place need IATA "
        "codes. timezone = departure airport's IANA zone, end_timezone = arrival "
        "airport's; end is the local arrival time there, often the next day. direction = "
        "outbound (leaving home), return (going home) or internal.",
    ),
    _add_tool(
        "lodging",
        "Add a hotel or other stay. start = check-in time, end = check-out time.",
    ),
    _add_tool(
        "activity",
        "Add an activity: sightseeing, a meal, a tour, a museum visit, etc.",
    ),
    _add_tool(
        "transfer",
        "Add ground/sea transport between two places: train, taxi, car rental, ferry... "
        "mode car_service is a booked private car with a driver (e.g. an airport pickup); "
        "car_rental is only a car the traveller drives themselves.",
    ),
    WriteTool(
        spec=ToolSpec(
            name="update_item",
            description="Change fields of an existing itinerary item, by id.",
            parameters=inline_schema(UpdateItemArgs),
        ),
        prepare=_prepare_update,
        apply=_apply_update,
    ),
    WriteTool(
        spec=ToolSpec(
            name="remove_item",
            description="Delete an itinerary item, by id.",
            parameters=inline_schema(RemoveItemArgs),
        ),
        prepare=_prepare_remove,
        apply=_apply_remove,
    ),
    ReadTool(
        spec=ToolSpec(
            name="web_search",
            description=(
                "Search the web for current options and facts: flights, hotels, "
                "activities, local transport, opening hours, prices. Use it before "
                "suggesting specific places or prices. Trip destination and dates are "
                "added to the query automatically."
            ),
            parameters=inline_schema(WebSearchArgs),
        ),
        run=_web_search,
    ),
]
TOOLS: dict[str, Tool] = {tool.spec.name: tool for tool in _ALL_TOOLS}

TOOL_SPECS: list[ToolSpec] = [tool.spec for tool in TOOLS.values()]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _failure(message: str) -> ToolOutcome:
    return ToolOutcome(ok=False, content=f"Error: {message}", summary=message)


def changes_plan(name: str) -> bool:
    """Whether `name` is a tool that writes to the plan (unknown tools count as writes)."""
    return not isinstance(TOOLS.get(name), ReadTool)


async def _attempt(name: str, action: Callable[[], Awaitable[ToolOutcome]]) -> ToolOutcome:
    """Run `action`, turning the expected failures into an unsuccessful outcome."""
    try:
        return await action()
    except ValidationError as exc:
        return _failure(f"Invalid arguments for {name}: {summarize_validation_error(exc)}")
    except ToolError as exc:
        return _failure(str(exc))
    except itinerary_service.ItemNotFoundError:
        return _failure("No item with that id in this plan; use an id from the plan JSON")
    except plans_service.PlanNotFoundError:
        return _failure("This plan no longer exists")


def _staged(*prepared: Prepared) -> ToolOutcome:
    summary = "; ".join(change.summary for change in prepared)
    return ToolOutcome(
        ok=True,
        content=STAGED_CONTENT.format(summary=summary),
        summary=f"Proposed: {summary}",
        prepared=prepared,
    )


def _logged(name: str, ctx: ToolContext, outcome: ToolOutcome) -> ToolOutcome:
    log = logger.info if outcome.ok else logger.warning
    log("Tool %s on plan %s: %s", name, ctx.plan_id, outcome.summary)
    return outcome


def _write_tool(name: str) -> WriteTool:
    tool = TOOLS.get(name)
    if not isinstance(tool, WriteTool):
        # Only reachable with a corrupted proposal row: proposals hold prepared writes.
        raise ToolError(f"{name!r} is not a plan change")
    return tool


async def execute(ctx: ToolContext, call: LLMToolCall) -> ToolOutcome:
    """Run a read tool, or prepare (stage) a write tool's change without applying it."""
    tool = TOOLS.get(call.name)
    if tool is None:
        return _failure(f"Unknown tool {call.name!r}. Available tools: {', '.join(TOOLS)}")

    async def action() -> ToolOutcome:
        if isinstance(tool, ReadTool):
            return await tool.run(ctx, call.arguments)
        if isinstance(tool, BundleTool):
            return _staged(*await tool.prepare(ctx, call.arguments))
        return _staged(await tool.prepare(ctx, call.arguments))

    return _logged(call.name, ctx, await _attempt(call.name, action))


async def check_change(ctx: ToolContext, change: ProposedChange) -> ToolOutcome:
    """Whether a proposed change still validates against the plan (writes nothing)."""

    async def action() -> ToolOutcome:
        return _staged(await _write_tool(change.name).prepare(ctx, change.arguments))

    return await _attempt(change.name, action)


async def apply_change(ctx: ToolContext, change: ProposedChange) -> ToolOutcome:
    """Prepare a proposed change again (the plan may have moved on) and write it."""

    async def action() -> ToolOutcome:
        tool = _write_tool(change.name)
        return await tool.apply(ctx, await tool.prepare(ctx, change.arguments))

    return _logged(change.name, ctx, await _attempt(change.name, action))
