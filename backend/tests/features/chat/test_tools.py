import json
from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from uuid import UUID

import pytest

from features.chat.schemas import ProposedChange
from features.chat.tools import (
    TOOL_SPECS,
    TOOLS,
    ToolContext,
    ToolOutcome,
    apply_change,
    changes_plan,
    check_change,
    execute,
    specs_for_stage,
    urls_in,
)
from features.itinerary import service as itinerary_service
from features.plans import service as plans_service
from features.plans.schemas import PlanStage
from shared.flights.fake import FakeFlightProvider
from shared.flights.types import FlightSearchError
from shared.llm.types import LLMToolCall
from shared.search.fake import FakeSearchProvider
from shared.search.types import SearchError, SearchResult
from tests.features.chat.conftest import HOTEL_ARGS, flight_option


async def _stage(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """What the agent does: validate the call, write nothing."""
    return await execute(ctx, LLMToolCall(name=name, arguments=arguments))


def _change(outcome: ToolOutcome) -> ProposedChange:
    assert outcome.prepared, outcome.content
    (prepared,) = outcome.prepared
    return ProposedChange(
        call_id="call_1",
        name=prepared.name,
        arguments=prepared.arguments,
        summary=prepared.summary,
    )


async def _run(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """Stage the call and, if it validated, apply it as approving its proposal would (from
    the stored, normalised arguments)."""
    staged = await _stage(ctx, name, arguments)
    if not staged.ok or not staged.prepared:
        return staged
    return await apply_change(ctx, _change(staged))


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
        "choose_flights",
        "add_round_trip",
        "add_flight",
        "add_lodging",
        "add_activity",
        "add_transfer",
        "update_item",
        "remove_item",
        "web_search",
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


def test_specs_hide_fields_the_model_should_not_fill() -> None:
    for spec in TOOL_SPECS:
        dumped = json.dumps(spec.parameters)
        assert '{"type": "null"}' not in dumped and '"default": null' not in dumped, spec.name
        assert '"lat"' not in dumped and '"lng"' not in dumped, spec.name
        assert '"maxLength"' not in dumped and '"minLength"' not in dumped, spec.name
    for name in ("add_flight", "add_lodging", "add_activity", "add_transfer"):
        properties = TOOLS[name].spec.parameters["properties"]
        assert "status" not in properties, name
        assert "pickup_location" not in properties, name
        assert "dropoff_location" not in properties, name
    assert "end_timezone" in TOOLS["add_flight"].spec.parameters["properties"]
    assert "end_timezone" in TOOLS["add_transfer"].spec.parameters["properties"]
    assert "end_timezone" not in TOOLS["add_lodging"].spec.parameters["properties"]
    assert "end_timezone" not in TOOLS["add_activity"].spec.parameters["properties"]
    origin = TOOLS["set_plan_details"].spec.parameters["properties"]["origin"]
    assert set(origin["properties"]) == {"name", "city", "iata", "address"}
    assert origin["required"] == ["name"]


def test_only_web_search_is_a_read_tool() -> None:
    assert [name for name in TOOLS if not changes_plan(name)] == ["web_search"]
    assert changes_plan("book_flight")  # unknown tools count as writes


# ---------------------------------------------------------------------------
# Staging: write tools validate and stage, and write nothing
# ---------------------------------------------------------------------------


async def test_staging_an_item_writes_nothing(ctx: ToolContext) -> None:
    before = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    version = before.version

    outcome = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "room_type": None})

    assert outcome.ok and not outcome.plan_changed
    assert outcome.summary == "Proposed: Add lodging “Hotel Okura” on 2027-05-02 15:00"
    assert outcome.content.startswith("Staged for the user's approval: Add lodging")
    assert "Do not call this tool again" in outcome.content
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []
    after = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    assert after.version == version


async def test_staged_arguments_are_normalised(ctx: ToolContext) -> None:
    outcome = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "room_type": None})

    change = _change(outcome)
    # Nulls and defaults are left out, and `type` is implied by the tool.
    assert "room_type" not in change.arguments and "status" not in change.arguments
    assert "type" not in change.arguments
    # Times come back localised to their timezone, which re-validates to the same instant.
    assert change.arguments["start"] == "2027-05-02T15:00:00+09:00"


async def test_staged_flight_records_the_inferred_direction(ctx: ToolContext) -> None:
    outcome = await _stage(ctx, "add_flight", _flight("LAX", "HND", "2027-05-01"))

    assert _change(outcome).arguments["direction"] == "outbound"


@pytest.mark.parametrize(
    ("name", "arguments", "summary"),
    [
        ("set_plan_details", {"start_date": "2027-05-02", "travelers": {"adults": 2}},
         "Set trip start date, travelers"),
        ("remove_item", None, "Remove “Hotel Okura”"),
        ("update_item", {"end": "2027-05-07T11:00:00"}, "Change “Hotel Okura”: end"),
    ],
)  # fmt: skip
async def test_staging_summaries_name_the_change(
    ctx: ToolContext, name: str, arguments: dict[str, Any] | None, summary: str
) -> None:
    if name == "set_plan_details":
        assert arguments is not None
        call = arguments
    else:
        item_id = await _add_hotel(ctx)
        call = (
            {"item_id": item_id}
            if arguments is None
            else {"item_id": item_id, "changes": arguments}
        )

    outcome = await _stage(ctx, name, call)

    assert outcome.ok, outcome.content
    assert outcome.summary == f"Proposed: {summary}"


async def test_check_change_validates_without_writing(ctx: ToolContext) -> None:
    change = _change(await _stage(ctx, "add_lodging", HOTEL_ARGS))

    outcome = await check_change(ctx, change)

    assert outcome.ok and outcome.prepared
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_applying_a_change_to_a_removed_item_fails(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)
    change = _change(
        await _stage(ctx, "update_item", {"item_id": item_id, "changes": {"notes": "x"}})
    )
    await itinerary_service.remove_item(ctx.session, ctx.user_id, ctx.plan_id, UUID(item_id))

    checked = await check_change(ctx, change)
    applied = await apply_change(ctx, change)

    assert not checked.ok and not applied.ok
    assert "No item with that id" in applied.content


async def test_applying_a_change_that_is_now_in_the_past_fails(ctx: ToolContext) -> None:
    change = _change(await _stage(ctx, "add_lodging", HOTEL_ARGS))
    later = replace(ctx, today=date(2027, 6, 1))

    outcome = await apply_change(later, change)

    assert not outcome.ok
    assert "start 2027-05-02 is in the past" in outcome.content
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_applying_a_read_tool_is_refused(ctx: ToolContext) -> None:
    change = ProposedChange(call_id="c", name="web_search", arguments={"query": "x"}, summary="x")

    outcome = await apply_change(ctx, change)

    assert not outcome.ok and outcome.summary == "'web_search' is not a plan change"


# ---------------------------------------------------------------------------
# Successful calls (staged, then applied as on approval)
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


# ---------------------------------------------------------------------------
# Budget rules (a plan with a budget or spending limits)
# ---------------------------------------------------------------------------

DINNER_ARGS: dict[str, Any] = {
    "title": "Dinner at Alchimiste",
    "start": "2027-05-03T19:00:00",
    "end": "2027-05-03T21:00:00",
    "timezone": "Asia/Tokyo",
    "place": {"name": "Alchimiste", "city": "Tokyo"},
    "category": "food",
}


async def _set_budget(ctx: ToolContext, amount: int, **details: Any) -> None:
    outcome = await _run(
        ctx, "set_plan_details", {"budget": {"amount": amount, "currency": "USD"}, **details}
    )
    assert outcome.ok, outcome.content


async def test_with_a_budget_every_item_needs_a_cost(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)

    outcome = await _stage(ctx, "add_lodging", HOTEL_ARGS)

    assert not outcome.ok and not outcome.prepared
    assert "Hotel Okura has no cost" in outcome.content
    assert 'best estimate (basis "estimate")' in outcome.content


async def test_without_a_budget_costs_are_optional(ctx: ToolContext) -> None:
    assert (await _stage(ctx, "add_lodging", HOTEL_ARGS)).ok


async def test_a_zero_budget_is_no_budget(ctx: ToolContext) -> None:
    await _set_budget(ctx, 0)

    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.budget is None
    pricey = {**HOTEL_ARGS, "cost": {"amount": 900, "currency": "USD"}}
    assert (await _stage(ctx, "add_lodging", HOTEL_ARGS)).ok
    assert (await _stage(ctx, "add_lodging", pricey)).ok


async def test_a_zero_budget_saved_before_it_meant_none_is_ignored(ctx: ToolContext) -> None:
    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    plan.budget = {"amount": "0", "currency": "USD"}
    await ctx.session.flush()

    outcome = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 900}})

    assert outcome.ok, outcome.content


async def test_a_cost_in_another_currency_than_the_budget_is_rejected(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)

    outcome = await _stage(
        ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 30000, "currency": "JPY"}}
    )

    assert not outcome.ok
    assert "costs 30,000.00 JPY, but the budget is in USD" in outcome.content


async def test_an_item_that_breaks_the_budget_is_rejected(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})

    within = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 200}})
    over = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 300}})

    assert within.ok and within.prepared
    assert not over.ok and not over.prepared
    assert "costs total 1,100.00 USD, 100.00 USD over the 1,000.00 USD budget" in over.content
    assert "ask the user whether to raise the budget" in over.content


async def test_an_update_counts_the_new_cost_in_place_of_the_old_one(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)

    cheaper = await _stage(
        ctx, "update_item", {"item_id": str(item.id), "changes": {"cost": {"amount": 950}}}
    )
    dearer = await _stage(
        ctx, "update_item", {"item_id": str(item.id), "changes": {"cost": {"amount": 1200}}}
    )

    assert cheaper.ok
    assert not dearer.ok and "costs total 1,200.00 USD" in dearer.content


async def test_an_update_that_does_not_touch_the_cost_is_not_checked(ctx: ToolContext) -> None:
    # An item from before the budget was set has no cost; moving it is still allowed (the
    # timeline flags the missing cost instead).
    item_id = await _add_hotel(ctx)
    await _set_budget(ctx, 1000)

    outcome = await _stage(
        ctx, "update_item", {"item_id": item_id, "changes": {"end": "2027-05-07T11:00:00"}}
    )

    assert outcome.ok, outcome.content


async def test_changes_already_on_the_card_count_towards_the_budget(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    first = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 600}})
    on_card = replace(ctx, staged=[_change(first)])

    second = await _stage(on_card, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 500}})
    repeat = await _stage(on_card, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 600}})

    assert not second.ok and "costs total 1,100.00 USD" in second.content
    # Re-sending the change that's already on the card isn't counted twice.
    assert repeat.ok


async def test_removals_on_the_card_free_up_budget(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    removal = await _stage(ctx, "remove_item", {"item_id": str(item.id)})
    on_card = replace(ctx, staged=[_change(removal)])

    outcome = await _stage(on_card, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 900}})

    assert outcome.ok, outcome.content


# The reported trip: PHL 15:00 (New York) → HND 07:00 the next morning (Tokyo).
_PHL_HND: dict[str, Any] = {
    "title": "PHL to HND",
    "carrier": "Japan Airlines",
    "start": "2027-05-01T15:00:00",
    "end": "2027-05-02T07:00:00",
    "timezone": "America/New_York",
    "end_timezone": "Asia/Tokyo",
    "from_place": {"iata": "PHL"},
    "to_place": {"iata": "HND"},
    "direction": "outbound",
}


async def test_an_item_at_the_destination_in_the_home_timezone_is_rejected(
    ctx: ToolContext,
) -> None:
    await _run(ctx, "add_flight", _PHL_HND)

    outcome = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "timezone": "America/New_York"})

    assert not outcome.ok
    assert "traveller is in Asia/Tokyo" in outcome.content
    assert "local times in Asia/Tokyo" in outcome.content


async def test_an_item_in_the_destination_timezone_is_accepted(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _PHL_HND)

    outcome = await _stage(ctx, "add_lodging", HOTEL_ARGS)

    assert outcome.ok, outcome.content


async def test_a_flight_on_the_card_sets_the_timezone_for_the_rest_of_it(
    ctx: ToolContext,
) -> None:
    flight = await _stage(ctx, "add_flight", _PHL_HND)
    on_card = replace(ctx, staged=[_change(flight)])

    outcome = await _stage(on_card, "add_lodging", {**HOTEL_ARGS, "timezone": "America/New_York"})

    assert not outcome.ok and "traveller is in Asia/Tokyo" in outcome.content


async def test_moving_an_item_into_the_wrong_timezone_is_rejected(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _PHL_HND)
    hotel_id = await _add_hotel_after_flight(ctx)

    outcome = await _stage(
        ctx, "update_item", {"item_id": hotel_id, "changes": {"timezone": "America/New_York"}}
    )

    assert not outcome.ok and "traveller is in Asia/Tokyo" in outcome.content


async def _add_hotel_after_flight(ctx: ToolContext) -> str:
    outcome = await _run(ctx, "add_lodging", HOTEL_ARGS)
    assert outcome.ok, outcome.content
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    return next(str(item.id) for item in items if item.type == "lodging")


async def test_a_meal_over_the_per_person_limit_is_rejected(ctx: ToolContext) -> None:
    limit = {"category": "food", "max_per_person": {"amount": 200, "currency": "USD"}}
    await _run(ctx, "set_plan_details", {"travelers": {"adults": 2}, "limits": [limit]})

    within = await _stage(ctx, "add_activity", {**DINNER_ARGS, "cost": {"amount": 380}})
    over = await _stage(ctx, "add_activity", {**DINNER_ARGS, "cost": {"amount": "504.24"}})
    no_cost = await _stage(ctx, "add_activity", DINNER_ARGS)

    assert within.ok, within.content
    assert not over.ok
    assert (
        "costs 504.24 USD (252.12 USD a person for 2), over the food limit of 200.00 USD a person"
        in over.content
    )
    assert "ask the user whether to raise that limit" in over.content
    # A limit needs a cost to check, even without a budget...
    assert not no_cost.ok and "has no cost" in no_cost.content
    # ...but only for the limited category.
    assert (await _stage(ctx, "add_lodging", HOTEL_ARGS)).ok


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


async def test_set_plan_details_sets_spending_limits(ctx: ToolContext) -> None:
    limit = {"category": "food", "max_per_person": {"amount": 200, "currency": "USD"}}

    outcome = await _run(ctx, "set_plan_details", {"limits": [limit]})

    assert outcome.ok, outcome.content
    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.limits == [
        {"category": "food", "max_per_person": {"amount": "200", "currency": "USD"}}
    ]


async def test_set_plan_details_accepts_place_as_string(ctx: ToolContext) -> None:
    # Local models often send "destination": "Tokyo" instead of an object.
    outcome = await _run(ctx, "set_plan_details", {"destination": "Tokyo"})

    assert outcome.ok, outcome.content
    plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.destination is not None and plan.destination.name == "Tokyo"


async def test_set_plan_details_ignores_an_echoed_schema(ctx: ToolContext) -> None:
    # Seen from qwen3:8b: an optional parameter "filled" with its own schema.
    echoed = {"description": "Total trip budget", "type": "object"}

    outcome = await _stage(ctx, "set_plan_details", {"start_date": "2027-05-02", "budget": echoed})

    assert outcome.ok, outcome.content
    assert _change(outcome).arguments == {"start_date": "2027-05-02"}


async def test_set_plan_details_accepts_place_as_json_string(ctx: ToolContext) -> None:
    outcome = await _run(
        ctx, "set_plan_details", {"destination": '{"name": "Tokyo", "country": "Japan"}'}
    )

    assert outcome.ok, outcome.content
    plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.destination is not None
    assert (plan.destination.name, plan.destination.country) == ("Tokyo", "Japan")


def _flight(from_iata: str, to_iata: str, day: str) -> dict[str, Any]:
    # `add_flight` arguments with no `direction`, as small models often send them.
    return {
        "title": f"{from_iata} to {to_iata}",
        "carrier": "ANA",
        "start": f"{day}T10:00:00",
        "end": f"{day}T12:00:00",
        "timezone": "Asia/Tokyo",
        "from_place": {"iata": from_iata},
        "to_place": {"iata": to_iata},
    }


@pytest.mark.parametrize(
    ("flight", "direction"),
    [
        # From / to the origin.
        (_flight("SFO", "HND", "2027-05-03"), "outbound"),
        (_flight("HND", "SFO", "2027-05-03"), "return"),
        # Not touching the origin: decided by the trip's first / last day.
        (_flight("LAX", "HND", "2027-05-01"), "outbound"),
        (_flight("KIX", "LAX", "2027-05-08"), "return"),
        (_flight("HND", "ITM", "2027-05-04"), "internal"),
    ],
)
async def test_add_flight_infers_a_missing_direction(
    ctx: ToolContext, flight: dict[str, Any], direction: str
) -> None:
    await _run(ctx, "set_plan_details", {"origin": {"name": "San Francisco", "iata": "SFO"}})

    outcome = await _run(ctx, "add_flight", flight)

    assert outcome.ok, outcome.content
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.type == "flight" and item.direction == direction


async def test_add_flight_matches_the_origin_by_city(ctx: ToolContext) -> None:
    # The origin is often just a city name while the flight has airport codes.
    await _run(ctx, "set_plan_details", {"origin": "Philadelphia"})
    flight = _flight("PHL", "HND", "2027-05-03")
    flight["from_place"] = {"name": "Philadelphia Intl", "city": "Philadelphia", "iata": "PHL"}

    outcome = await _run(ctx, "add_flight", flight)

    assert outcome.ok, outcome.content
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.type == "flight" and item.direction == "outbound"


async def test_add_flight_keeps_a_given_direction(ctx: ToolContext) -> None:
    outcome = await _run(
        ctx, "add_flight", {**_flight("HND", "ITM", "2027-05-01"), "direction": "internal"}
    )

    assert outcome.ok, outcome.content
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.type == "flight" and item.direction == "internal"


async def _directions(ctx: ToolContext) -> dict[str, str]:
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    return {item.title: item.direction for item in items if item.type == "flight"}


async def test_add_flight_corrects_a_trip_home_labelled_outbound(ctx: ToolContext) -> None:
    # The reported bug: the origin is just "Philadelphia" (no code) and the model labels
    # both legs "outbound". Landing back at PHL on the last day is the return.
    await _run(ctx, "set_plan_details", {"origin": "Philadelphia"})
    for flight in (_flight("PHL", "HND", "2027-05-01"), _flight("HND", "PHL", "2027-05-08")):
        outcome = await _run(ctx, "add_flight", {**flight, "direction": "outbound"})
        assert outcome.ok, outcome.content

    assert await _directions(ctx) == {"PHL to HND": "outbound", "HND to PHL": "return"}


async def test_add_flight_mirrors_the_outbound_flight_mid_trip(ctx: ToolContext) -> None:
    # No origin and no date signal: flying back to where the outbound left from (from the
    # other Tokyo airport) is the trip home.
    await _run(ctx, "add_flight", {**_flight("PHL", "HND", "2027-05-01"), "direction": "outbound"})
    outcome = await _run(
        ctx, "add_flight", {**_flight("NRT", "PHL", "2027-05-05"), "direction": "outbound"}
    )

    assert outcome.ok, outcome.content
    assert (await _directions(ctx))["NRT to PHL"] == "return"


async def test_add_flight_origin_beats_the_given_label(ctx: ToolContext) -> None:
    await _run(ctx, "set_plan_details", {"origin": {"name": "Tokyo", "iata": "TYO"}})
    outcome = await _run(
        ctx, "add_flight", {**_flight("NRT", "SFO", "2027-05-04"), "direction": "return"}
    )

    assert outcome.ok, outcome.content
    assert (await _directions(ctx))["NRT to SFO"] == "outbound"


async def test_add_flight_rejects_a_zero_length_flight(ctx: ToolContext) -> None:
    # PHL 10:00 EST is exactly 00:00 JST the next day: the model's "fix" for an arrival
    # that was before departure.
    flight = {
        **_flight("PHL", "HND", "2026-12-15"),
        "timezone": "America/New_York",
        "end": "2026-12-16T00:00:00",
        "end_timezone": "Asia/Tokyo",
    }

    outcome = await _run(ctx, "add_flight", flight)

    assert not outcome.ok
    assert "at least 15 minute(s)" in outcome.content
    assert await _directions(ctx) == {}


async def test_add_transfer_rejects_an_airport_code_that_names_another_airport(
    ctx: ToolContext,
) -> None:
    # The reported bug: "Tokyo Haneda Airport" sent with Narita's code.
    transfer = {
        "title": "Car from Haneda",
        "start": "2027-05-01T09:00:00",
        "end": "2027-05-01T11:00:00",
        "timezone": "Asia/Tokyo",
        "mode": "taxi",
        "from_place": {"name": "Tokyo Haneda Airport", "iata": "NRT"},
        "to_place": {"name": "Hotel Okura"},
    }

    outcome = await _stage(ctx, "add_transfer", transfer)

    assert not outcome.ok and not outcome.prepared
    assert "'Tokyo Haneda Airport' is HND, not NRT" in outcome.content


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


async def test_set_plan_details_rejects_past_dates_and_suggests_the_next_year(
    ctx: ToolContext,
) -> None:
    # The reported bug: "a week in Tokyo in May" said in autumn 2026 recorded May 2026.
    outcome = await _run(
        ctx, "set_plan_details", {"start_date": "2026-05-01", "end_date": "2026-05-08"}
    )

    assert not outcome.ok and not outcome.plan_changed
    assert "start_date 2026-05-01 is in the past (today is 2026-10-01)" in outcome.content
    assert "e.g. 2027-05-01" in outcome.content
    plan = await itinerary_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.start_date is not None and plan.start_date.isoformat() == "2027-05-01"


async def test_set_plan_details_accepts_today(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "set_plan_details", {"start_date": ctx.today.isoformat()})

    assert outcome.ok, outcome.content


async def test_add_item_rejects_a_past_start(ctx: ToolContext) -> None:
    outcome = await _run(
        ctx,
        "add_lodging",
        {**HOTEL_ARGS, "start": "2026-05-02T15:00:00", "end": "2026-05-06T11:00:00"},
    )

    assert not outcome.ok
    assert "start 2026-05-02 is in the past" in outcome.content
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_update_item_rejects_a_past_start(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(
        ctx, "update_item", {"item_id": item_id, "changes": {"start": "2026-05-02T15:00:00"}}
    )

    assert not outcome.ok
    assert "start 2026-05-02 is in the past" in outcome.content


async def test_past_feb_29_suggests_the_next_leap_day(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "set_plan_details", {"start_date": "2024-02-29"})

    assert not outcome.ok
    assert "e.g. 2028-02-29" in outcome.content


# ---------------------------------------------------------------------------
# web_search
# ---------------------------------------------------------------------------


async def test_web_search_shapes_query_with_plan_and_lists_results(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    fake_search.queue(
        [
            SearchResult(title="Hotel Okura", url="https://example.com/okura", snippet="$400"),
            SearchResult(title="Park Hyatt", url="https://example.com/ph"),
        ]
    )

    outcome = await _run(ctx, "web_search", {"query": "quiet hotels", "kind": "hotels"})

    assert outcome.ok and not outcome.plan_changed
    # The fixture plan is "Tokyo in May" 2027.
    (search,) = fake_search.searches
    assert (search.query, search.kind) == ("quiet hotels in Tokyo May 2027", "hotels")
    assert outcome.summary == "Searched the web for “quiet hotels in Tokyo May 2027”"
    assert "1. Hotel Okura\n   URL: https://example.com/okura\n   $400" in outcome.content
    assert "2. Park Hyatt\n   URL: https://example.com/ph" in outcome.content
    assert "source_url" in outcome.content


async def test_web_search_kind_defaults_to_general(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    outcome = await _run(ctx, "web_search", {"query": "Japan visa rules", "kind": None})

    assert outcome.ok
    assert [(s.query, s.kind) for s in fake_search.searches] == [("Japan visa rules", "general")]


async def test_web_search_with_no_results(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    fake_search.queue([])

    outcome = await _run(ctx, "web_search", {"query": "zzzz", "kind": "general"})

    assert outcome.ok
    assert outcome.content.startswith('No web results for "zzzz"')


async def test_web_search_failure_is_reported_with_guidance(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    fake_search.queue(SearchError("Web search timed out."))

    outcome = await _run(ctx, "web_search", {"query": "ramen", "kind": "activities"})

    assert not outcome.ok and not outcome.plan_changed
    assert outcome.summary == "Web search timed out."
    assert outcome.content.startswith("Error: Web search timed out. Don't repeat this search.")


async def test_web_search_rejects_an_unknown_kind(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    outcome = await _run(ctx, "web_search", {"query": "ramen", "kind": "restaurants"})

    assert not outcome.ok
    assert outcome.content.startswith("Error: Invalid arguments for web_search:")
    assert fake_search.searches == []


# ---------------------------------------------------------------------------
# Round trip (two add_flight changes from one call)
# ---------------------------------------------------------------------------

_ROUND_TRIP: dict[str, Any] = {
    "outbound": _flight("PHL", "HND", "2027-05-01"),
    "return": _flight("HND", "PHL", "2027-05-08"),
}


async def test_round_trip_stages_both_flights_and_writes_nothing(ctx: ToolContext) -> None:
    # The model's own directions are ignored: the leg's key says which way it goes.
    legs = {key: {**leg, "direction": "internal"} for key, leg in _ROUND_TRIP.items()}

    outcome = await _stage(ctx, "add_round_trip", legs)

    assert outcome.ok, outcome.content
    assert [(p.name, p.arguments["direction"]) for p in outcome.prepared] == [
        ("add_flight", "outbound"),
        ("add_flight", "return"),
    ]
    assert outcome.summary == (
        "Proposed: Add flight “PHL to HND” on 2027-05-01 10:00; "
        "Add flight “HND to PHL” on 2027-05-08 10:00"
    )
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_round_trip_legs_are_applied_as_ordinary_flights(ctx: ToolContext) -> None:
    outcome = await _stage(ctx, "add_round_trip", _ROUND_TRIP)

    for prepared in outcome.prepared:
        change = ProposedChange(
            call_id="call_1",
            name=prepared.name,
            arguments=prepared.arguments,
            summary=prepared.summary,
        )
        assert (await apply_change(ctx, change)).ok

    assert await _directions(ctx) == {"PHL to HND": "outbound", "HND to PHL": "return"}


async def test_round_trip_home_must_leave_after_the_flight_out_lands(ctx: ToolContext) -> None:
    # Earlier but not overlapping, so only the round-trip check catches it.
    early = {**_ROUND_TRIP, "return": _flight("HND", "PHL", "2027-04-30")}

    outcome = await _stage(ctx, "add_round_trip", early)

    assert not outcome.ok and not outcome.prepared
    assert "must leave after the outbound flight lands" in outcome.content


async def test_round_trip_legs_fit_the_budget_together(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    leg_cost = {"cost": {"amount": 600}}
    pricey = {key: {**leg, **leg_cost} for key, leg in _ROUND_TRIP.items()}

    outcome = await _stage(ctx, "add_round_trip", pricey)

    assert not outcome.ok
    assert "200.00 USD over the 1,000.00 USD budget" in outcome.content


async def test_round_trip_legs_without_a_title_are_named_after_their_airports(
    ctx: ToolContext,
) -> None:
    untitled = {
        key: {name: value for name, value in leg.items() if name != "title"}
        for key, leg in _ROUND_TRIP.items()
    }

    outcome = await _stage(ctx, "add_round_trip", untitled)

    assert outcome.ok, outcome.content
    assert [p.arguments["title"] for p in outcome.prepared] == [
        "Flight PHL to HND",
        "Flight HND to PHL",
    ]


async def test_round_trip_legs_missing_an_airport_take_it_from_the_other_leg(
    ctx: ToolContext,
) -> None:
    # As sent by a small model: each leg says only where it leaves from (or, null, where
    # it lands).
    outbound = {k: v for k, v in _ROUND_TRIP["outbound"].items() if k != "to_place"}
    home = {**_ROUND_TRIP["return"], "to_place": None}

    outcome = await _stage(ctx, "add_round_trip", {"outbound": outbound, "return": home})

    assert outcome.ok, outcome.content
    assert [
        (p.arguments["from_place"]["iata"], p.arguments["to_place"]["iata"])
        for p in outcome.prepared
    ] == [("PHL", "HND"), ("HND", "PHL")]


async def test_round_trip_errors_name_the_leg(ctx: ToolContext) -> None:
    broken = {**_ROUND_TRIP, "return": {**_ROUND_TRIP["return"], "start": "soon"}}

    outcome = await _stage(ctx, "add_round_trip", broken)

    assert not outcome.ok
    assert outcome.content.startswith(
        "Error: Invalid arguments for add_round_trip's return leg: start:"
    )


async def test_round_trip_needs_both_legs(ctx: ToolContext) -> None:
    outcome = await _stage(ctx, "add_round_trip", {"outbound": _ROUND_TRIP["outbound"]})

    assert not outcome.ok
    assert outcome.content.startswith("Error: Invalid arguments for add_round_trip:")


def test_round_trip_spec_has_two_flight_legs_without_a_direction() -> None:
    parameters = TOOLS["add_round_trip"].spec.parameters

    assert parameters["required"] == ["outbound", "return"]
    for key in ("outbound", "return"):
        leg = parameters["properties"][key]["properties"]
        assert "carrier" in leg and "end_timezone" in leg
        assert "direction" not in leg


# ---------------------------------------------------------------------------
# Choosing flights (search, rank by travel time then price, stage the best)
# ---------------------------------------------------------------------------

_SLOW = flight_option(
    "PHL", "HND", "2027-05-01T08:00", 22 * 60, 800, via="ORD", departure_token="slow"
)
_FAST = flight_option("PHL", "HND", "2027-05-01T15:00", 14 * 60 + 30, 1500, departure_token="fast")
_FAST_CHEAPER = flight_option(
    "PHL", "HND", "2027-05-01T11:00", 14 * 60 + 30, 1300, departure_token="fast-cheaper"
)
_HOME = flight_option("HND", "PHL", "2027-05-08T17:00", 13 * 60, 1400)
_HOME_SLOW = flight_option("HND", "PHL", "2027-05-08T10:00", 19 * 60, 1250, via="SEA")


async def _ready_for_flights(ctx: ToolContext, **details: Any) -> None:
    outcome = await _run(
        ctx,
        "set_plan_details",
        {
            "origin": {"name": "Philadelphia", "iata": "PHL"},
            "travelers": {"adults": 2, "children": 1},
            **details,
        },
    )
    assert outcome.ok, outcome.content


async def test_choose_flights_stages_the_shortest_then_cheapest_round_trip(
    ctx: ToolContext, fake_flights: FakeFlightProvider
) -> None:
    await _ready_for_flights(ctx)
    fake_flights.queue([_SLOW, _FAST, _FAST_CHEAPER], [_HOME_SLOW, _HOME])

    # One airport as a bare string, as small models send it.
    outcome = await _stage(ctx, "choose_flights", {"to_airports": "hnd"})

    assert outcome.ok, outcome.content
    # Searched with the trip's origin, dates and everyone travelling; then the flights
    # home for the chosen flight out.
    first, second = fake_flights.searches
    assert (first.query.origin, first.query.destination) == (("PHL",), ("HND",))
    assert (first.query.depart_date, first.query.return_date) == (
        date(2027, 5, 1),
        date(2027, 5, 8),
    )
    assert first.query.adults == 3
    assert second.departure_token == "fast-cheaper"
    out, home = outcome.prepared
    assert (out.arguments["direction"], home.arguments["direction"]) == ("outbound", "return")
    assert out.arguments["start"] == "2027-05-01T11:00:00-04:00"
    assert home.arguments["start"] == "2027-05-08T17:00:00+09:00"
    # The whole round-trip price (from the flight home's search) goes on the flight out.
    # (USD is the default currency, so normalising leaves it out.)
    assert out.arguments["cost"] == {"amount": "1400", "basis": "exact"}
    assert home.arguments["cost"]["amount"] == "0"
    assert "Total price (round trip, all travelers): 1,400 USD" in outcome.content
    assert "\n2. ANA NH 100: PHL Sat May 1 15:00" in outcome.content
    assert "\n3. ANA NH 100, NH 101: PHL Sat May 1 08:00" in outcome.content
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_chosen_flights_keep_their_real_length_across_timezones(
    ctx: ToolContext, fake_flights: FakeFlightProvider
) -> None:
    await _ready_for_flights(ctx)
    fake_flights.queue([_FAST], [_HOME])
    staged = await _stage(ctx, "choose_flights", {"to_airports": ["HND"]})

    for prepared in staged.prepared:
        change = ProposedChange(
            call_id="call_1",
            name=prepared.name,
            arguments=prepared.arguments,
            summary=prepared.summary,
        )
        assert (await apply_change(ctx, change)).ok

    # Approving re-checks the stored changes; it doesn't search again.
    assert len(fake_flights.searches) == 2
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert {item.title: item.end - item.start for item in items} == {
        "Flight PHL to HND": timedelta(hours=14, minutes=30),
        "Flight HND to PHL": timedelta(hours=13),
    }


async def test_choose_flights_can_propose_a_runner_up(
    ctx: ToolContext, fake_flights: FakeFlightProvider
) -> None:
    await _ready_for_flights(ctx)
    fake_flights.queue([_SLOW, _FAST, _FAST_CHEAPER], [_HOME])

    outcome = await _stage(ctx, "choose_flights", {"to_airports": ["HND"], "option": 2})

    assert outcome.ok, outcome.content
    assert fake_flights.searches[1].departure_token == "fast"
    assert "\n1. ANA NH 100: PHL Sat May 1 11:00" in outcome.content


async def test_choose_flights_one_way_searches_once(
    ctx: ToolContext, fake_flights: FakeFlightProvider
) -> None:
    await _ready_for_flights(ctx)
    fake_flights.queue([_SLOW, _FAST])

    outcome = await _stage(ctx, "choose_flights", {"to_airports": ["HND"], "one_way": True})

    assert outcome.ok, outcome.content
    (search,) = fake_flights.searches
    assert search.query.return_date is None
    (flight,) = outcome.prepared
    assert flight.arguments["cost"]["amount"] == "1500"
    assert "Total price (one way, all travelers): 1,500 USD" in outcome.content


@pytest.mark.parametrize(
    ("details", "arguments", "error"),
    [
        # The test plan has no travelers or origin.
        ({}, {"to_airports": ["HND"]}, "Give from_airports"),
        (
            {"origin": {"name": "Philadelphia", "iata": "PHL"}},
            {"to_airports": ["HND"]},
            "no travelers",
        ),
        # Its destination is "Tokyo", with no airport code.
        ({"travelers": {"adults": 1}}, {"from_airports": ["PHL"]}, "Give to_airports"),
        (
            {"travelers": {"adults": 1}},
            {"from_airports": ["PHL"], "to_airports": ["HND"], "depart_date": "2026-05-01"},
            "depart_date 2026-05-01 is in the past",
        ),
    ],
)
async def test_choose_flights_needs_a_complete_search_before_searching(
    ctx: ToolContext,
    fake_flights: FakeFlightProvider,
    details: dict[str, Any],
    arguments: dict[str, Any],
    error: str,
) -> None:
    if details:
        assert (await _run(ctx, "set_plan_details", details)).ok

    outcome = await _stage(ctx, "choose_flights", arguments)

    assert not outcome.ok and error in outcome.content
    assert fake_flights.searches == []


async def test_choose_flights_reports_a_failed_search(
    ctx: ToolContext, fake_flights: FakeFlightProvider
) -> None:
    await _ready_for_flights(ctx)
    fake_flights.queue(FlightSearchError("Flight search timed out."))

    outcome = await _stage(ctx, "choose_flights", {"to_airports": ["HND"]})

    assert not outcome.ok
    assert outcome.content == "Error: Flight search timed out."


@pytest.mark.parametrize(
    ("replies", "option", "error"),
    [
        ([[]], 1, "No flights out found"),
        ([[_FAST], []], 1, "No flights home found"),
        ([[_FAST]], 3, "There is no option 3; choose 1 to 1"),
    ],
)
async def test_choose_flights_without_enough_flights(
    ctx: ToolContext,
    fake_flights: FakeFlightProvider,
    replies: list[Any],
    option: int,
    error: str,
) -> None:
    await _ready_for_flights(ctx)
    fake_flights.queue(*replies)

    outcome = await _stage(ctx, "choose_flights", {"to_airports": ["HND"], "option": option})

    assert not outcome.ok and error in outcome.content


async def test_chosen_flights_must_fit_the_budget(
    ctx: ToolContext, fake_flights: FakeFlightProvider
) -> None:
    await _ready_for_flights(ctx, budget={"amount": 1000, "currency": "USD"})
    fake_flights.queue([_FAST], [_HOME])

    outcome = await _stage(ctx, "choose_flights", {"to_airports": ["HND"]})

    assert not outcome.ok
    assert "400.00 USD over the 1,000.00 USD budget" in outcome.content


# ---------------------------------------------------------------------------
# Flight routes (no overlaps; each flight leaves from where the last one landed)
# ---------------------------------------------------------------------------


async def test_a_second_flight_out_is_rejected_with_how_to_swap(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", {**_flight("PHL", "HND", "2027-05-01"), "title": "Pricey"})
    (old,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    cheaper = {**_flight("PHL", "HND", "2027-05-01"), "title": "Cheaper"}
    cheaper["start"], cheaper["end"] = "2027-05-01T11:00:00", "2027-05-01T13:00:00"

    outcome = await _stage(ctx, "add_flight", cheaper)

    assert not outcome.ok
    assert "Cheaper overlaps the flight Pricey" in outcome.content
    assert f'remove_item (item_id "{old.id}")' in outcome.content


async def test_a_flight_removed_on_the_same_card_does_not_count(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _flight("PHL", "HND", "2027-05-01"))
    (old,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    removal = _change(await _stage(ctx, "remove_item", {"item_id": str(old.id)}))

    outcome = await _stage(
        replace(ctx, staged=[removal]), "add_flight", _flight("PHL", "HND", "2027-05-01")
    )

    assert outcome.ok, outcome.content


async def test_moving_a_flight_so_it_breaks_the_route_is_rejected(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _flight("PHL", "HND", "2027-05-01"))
    await _run(ctx, "add_flight", _flight("HND", "PHL", "2027-05-08"))
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    home = next(item for item in items if item.title == "HND to PHL")

    outcome = await _stage(
        ctx,
        "update_item",
        {"item_id": str(home.id), "changes": {"from_place": {"iata": "KIX"}}},
    )

    assert not outcome.ok
    assert "HND to PHL leaves from KIX, but after PHL to HND the traveller is at HND" in (
        outcome.content
    )


# ---------------------------------------------------------------------------
# Stage gating
# ---------------------------------------------------------------------------

_COMMON = ["update_item", "remove_item", "web_search"]


@pytest.mark.parametrize(
    ("stage", "own"),
    [
        ("basics", ["set_plan_details"]),
        ("flights", ["choose_flights", "add_round_trip", "add_flight"]),
        ("lodging", ["add_lodging"]),
        ("activities", ["add_activity"]),
        ("ground", ["add_transfer"]),
        ("review", []),
    ],
)
def test_each_stage_offers_its_own_tools_and_the_common_ones(
    stage: PlanStage, own: list[str]
) -> None:
    assert [spec.name for spec in specs_for_stage(stage)] == [*own, *_COMMON]


async def test_a_tool_from_another_stage_is_refused(ctx: ToolContext) -> None:
    outcome = await _stage(replace(ctx, stage="flights"), "add_lodging", HOTEL_ARGS)

    assert not outcome.ok
    assert not outcome.prepared
    assert outcome.content == (
        "Error: add_lodging is not available in the flights stage (it belongs to the lodging "
        "stage). Only the user can change stages with the Next/Back buttons; tell them which "
        "stage this belongs to."
    )


async def test_an_unknown_tool_lists_only_the_stage_tools(ctx: ToolContext) -> None:
    outcome = await _stage(replace(ctx, stage="review"), "book_hotel", {})

    assert outcome.content == (
        "Error: Unknown tool 'book_hotel'. Available tools: update_item, remove_item, web_search"
    )


async def test_common_tools_work_in_every_stage(ctx: ToolContext) -> None:
    hotel_id = await _add_hotel(ctx)

    outcome = await _stage(replace(ctx, stage="review"), "remove_item", {"item_id": hotel_id})

    assert outcome.ok, outcome.content


async def test_an_approved_change_applies_whatever_the_stage(ctx: ToolContext) -> None:
    # A card staged in the lodging stage is approved after the user went back to flights.
    staged = await _stage(replace(ctx, stage="lodging"), "add_lodging", HOTEL_ARGS)

    outcome = await apply_change(replace(ctx, stage="flights"), _change(staged))

    assert outcome.ok, outcome.content


# ---------------------------------------------------------------------------
# Plausibility checks: overnight stays and cited sources
# ---------------------------------------------------------------------------

_OKURA_URL = "https://www.hotelokura.co.jp/tokyo/"
_RESULTS = [
    SearchResult(title="The Okura Tokyo", url=_OKURA_URL, snippet="From $400 a night"),
    SearchResult(title="Park Hyatt Tokyo", url="https://www.hyatt.com/park-hyatt/tokyo"),
]
_EXACT = {"amount": 1600, "currency": "USD", "basis": "exact"}


def _gated(ctx: ToolContext) -> ToolContext:
    """The context an agent turn runs with: its own (empty) list of web results."""
    return replace(ctx, evidence=[])


async def _searched(ctx: ToolContext, fake_search: FakeSearchProvider) -> ToolContext:
    gated = _gated(ctx)
    fake_search.queue(_RESULTS)
    assert (await _stage(gated, "web_search", {"query": "hotels", "kind": "hotels"})).ok
    return gated


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2027-05-03T19:00:00", "2027-05-04T00:30:00"),  # a dinner across midnight
        ("2027-05-03T09:00:00", "2027-05-03T22:00:00"),  # a day room
    ],
)
async def test_lodging_must_be_an_overnight_stay(ctx: ToolContext, start: str, end: str) -> None:
    outcome = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "start": start, "end": end})

    assert not outcome.ok and not outcome.prepared
    assert "lodging is for overnight stays" in outcome.content
    assert "activity (activities stage)" in outcome.content


async def test_moving_lodging_so_it_is_not_overnight_is_rejected(ctx: ToolContext) -> None:
    hotel_id = await _add_hotel(ctx)

    outcome = await _stage(
        ctx, "update_item", {"item_id": hotel_id, "changes": {"end": "2027-05-02T20:00:00"}}
    )

    assert not outcome.ok
    assert "overnight stays" in outcome.content


async def test_a_placeholder_url_is_rejected_even_without_a_search(ctx: ToolContext) -> None:
    arguments = {**HOTEL_ARGS, "source_url": "https://www.example.com/hotel-okura"}

    outcome = await _stage(_gated(ctx), "add_lodging", arguments)

    assert not outcome.ok
    assert "is made up. Never invent a URL" in outcome.content


async def test_a_url_no_search_found_is_rejected(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    gated = await _searched(ctx, fake_search)

    outcome = await _stage(
        gated, "add_lodging", {**HOTEL_ARGS, "source_url": "https://okura.jp/booking"}
    )

    assert not outcome.ok
    assert "is not from a web_search result" in outcome.content
    assert _OKURA_URL.rstrip("/") in outcome.content  # the URLs it could cite


async def test_a_url_without_any_search_this_turn_is_rejected(ctx: ToolContext) -> None:
    outcome = await _stage(
        _gated(ctx), "add_lodging", {**HOTEL_ARGS, "source_url": "https://okura.jp/booking"}
    )

    assert not outcome.ok
    assert "none ran this turn" in outcome.content


async def test_a_url_from_the_search_is_accepted(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    gated = await _searched(ctx, fake_search)

    # Without the trailing slash, as models often copy it.
    arguments = {**HOTEL_ARGS, "source_url": f" {_OKURA_URL.rstrip('/')}", "cost": _EXACT}
    outcome = await _stage(gated, "add_lodging", arguments)

    assert outcome.ok, outcome.content


async def test_a_known_source_needs_no_new_search(ctx: ToolContext) -> None:
    known = replace(_gated(ctx), known_sources=frozenset({_OKURA_URL.rstrip("/")}))

    outcome = await _stage(known, "add_lodging", {**HOTEL_ARGS, "source_url": _OKURA_URL})

    assert outcome.ok, outcome.content


async def test_a_wrong_url_is_rejected_naming_the_known_sources(ctx: ToolContext) -> None:
    # qwen3:8b cited the hotel's own site in place of the result (from an earlier turn)
    # that listed it. With no search this turn, the error names what it may cite instead.
    known = replace(_gated(ctx), known_sources=frozenset({_OKURA_URL.rstrip("/")}))

    outcome = await _stage(
        known, "add_lodging", {**HOTEL_ARGS, "source_url": "https://okura.jp/booking"}
    )

    assert not outcome.ok
    assert "is not from a web_search result" in outcome.content
    assert _OKURA_URL.rstrip("/") in outcome.content
    assert "none ran this turn" not in outcome.content


async def test_a_web_search_reports_its_result_urls_as_sources(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    gated = _gated(ctx)
    fake_search.queue(_RESULTS)

    outcome = await _stage(gated, "web_search", {"query": "hotels", "kind": "hotels"})

    assert outcome.ok
    # Normalised like a cited source_url (no trailing slash), in result order.
    assert outcome.sources == (_OKURA_URL.rstrip("/"), "https://www.hyatt.com/park-hyatt/tokyo")


async def test_an_offline_search_result_may_be_cited(ctx: ToolContext) -> None:
    # The fake search provider's canned results are on example.com.
    gated = _gated(ctx)
    assert (await _stage(gated, "web_search", {"query": "hotels", "kind": "hotels"})).ok
    url = gated.evidence[0].url if gated.evidence else ""

    outcome = await _stage(gated, "add_lodging", {**HOTEL_ARGS, "source_url": url})

    assert url.startswith("https://example.com/")
    assert outcome.ok, outcome.content


async def test_an_exact_price_after_a_search_must_cite_it(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    gated = await _searched(ctx, fake_search)

    outcome = await _stage(gated, "add_lodging", {**HOTEL_ARGS, "cost": _EXACT})

    assert not outcome.ok
    assert 'cost is marked "exact" but has no source_url' in outcome.content
    estimate = {**_EXACT, "basis": "estimate"}
    assert (await _stage(gated, "add_lodging", {**HOTEL_ARGS, "cost": estimate})).ok


async def test_an_exact_price_without_a_search_is_the_users(ctx: ToolContext) -> None:
    outcome = await _stage(_gated(ctx), "add_lodging", {**HOTEL_ARGS, "cost": _EXACT})

    assert outcome.ok, outcome.content


async def test_an_update_citing_an_unfound_url_is_rejected(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    hotel_id = await _add_hotel(ctx)
    gated = await _searched(ctx, fake_search)

    outcome = await _stage(
        gated,
        "update_item",
        {"item_id": hotel_id, "changes": {"source_url": "https://okura.jp/booking"}},
    )

    assert not outcome.ok
    assert "is not from a web_search result" in outcome.content


async def test_choose_flights_skips_the_source_checks_after_a_web_search(
    ctx: ToolContext, fake_search: FakeSearchProvider, fake_flights: FakeFlightProvider
) -> None:
    await _ready_for_flights(ctx)
    gated = await _searched(ctx, fake_search)
    fake_flights.queue([_FAST], [_HOME])

    # The legs have exact prices from the flight search and no source_url.
    outcome = await _stage(gated, "choose_flights", {"to_airports": ["HND"]})

    assert outcome.ok, outcome.content
    assert len(outcome.prepared) == 2


async def test_approving_skips_the_source_checks(
    ctx: ToolContext, fake_search: FakeSearchProvider
) -> None:
    gated = await _searched(ctx, fake_search)
    staged = await _stage(gated, "add_lodging", {**HOTEL_ARGS, "source_url": _OKURA_URL})

    # On approval there is no evidence (a new request): the change was checked when staged.
    outcome = await check_change(ctx, _change(staged))

    assert ctx.evidence is None
    assert outcome.ok, outcome.content


def test_urls_in_finds_pasted_links() -> None:
    text = "Book https://okura.jp/rooms/, or (https://hyatt.com/tokyo). Not www.x.com"

    assert urls_in(text) == {"https://okura.jp/rooms", "https://hyatt.com/tokyo"}
