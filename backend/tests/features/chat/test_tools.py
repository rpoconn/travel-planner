import json
from dataclasses import replace
from datetime import date
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
)
from features.itinerary import service as itinerary_service
from features.plans import service as plans_service
from shared.llm.types import LLMToolCall
from shared.search.fake import FakeSearchProvider
from shared.search.types import SearchError, SearchResult
from tests.features.chat.conftest import HOTEL_ARGS


async def _stage(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """What the agent does: validate the call, write nothing."""
    return await execute(ctx, LLMToolCall(name=name, arguments=arguments))


def _change(outcome: ToolOutcome) -> ProposedChange:
    assert outcome.prepared is not None, outcome.content
    prepared = outcome.prepared
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
    if not staged.ok or staged.prepared is None:
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

    assert outcome.ok and outcome.prepared is not None
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


async def test_staging_an_item_that_breaks_the_budget_says_so(ctx: ToolContext) -> None:
    await _run(ctx, "set_plan_details", {"budget": {"amount": 1000, "currency": "USD"}})
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})

    within = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 200}})
    over = await _stage(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 300}})

    assert "budget" not in within.summary
    note = "(puts the trip at 1,100.00 USD, over the 1,000.00 USD budget)"
    assert over.summary.endswith(note)
    assert note in over.content  # the model is told too
    assert _change(over).summary.endswith(note)  # and the proposal card shows it


async def test_updating_a_cost_counts_it_in_place_of_the_old_one(ctx: ToolContext) -> None:
    await _run(ctx, "set_plan_details", {"budget": {"amount": 1000, "currency": "USD"}})
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)

    cheaper = await _stage(
        ctx, "update_item", {"item_id": str(item.id), "changes": {"cost": {"amount": 900}}}
    )
    dearer = await _stage(
        ctx, "update_item", {"item_id": str(item.id), "changes": {"cost": {"amount": 1200}}}
    )

    assert "budget" not in cheaper.summary
    assert dearer.summary.endswith("(puts the trip at 1,200.00 USD, over the 1,000.00 USD budget)")


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

    assert not outcome.ok and outcome.prepared is None
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
