"""Tools the planning agent can call, and the executor that runs them.

Each tool validates the model's arguments with the same Pydantic schemas the REST API uses,
so the model can't write anything the API wouldn't accept. Expected failures (bad
arguments, unknown ids) come back as an unsuccessful `ToolOutcome` whose text is fed to the
model so it can correct itself; they never raise.

Item creation is split into one tool per item type (rather than one `add_item` taking a
discriminated union): a flat schema per type is much easier for a small local model.
"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from features.itinerary import service as itinerary_service
from features.itinerary.schemas import (
    ActivityItemCreate,
    ActivityItemPatch,
    FlightItemCreate,
    FlightItemPatch,
    ItemFields,
    ItemType,
    LodgingItemCreate,
    LodgingItemPatch,
    TransferItemCreate,
    TransferItemPatch,
)
from features.plans import service as plans_service
from features.plans.schemas import PlanUpdate
from shared.domain import Money, Place, Travelers
from shared.llm.schema import inline_schema
from shared.llm.types import LLMToolCall, ToolSpec
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


@dataclass(frozen=True)
class ToolOutcome:
    ok: bool
    content: str  # fed back to the model
    summary: str  # shown to the user
    plan_changed: bool = False


class ToolError(Exception):
    """An expected failure; the message is shown to both the model and the user."""


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[ToolOutcome]]


def _drop_nulls(arguments: dict[str, Any]) -> dict[str, Any]:
    # Small models often send every parameter, with null for "leave unchanged". Treat
    # null as "not provided" (the agent can't clear optional fields, which is fine).
    return {key: value for key, value in arguments.items() if value is not None}


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


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _set_plan_details(ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    args = SetPlanDetailsArgs.model_validate(_drop_nulls(arguments))
    fields = args.model_dump(exclude_unset=True)
    if not fields:
        raise ToolError("No plan details were given")
    # Dates are strings in the tool schema (simpler for the model); PlanUpdate parses them.
    patch = PlanUpdate.model_validate(fields)
    try:
        await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, patch)
    except plans_service.InvalidPlanError as exc:
        raise ToolError(str(exc)) from exc
    changed = ", ".join(name.replace("_", " ") for name in fields)
    return ToolOutcome(
        ok=True,
        content=f"Plan updated ({changed}).",
        summary=f"Updated trip {changed}",
        plan_changed=True,
    )


def _add_handler(item_type: ItemType) -> Handler:
    model = _CREATE_MODELS[item_type]

    async def handler(ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
        # `type` is implied by the tool, so it's not in the schema; set it here.
        data = model.model_validate({**_drop_nulls(arguments), "type": item_type})
        item = await itinerary_service.add_item(
            ctx.session, ctx.user_id, ctx.plan_id, data, created_by="assistant"
        )
        return ToolOutcome(
            ok=True,
            content=f'Added {item.type} "{item.title}" with id {item.id}.',
            summary=f"Added {item.type} “{item.title}”",
            plan_changed=True,
        )

    return handler


async def _update_item(ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
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
    try:
        item = await itinerary_service.update_item(
            ctx.session, ctx.user_id, ctx.plan_id, args.item_id, patch
        )
    except itinerary_service.InvalidItemError as exc:
        raise ToolError(str(exc)) from exc
    return ToolOutcome(
        ok=True,
        content=f'Updated {item.type} "{item.title}" ({", ".join(sorted(changes))}).',
        summary=f"Updated “{item.title}”",
        plan_changed=True,
    )


async def _remove_item(ctx: ToolContext, arguments: dict[str, Any]) -> ToolOutcome:
    args = RemoveItemArgs.model_validate(arguments)
    item = await itinerary_service.get_item(ctx.session, ctx.user_id, ctx.plan_id, args.item_id)
    await itinerary_service.remove_item(ctx.session, ctx.user_id, ctx.plan_id, args.item_id)
    return ToolOutcome(
        ok=True,
        content=f'Removed {item.type} "{item.title}".',
        summary=f"Removed “{item.title}”",
        plan_changed=True,
    )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    spec: ToolSpec
    run: Handler


def _item_parameters(model: type[ItemFields]) -> dict[str, Any]:
    schema = inline_schema(model)
    schema["properties"].pop("type", None)
    schema["required"] = [name for name in schema.get("required", []) if name != "type"]
    return schema


def _add_tool(item_type: ItemType, description: str) -> Tool:
    return Tool(
        spec=ToolSpec(
            name=f"add_{item_type}",
            description=description,
            parameters=_item_parameters(_CREATE_MODELS[item_type]),
        ),
        run=_add_handler(item_type),
    )


TOOLS: dict[str, Tool] = {
    tool.spec.name: tool
    for tool in [
        Tool(
            spec=ToolSpec(
                name="set_plan_details",
                description=(
                    "Set trip-level details: title, origin, destination, start/end dates, "
                    "travelers, budget. Send only the fields that change."
                ),
                parameters=inline_schema(SetPlanDetailsArgs),
            ),
            run=_set_plan_details,
        ),
        _add_tool(
            "flight",
            "Add a flight. from_place/to_place need IATA codes. timezone = departure "
            "airport's IANA zone, end_timezone = arrival airport's.",
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
            "Add ground/sea transport between two places: train, taxi, car rental, ferry...",
        ),
        Tool(
            spec=ToolSpec(
                name="update_item",
                description="Change fields of an existing itinerary item, by id.",
                parameters=inline_schema(UpdateItemArgs),
            ),
            run=_update_item,
        ),
        Tool(
            spec=ToolSpec(
                name="remove_item",
                description="Delete an itinerary item, by id.",
                parameters=inline_schema(RemoveItemArgs),
            ),
            run=_remove_item,
        ),
    ]
}

TOOL_SPECS: list[ToolSpec] = [tool.spec for tool in TOOLS.values()]


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _failure(message: str) -> ToolOutcome:
    return ToolOutcome(ok=False, content=f"Error: {message}", summary=message)


async def execute(ctx: ToolContext, call: LLMToolCall) -> ToolOutcome:
    tool = TOOLS.get(call.name)
    if tool is None:
        return _failure(f"Unknown tool {call.name!r}. Available tools: {', '.join(TOOLS)}")
    try:
        outcome = await tool.run(ctx, call.arguments)
    except ValidationError as exc:
        outcome = _failure(f"Invalid arguments for {call.name}: {summarize_validation_error(exc)}")
    except ToolError as exc:
        outcome = _failure(str(exc))
    except itinerary_service.ItemNotFoundError:
        outcome = _failure("No item with that id in this plan; use an id from the plan JSON")
    except plans_service.PlanNotFoundError:
        outcome = _failure("This plan no longer exists")
    log = logger.info if outcome.ok else logger.warning
    log("Tool %s on plan %s: %s", call.name, ctx.plan_id, outcome.summary)
    return outcome
