from dataclasses import replace
from datetime import date
from typing import Any
from uuid import UUID

import pytest

from features.chat.schemas import ProposedChange
from features.chat.tools import TOOLS, ToolContext, ToolOutcome, apply_change, check_change
from features.itinerary import service as itinerary_service
from features.plans import service as plans_service
from features.plans.schemas import PlanUpdate
from tests.features.chat.conftest import HOTEL_ARGS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _proposed(name: str, arguments: dict[str, Any]) -> ProposedChange:
    return ProposedChange(call_id="call_1", name=name, arguments=arguments, summary=name)


async def _check(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """Validate the change, write nothing."""
    return await check_change(ctx, _proposed(name, arguments))


def _change(outcome: ToolOutcome) -> ProposedChange:
    """The checked change as stored (normalised arguments), to stage or apply."""
    assert outcome.prepared, outcome.summary
    (prepared,) = outcome.prepared
    return ProposedChange(
        call_id="call_1",
        name=prepared.name,
        arguments=prepared.arguments,
        summary=prepared.summary,
    )


async def _run(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolOutcome:
    """Check the change and, if it validated, apply it from the normalised arguments, as a
    pick does."""
    checked = await _check(ctx, name, arguments)
    if not checked.ok:
        return checked
    return await apply_change(ctx, _change(checked))


async def _add_hotel(ctx: ToolContext) -> str:
    outcome = await _run(ctx, "add_lodging", HOTEL_ARGS)
    assert outcome.ok, outcome.summary
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    return next(str(item.id) for item in items if item.type == "lodging")


async def _update_plan(ctx: ToolContext, **fields: Any) -> None:
    patch = PlanUpdate.model_validate(fields)
    await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, patch)


async def _set_budget(ctx: ToolContext, amount: int, **details: Any) -> None:
    await _update_plan(ctx, budget={"amount": amount, "currency": "USD"}, **details)


def _flight(from_iata: str, to_iata: str, day: str, direction: str = "outbound") -> dict[str, Any]:
    return {
        "title": f"{from_iata} to {to_iata}",
        "carrier": "ANA",
        "start": f"{day}T10:00:00",
        "end": f"{day}T12:00:00",
        "timezone": "Asia/Tokyo",
        "from_place": {"iata": from_iata},
        "to_place": {"iata": to_iata},
        "direction": direction,
    }


def test_the_changes() -> None:
    assert set(TOOLS) == {
        "add_flight",
        "add_lodging",
        "add_activity",
        "add_transfer",
        "update_item",
        "remove_item",
    }


# ---------------------------------------------------------------------------
# Checking writes nothing
# ---------------------------------------------------------------------------


async def test_checking_a_change_writes_nothing(ctx: ToolContext) -> None:
    before = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    version = before.version

    outcome = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "room_type": None})

    assert outcome.ok and outcome.prepared
    assert outcome.summary == "Add lodging “Hotel Okura” on 2027-05-02 15:00"
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []
    after = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    assert after.version == version


async def test_checked_arguments_are_normalised(ctx: ToolContext) -> None:
    outcome = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "room_type": None})

    change = _change(outcome)
    # Nulls and defaults are left out, and `type` is implied by the change's name.
    assert "room_type" not in change.arguments and "status" not in change.arguments
    assert "type" not in change.arguments
    # Times come back localised to their timezone, which re-validates to the same instant.
    assert change.arguments["start"] == "2027-05-02T15:00:00+09:00"


@pytest.mark.parametrize(
    ("name", "arguments", "summary"),
    [
        ("remove_item", None, "Remove “Hotel Okura”"),
        ("update_item", {"end": "2027-05-07T11:00:00"}, "Change “Hotel Okura”: end"),
    ],
)
async def test_summaries_name_the_change(
    ctx: ToolContext, name: str, arguments: dict[str, Any] | None, summary: str
) -> None:
    item_id = await _add_hotel(ctx)
    call = {"item_id": item_id} if arguments is None else {"item_id": item_id, "changes": arguments}

    outcome = await _check(ctx, name, call)

    assert outcome.ok, outcome.summary
    assert outcome.summary == summary


async def test_applying_a_change_to_a_removed_item_fails(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)
    change = _change(
        await _check(ctx, "update_item", {"item_id": item_id, "changes": {"notes": "x"}})
    )
    await itinerary_service.remove_item(ctx.session, ctx.user_id, ctx.plan_id, UUID(item_id))

    checked = await check_change(ctx, change)
    applied = await apply_change(ctx, change)

    assert not checked.ok and not applied.ok
    assert applied.summary == "That item is no longer in this plan"


async def test_applying_a_change_that_is_now_in_the_past_fails(ctx: ToolContext) -> None:
    change = _change(await _check(ctx, "add_lodging", HOTEL_ARGS))
    later = replace(ctx, today=date(2027, 6, 1))

    outcome = await apply_change(later, change)

    assert not outcome.ok
    assert "start 2027-05-02 is in the past" in outcome.summary
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_an_unknown_change_is_refused(ctx: ToolContext) -> None:
    outcome = await apply_change(ctx, _proposed("book_flight", {}))

    assert not outcome.ok and outcome.summary == "'book_flight' is not a plan change"


# ---------------------------------------------------------------------------
# Applying
# ---------------------------------------------------------------------------


async def test_add_lodging_creates_an_assistant_item(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "add_lodging", {**HOTEL_ARGS, "room_type": None})

    assert outcome.ok
    assert outcome.summary == "Added lodging “Hotel Okura”"
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.type == "lodging"
    assert item.created_by == "assistant"


async def test_update_item_merges_changes(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(
        ctx,
        "update_item",
        {"item_id": item_id, "changes": {"end": "2027-05-07T11:00:00", "notes": None}},
    )

    assert outcome.ok, outcome.summary
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    assert item.end.isoformat() == "2027-05-07T11:00:00+09:00"
    assert item.title == "Hotel Okura"


async def test_remove_item(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(ctx, "remove_item", {"item_id": item_id})

    assert outcome.ok
    assert outcome.summary == "Removed “Hotel Okura”"
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


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


async def test_with_a_budget_every_item_needs_a_cost(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)

    outcome = await _check(ctx, "add_lodging", HOTEL_ARGS)

    assert not outcome.ok and not outcome.prepared
    assert "Hotel Okura has no cost" in outcome.summary


async def test_without_a_budget_costs_are_optional(ctx: ToolContext) -> None:
    assert (await _check(ctx, "add_lodging", HOTEL_ARGS)).ok


async def test_a_zero_budget_is_no_budget(ctx: ToolContext) -> None:
    await _set_budget(ctx, 0)

    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    assert plan.budget is None
    pricey = {**HOTEL_ARGS, "cost": {"amount": 900, "currency": "USD"}}
    assert (await _check(ctx, "add_lodging", HOTEL_ARGS)).ok
    assert (await _check(ctx, "add_lodging", pricey)).ok


async def test_a_zero_budget_saved_before_it_meant_none_is_ignored(ctx: ToolContext) -> None:
    plan = await plans_service.get_plan(ctx.session, ctx.user_id, ctx.plan_id)
    plan.budget = {"amount": "0", "currency": "USD"}
    await ctx.session.flush()

    outcome = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 900}})

    assert outcome.ok, outcome.summary


async def test_a_cost_in_another_currency_than_the_budget_is_rejected(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)

    outcome = await _check(
        ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 30000, "currency": "JPY"}}
    )

    assert not outcome.ok
    assert "costs 30,000.00 JPY, but the budget is in USD" in outcome.summary


async def test_an_item_that_breaks_the_budget_is_rejected(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})

    within = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 200}})
    over = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 300}})

    assert within.ok and within.prepared
    assert not over.ok and not over.prepared
    assert over.summary == (
        "With Hotel Okura the trip costs total 1,100.00 USD, 100.00 USD over the "
        "1,000.00 USD budget"
    )


async def test_an_update_counts_the_new_cost_in_place_of_the_old_one(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)

    cheaper = await _check(
        ctx, "update_item", {"item_id": str(item.id), "changes": {"cost": {"amount": 950}}}
    )
    dearer = await _check(
        ctx, "update_item", {"item_id": str(item.id), "changes": {"cost": {"amount": 1200}}}
    )

    assert cheaper.ok
    assert not dearer.ok and "costs total 1,200.00 USD" in dearer.summary


async def test_an_update_that_does_not_touch_the_cost_is_not_checked(ctx: ToolContext) -> None:
    # An item from before the budget was set has no cost; moving it is still allowed (the
    # timeline flags the missing cost instead).
    item_id = await _add_hotel(ctx)
    await _set_budget(ctx, 1000)

    outcome = await _check(
        ctx, "update_item", {"item_id": item_id, "changes": {"end": "2027-05-07T11:00:00"}}
    )

    assert outcome.ok, outcome.summary


async def test_staged_changes_count_towards_the_budget(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    first = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 600}})
    staged = replace(ctx, staged=[_change(first)])

    second = await _check(staged, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 500}})
    repeat = await _check(staged, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 600}})

    assert not second.ok and "costs total 1,100.00 USD" in second.summary
    # Checking the change that's already staged doesn't count it twice.
    assert repeat.ok


async def test_staged_removals_free_up_budget(ctx: ToolContext) -> None:
    await _set_budget(ctx, 1000)
    await _run(ctx, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 800}})
    (item,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    removal = await _check(ctx, "remove_item", {"item_id": str(item.id)})
    staged = replace(ctx, staged=[_change(removal)])

    outcome = await _check(staged, "add_lodging", {**HOTEL_ARGS, "cost": {"amount": 900}})

    assert outcome.ok, outcome.summary


async def test_a_meal_over_the_per_person_limit_is_rejected(ctx: ToolContext) -> None:
    limit = {"category": "food", "max_per_person": {"amount": 200, "currency": "USD"}}
    await _update_plan(ctx, travelers={"adults": 2}, limits=[limit])

    within = await _check(ctx, "add_activity", {**DINNER_ARGS, "cost": {"amount": 380}})
    over = await _check(ctx, "add_activity", {**DINNER_ARGS, "cost": {"amount": "504.24"}})
    no_cost = await _check(ctx, "add_activity", DINNER_ARGS)

    assert within.ok, within.summary
    assert not over.ok
    assert (
        "costs 504.24 USD (252.12 USD a person for 2), over the food limit of 200.00 USD a person"
        in over.summary
    )
    # A limit needs a cost to check, even without a budget...
    assert not no_cost.ok and "has no cost" in no_cost.summary
    # ...but only for the limited category.
    assert (await _check(ctx, "add_lodging", HOTEL_ARGS)).ok


# ---------------------------------------------------------------------------
# Timezones
# ---------------------------------------------------------------------------

# PHL 15:00 (New York) → HND 07:00 the next morning (Tokyo).
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

    outcome = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "timezone": "America/New_York"})

    assert not outcome.ok
    assert outcome.summary == (
        "Hotel Okura is in America/New_York time, but the traveller is in Asia/Tokyo then"
    )


async def test_an_item_in_the_destination_timezone_is_accepted(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _PHL_HND)

    outcome = await _check(ctx, "add_lodging", HOTEL_ARGS)

    assert outcome.ok, outcome.summary


async def test_a_staged_flight_sets_the_timezone_for_what_follows(ctx: ToolContext) -> None:
    flight = await _check(ctx, "add_flight", _PHL_HND)
    staged = replace(ctx, staged=[_change(flight)])

    outcome = await _check(staged, "add_lodging", {**HOTEL_ARGS, "timezone": "America/New_York"})

    assert not outcome.ok and "traveller is in Asia/Tokyo" in outcome.summary


async def test_moving_an_item_into_the_wrong_timezone_is_rejected(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _PHL_HND)
    hotel_id = await _add_hotel(ctx)

    outcome = await _check(
        ctx, "update_item", {"item_id": hotel_id, "changes": {"timezone": "America/New_York"}}
    )

    assert not outcome.ok and "traveller is in Asia/Tokyo" in outcome.summary


# ---------------------------------------------------------------------------
# Validation failures are reported, never raised
# ---------------------------------------------------------------------------


async def test_add_flight_rejects_a_zero_length_flight(ctx: ToolContext) -> None:
    # PHL 10:00 EST is exactly 00:00 JST the next day.
    flight = {
        **_flight("PHL", "HND", "2026-12-15"),
        "timezone": "America/New_York",
        "end": "2026-12-16T00:00:00",
        "end_timezone": "Asia/Tokyo",
    }

    outcome = await _run(ctx, "add_flight", flight)

    assert not outcome.ok
    assert "at least 15 minute(s)" in outcome.summary
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_add_transfer_rejects_an_airport_code_that_names_another_airport(
    ctx: ToolContext,
) -> None:
    transfer = {
        "title": "Car from Haneda",
        "start": "2027-05-01T09:00:00",
        "end": "2027-05-01T11:00:00",
        "timezone": "Asia/Tokyo",
        "mode": "taxi",
        "from_place": {"name": "Tokyo Haneda Airport", "iata": "NRT"},
        "to_place": {"name": "Hotel Okura"},
    }

    outcome = await _check(ctx, "add_transfer", transfer)

    assert not outcome.ok and not outcome.prepared
    assert "'Tokyo Haneda Airport' is HND, not NRT" in outcome.summary


async def test_invalid_arguments_are_summarized(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "add_lodging", {**HOTEL_ARGS, "end": "2027-05-01T10:00:00"})

    assert not outcome.ok
    assert outcome.summary.startswith("Invalid arguments for add_lodging:")
    assert "end must be at or after start" in outcome.summary
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_unknown_item_id(ctx: ToolContext) -> None:
    outcome = await _run(ctx, "remove_item", {"item_id": "00000000-0000-0000-0000-000000000000"})

    assert not outcome.ok
    assert outcome.summary == "That item is no longer in this plan"


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


async def test_add_item_rejects_a_past_start(ctx: ToolContext) -> None:
    outcome = await _run(
        ctx,
        "add_lodging",
        {**HOTEL_ARGS, "start": "2026-05-02T15:00:00", "end": "2026-05-06T11:00:00"},
    )

    assert not outcome.ok
    assert outcome.summary == "start 2026-05-02 is in the past (today is 2026-10-01)"
    assert await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id) == []


async def test_update_item_rejects_a_past_start(ctx: ToolContext) -> None:
    item_id = await _add_hotel(ctx)

    outcome = await _run(
        ctx, "update_item", {"item_id": item_id, "changes": {"start": "2026-05-02T15:00:00"}}
    )

    assert not outcome.ok
    assert "start 2026-05-02 is in the past" in outcome.summary


# ---------------------------------------------------------------------------
# Flight routes (no overlaps; each flight leaves from where the last one landed)
# ---------------------------------------------------------------------------


async def test_a_second_flight_out_is_rejected(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", {**_flight("PHL", "HND", "2027-05-01"), "title": "Pricey"})
    cheaper = {**_flight("PHL", "HND", "2027-05-01"), "title": "Cheaper"}
    cheaper["start"], cheaper["end"] = "2027-05-01T11:00:00", "2027-05-01T13:00:00"

    outcome = await _check(ctx, "add_flight", cheaper)

    assert not outcome.ok
    assert "Cheaper overlaps the flight Pricey" in outcome.summary


async def test_a_staged_removal_frees_the_flight_slot(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _flight("PHL", "HND", "2027-05-01"))
    (old,) = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    removal = _change(await _check(ctx, "remove_item", {"item_id": str(old.id)}))

    outcome = await _check(
        replace(ctx, staged=[removal]), "add_flight", _flight("PHL", "HND", "2027-05-01")
    )

    assert outcome.ok, outcome.summary


async def test_moving_a_flight_so_it_breaks_the_route_is_rejected(ctx: ToolContext) -> None:
    await _run(ctx, "add_flight", _flight("PHL", "HND", "2027-05-01"))
    await _run(ctx, "add_flight", _flight("HND", "PHL", "2027-05-08", direction="return"))
    items = await itinerary_service.list_items(ctx.session, ctx.user_id, ctx.plan_id)
    home = next(item for item in items if item.title == "HND to PHL")

    outcome = await _check(
        ctx,
        "update_item",
        {"item_id": str(home.id), "changes": {"from_place": {"iata": "KIX"}}},
    )

    assert not outcome.ok
    assert "HND to PHL leaves from KIX, but after PHL to HND the traveller is at HND" in (
        outcome.summary
    )


# ---------------------------------------------------------------------------
# Overnight stays
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2027-05-03T19:00:00", "2027-05-04T00:30:00"),  # a dinner across midnight
        ("2027-05-03T09:00:00", "2027-05-03T22:00:00"),  # a day room
    ],
)
async def test_lodging_must_be_an_overnight_stay(ctx: ToolContext, start: str, end: str) -> None:
    outcome = await _check(ctx, "add_lodging", {**HOTEL_ARGS, "start": start, "end": end})

    assert not outcome.ok and not outcome.prepared
    assert "lodging is for overnight stays" in outcome.summary


async def test_moving_lodging_so_it_is_not_overnight_is_rejected(ctx: ToolContext) -> None:
    hotel_id = await _add_hotel(ctx)

    outcome = await _check(
        ctx, "update_item", {"item_id": hotel_id, "changes": {"end": "2027-05-02T20:00:00"}}
    )

    assert not outcome.ok
    assert "overnight stays" in outcome.summary
