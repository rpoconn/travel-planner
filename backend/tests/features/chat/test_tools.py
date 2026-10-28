import json
from typing import Any

from features.chat.tools import TOOL_SPECS, TOOLS, ToolContext, execute
from features.itinerary import service as itinerary_service
from features.plans import service as plans_service
from shared.llm.types import LLMToolCall
from tests.features.chat.conftest import HOTEL_ARGS


async def _run(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> Any:
    return await execute(ctx, LLMToolCall(name=name, arguments=arguments))


async def _add_hotel(ctx: ToolContext) -> str:
    outcome = await _run(ctx, "add_lodging", HOTEL_ARGS)
    assert outcome.ok, outcome.content
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    return str(item.id)


# ---------------------------------------------------------------------------
# Specs
# ---------------------------------------------------------------------------


def test_tool_names() -> None:
    assert set(TOOLS) == {
        "set_plan_details",
        "add_flight",
        "add_lodging",
        "add_activity",
        "add_transfer",
        "update_item",
        "remove_item",
    }


def test_specs_are_self_contained_and_omit_type() -> None:
    for spec in TOOL_SPECS:
        dumped = json.dumps(spec.parameters)
        assert "$ref" not in dumped and "$defs" not in dumped, spec.name
        assert spec.parameters["type"] == "object"
    for name in ("add_flight", "add_lodging", "add_activity", "add_transfer"):
        parameters = TOOLS[name].spec.parameters
        assert "type" not in parameters["properties"]
        assert "type" not in parameters["required"]
        # A property *named* "title" survives the title-stripping.
        assert "title" in parameters["properties"]


# ---------------------------------------------------------------------------
# Successful calls
# ---------------------------------------------------------------------------


async def test_add_lodging_creates_assistant_item(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "add_lodging", {**HOTEL_ARGS, "room_type": None})

    assert outcome.ok and outcome.plan_changed
    assert outcome.summary == "Added lodging “Hotel Okura”"
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.type == "lodging"
    assert item.created_by == "assistant"
    assert str(item.id) in outcome.content  # the model needs the id for later edits


async def test_update_item_merges_changes(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(
        ctx,
        "update_item",
        {"item_id": item_id, "changes": {"end": "2027-05-07T11:00:00", "notes": None}},
    )

    assert outcome.ok, outcome.content
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.end.isoformat() == "2027-05-07T11:00:00+09:00"
    assert item.title == "Hotel Okura"


async def test_remove_item(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(ctx, "remove_item", {"item_id": item_id})

    assert outcome.ok
    assert outcome.summary == "Removed “Hotel Okura”"
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_set_plan_details(ctx: ToolContext) -> None:
    before = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    version = before.version

    outcome = await _run(
        ctx,
        "set_plan_details",
        {"origin": {"name": "San Francisco", "iata": "SFO"}, "end_date": "2027-05-10"},
    )

    assert outcome.ok and outcome.plan_changed, outcome.content
    plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.origin is not None and plan.origin.iata == "SFO"
    assert plan.end_date is not None and plan.end_date.isoformat() == "2027-05-10"
    assert plan.version == version + 1


# ---------------------------------------------------------------------------
# Failures are reported, never raised
# ---------------------------------------------------------------------------


async def test_unknown_tool(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "book_flight", {})

    assert not outcome.ok and not outcome.plan_changed
    assert "Unknown tool 'book_flight'" in outcome.content


async def test_invalid_arguments_are_summarized(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "add_lodging", {**HOTEL_ARGS, "end": "2027-05-01T10:00:00"})

    assert not outcome.ok
    assert outcome.content.startswith("Error: Invalid arguments for add_lodging:")
    assert "end must be at or after start" in outcome.content
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_unknown_item_id(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "remove_item", {"item_id": "00000000-0000-0000-0000-000000000000"})

    assert not outcome.ok
    assert "No item with that id" in outcome.content


async def test_update_rejects_fields_of_other_item_types(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(ctx, "update_item", {"item_id": item_id, "changes": {"carrier": "ANA"}})

    assert not outcome.ok
    assert outcome.summary == "A lodging has no field(s): carrier"


async def test_update_that_breaks_the_item_is_rejected(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(
        ctx, "update_item", {"item_id": item_id, "changes": {"end": "2027-05-01T10:00:00"}}
    )

    assert not outcome.ok
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.end.isoformat() == "2027-05-06T11:00:00+09:00"


async def test_set_plan_details_requires_a_field(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "set_plan_details", {"title": None})

    assert not outcome.ok
    assert outcome.summary == "No plan details were given"


async def test_set_plan_details_rejects_bad_date_range(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "set_plan_details", {"end_date": "2027-04-01"})

    assert not outcome.ok
    assert not outcome.plan_changed
