"""Commands: resolving item numbers, checking every command against the plan's rules with
the earlier ones staged, writing all or nothing, and the summaries the transcript shows.

The plan is the Lisbon trip at the ground stage (`planning_http.reach`), with no search
preferences: its items, in time order, are 1 "Friend's flat" (May 1-8) and 2 "Dinner in"
(May 2).
"""

from collections.abc import Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import JsonValue, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from features.chat.tools import ToolContext
from features.itinerary import service as itinerary_service
from features.itinerary.schemas import (
    ActivityItemCreate,
    ItemStatus,
    ItemType,
    ItineraryItem,
    TransferItemCreate,
)
from features.planning import service as planning_service
from features.planning.commands import check_and_apply, describe_changes, dry_run
from features.planning.flight_search import outbound_flights
from features.planning.schemas import (
    MAX_COMMANDS,
    AgentCommand,
    AppliedChange,
    Candidate,
    CandidateChange,
    ChangesRecord,
    Command,
    ProposalChange,
    RejectedChange,
    RunSearch,
    TravelPlan,
)
from features.planning.search_types import SearchContext
from features.plans import service as plans_service
from features.plans.schemas import (
    FlightPreferences,
    LodgingPreferences,
    PlanPreferences,
    PlanStage,
    PlanUpdate,
    SlotName,
)
from shared.domain import Cost, Money, Place
from shared.flights.fake import FakeFlightProvider
from shared.hotels.fake import FakeHotelProvider
from shared.places.fake import FakePlaceProvider
from tests.features.planning.planning_http import BASICS, FLAT, LISBON_TZ, create_plan, reach

# Fixed so the trip (May 2027) stays in the future.
TODAY = date(2026, 11, 1)

COMMANDS: TypeAdapter[list[Command]] = TypeAdapter(list[Command])
AGENT_COMMANDS: TypeAdapter[list[AgentCommand]] = TypeAdapter(list[AgentCommand])
FLAT_REF, DINNER_REF = 1, 2


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
async def plan_id(client: AsyncClient, headers: dict[str, str]) -> str:
    return await create_plan(client, headers, **BASICS)


@pytest.fixture
async def ctx(
    app: FastAPI,
    client: AsyncClient,
    headers: dict[str, str],
    plan_id: str,
    session: AsyncSession,
) -> ToolContext:
    await reach(app, client, headers, plan_id, PlanStage.GROUND)
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    plan = TravelPlan.model_validate_json(response.content)
    return ToolContext(session=session, user_id=plan.user_id, plan_id=plan.id, today=TODAY)


async def _plan(ctx: ToolContext) -> TravelPlan:
    return await planning_service.get_plan_snapshot(ctx.session, ctx.user_id, ctx.plan_id)


async def _run(ctx: ToolContext, *commands: dict[str, JsonValue]) -> ChangesRecord:
    """Read the commands as the model's reply would be read, then check and apply them."""
    return await check_and_apply(ctx, await _plan(ctx), COMMANDS.validate_python(commands))


async def _item(ctx: ToolContext, ref: int) -> ItineraryItem:
    return (await _plan(ctx)).items[ref - 1]


def _remove(ref: int) -> dict[str, JsonValue]:
    return {"type": "remove_item", "item": ref}


def _update(ref: int, **changes: JsonValue) -> dict[str, JsonValue]:
    return {"type": "update_item", "item": ref, "changes": changes}


def _stage(action: str, stage: str | None = None) -> dict[str, JsonValue]:
    return {"type": "change_stage", "action": action, "stage": stage}


TRAM = Place(name="Tram 28 stop", city="Lisbon")


async def _add_activity(
    ctx: ToolContext,
    title: str,
    day: int,
    status: ItemStatus,
    place: Place | None = None,
    hours: tuple[int, int] = (10, 12),
) -> None:
    """An 80 USD activity, 10:00-12:00 unless `hours` says otherwise, added past the rules
    (as if it was there before the budget). At the flat unless `place` says otherwise."""
    data = ActivityItemCreate(
        type=ItemType.ACTIVITY,
        title=title,
        start=datetime(2027, 5, day, hours[0]),
        end=datetime(2027, 5, day, hours[1]),
        timezone=LISBON_TZ,
        place=place or Place.model_validate(FLAT),
        category="tour",
        status=status,
        cost=Cost(amount=Decimal(80), currency="USD"),
    )
    await itinerary_service.add_item(ctx.session, ctx.user_id, ctx.plan_id, data)


async def _add_taxi(
    ctx: ToolContext, title: str, start: datetime, from_place: Place, to_place: Place
) -> None:
    """A half-hour taxi."""
    data = TransferItemCreate(
        type=ItemType.TRANSFER,
        title=title,
        start=start,
        end=start + timedelta(minutes=30),
        timezone=LISBON_TZ,
        mode="taxi",
        from_place=from_place,
        to_place=to_place,
    )
    await itinerary_service.add_item(ctx.session, ctx.user_id, ctx.plan_id, data)


async def _add_tram_tour(ctx: ToolContext) -> None:
    """A tour on May 3 away from the flat, with a taxi there and back: items 3-5."""
    flat = Place.model_validate(FLAT)
    await _add_activity(ctx, "Tram tour", 3, "proposed", place=TRAM)
    await _add_taxi(ctx, "Taxi there", datetime(2027, 5, 3, 9, 30), flat, TRAM)
    await _add_taxi(ctx, "Taxi back", datetime(2027, 5, 3, 12), TRAM, flat)


async def _titles(ctx: ToolContext) -> list[str]:
    return [item.title for item in (await _plan(ctx)).items]


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        {"type": "add_activity", "item": 1},  # no adds
        {"type": "remove_item", "item": 0},  # numbers start at 1
        {"type": "remove_item", "item": "a1b2"},  # never an id
        _update(1, cost={"amount": 10}),  # costs come from searches
        _update(1, status="cancelled"),  # taking an item off is remove_item
        # One basic per command.
        {"type": "set_dates", "start": "2027-05-03", "end": "2027-05-10", "amount": 1},
        {"type": "set_dates"},
        # Fields a command always knows are required, so the schema stays small.
        {"type": "set_budget", "amount": 3000},
        {"type": "set_travelers", "adults": 2},
        # A place carries only what the searches read.
        {"type": "set_destination", "name": "Lisbon", "lat": 38.7},
        {"type": "set_basics", "budget": {"amount": 1, "currency": "USD"}},  # the old shape
        _stage("next"),  # moving on is the engine's job
    ],
)
def test_a_command_outside_the_vocabulary_is_malformed(command: dict[str, JsonValue]) -> None:
    with pytest.raises(ValidationError):
        COMMANDS.validate_python([command])


# ---------------------------------------------------------------------------
# Items
# ---------------------------------------------------------------------------


async def test_removing_an_item_deletes_it(ctx: ToolContext) -> None:
    version = (await _plan(ctx)).version
    dinner = await _item(ctx, DINNER_REF)

    record = await _run(ctx, _remove(DINNER_REF))

    assert record == ChangesRecord(
        applied=[
            AppliedChange(name="remove_item", summary="Removed “Dinner in”", item_id=dinner.id)
        ],
        # The only activity: the planner says what the trip needs now, and how to fix it.
        left_open=[
            "The trip needs at least one activity now. Pick something else to do, or tell me "
            "what you're into"
        ],
    )
    assert describe_changes(record) == (
        "Removed “Dinner in”. The trip needs at least one activity now. Pick something "
        "else to do, or tell me what you're into."
    )
    plan = await _plan(ctx)
    assert [item.title for item in plan.items] == ["Friend's flat"]
    assert plan.version > version


async def test_removing_an_item_takes_the_rides_to_and_from_it(ctx: ToolContext) -> None:
    await _add_tram_tour(ctx)
    assert await _titles(ctx) == [
        "Friend's flat",
        "Dinner in",
        "Taxi there",
        "Tram tour",
        "Taxi back",
    ]

    record = await _run(ctx, _remove(4))

    assert describe_changes(record) == "Removed “Tram tour” and “Taxi there”, “Taxi back”."
    assert await _titles(ctx) == ["Friend's flat", "Dinner in"]


async def test_removing_a_ride_that_goes_with_an_earlier_removal(ctx: ToolContext) -> None:
    await _add_tram_tour(ctx)

    # "Remove the tour and the taxi there."
    record = await _run(ctx, _remove(4), _remove(3))

    assert [change.summary for change in record.applied] == [
        "Removed “Tram tour” and “Taxi there”, “Taxi back”",
        "Removed “Taxi there”",
    ]
    assert await _titles(ctx) == ["Friend's flat", "Dinner in"]


async def test_a_ride_changed_first_stays_when_its_item_goes(ctx: ToolContext) -> None:
    await _add_tram_tour(ctx)

    record = await _run(ctx, _update(5, title="Taxi home"), _remove(4))

    assert [change.summary for change in record.applied] == [
        "Changed “Taxi back”: title",
        "Removed “Tram tour” and “Taxi there”",
    ]
    assert await _titles(ctx) == ["Friend's flat", "Dinner in", "Taxi home"]


async def test_removing_a_stop_in_the_middle_says_which_ride_is_missing(
    ctx: ToolContext,
) -> None:
    # Flat → museum → taxi → lunch → flat: the taxi goes with the museum, so nothing gets
    # from the flat to lunch any more. The flat → market ride the next day was already
    # missing; this one is missing as well.
    lunch_spot = Place(name="Time Out Market", city="Lisbon")
    await _add_activity(ctx, "Museum", 3, "proposed", place=TRAM)
    await _add_taxi(ctx, "Taxi to lunch", datetime(2027, 5, 3, 12), TRAM, lunch_spot)
    await _add_activity(ctx, "Lunch", 3, "proposed", place=lunch_spot, hours=(13, 14))
    await _add_activity(ctx, "Market", 4, "proposed", place=lunch_spot)
    assert await _titles(ctx) == [
        "Friend's flat",
        "Dinner in",
        "Museum",
        "Taxi to lunch",
        "Lunch",
        "Market",
    ]

    record = await _run(ctx, _remove(3))

    assert record.left_open == [
        "Nothing gets you from Friend's flat to Time Out Market now. Pick a way to get "
        "there, or tell me how you'd like to go"
    ]
    assert describe_changes(record) == (
        "Removed “Museum” and “Taxi to lunch”. Nothing gets you from Friend's flat to "
        "Time Out Market now. Pick a way to get there, or tell me how you'd like to go."
    )


async def test_a_ride_missing_before_the_edit_is_not_named_again(ctx: ToolContext) -> None:
    await _add_tram_tour(ctx)

    # Both taxis go with the tour, so the trip is back to flat and dinner: nothing new.
    record = await _run(ctx, _remove(4))

    assert record.left_open == []


async def test_a_ride_left_missing_before_ground_transport_waits_for_it(
    ctx: ToolContext,
) -> None:
    await _add_tram_tour(ctx)
    await plans_service.set_stage(
        ctx.session, ctx.user_id, ctx.plan_id, PlanStage.ACTIVITIES, False
    )

    record = await _run(ctx, _remove(3))

    assert record.left_open == [
        "Nothing gets you from Friend's flat to Tram 28 stop now. I'll look for a ride at "
        "ground transport"
    ]


async def test_what_a_stage_not_reached_yet_asks_for_anyway_is_not_named(
    ctx: ToolContext,
) -> None:
    # At lodging, the activities stage is still to come and will ask for activities itself.
    await plans_service.set_stage(ctx.session, ctx.user_id, ctx.plan_id, PlanStage.LODGING, True)

    record = await _run(ctx, _remove(DINNER_REF))

    assert record.left_open == []


async def test_a_cancelled_item_is_not_in_the_list(ctx: ToolContext) -> None:
    await _add_activity(ctx, "Fado night", 4, "cancelled")

    record = await _run(ctx, _remove(3))

    assert record.rejected == [
        RejectedChange(summary="Remove item 3", reason="There's no item 3 in the list")
    ]


async def test_an_item_number_not_in_the_list_is_rejected(ctx: ToolContext) -> None:
    version = (await _plan(ctx)).version

    record = await _run(ctx, _remove(9))

    assert record == ChangesRecord(
        rejected=[RejectedChange(summary="Remove item 9", reason="There's no item 9 in the list")]
    )
    assert describe_changes(record) == "Couldn't remove item 9: There's no item 9 in the list."
    assert (await _plan(ctx)).version == version


async def test_updating_an_item_moves_it(ctx: ToolContext) -> None:
    record = await _run(
        ctx, _update(DINNER_REF, start="2027-05-03T20:00:00", end="2027-05-03T21:30:00")
    )

    assert [change.summary for change in record.applied] == ["Changed “Dinner in”: start, end"]
    dinner = await _item(ctx, DINNER_REF)
    # Read as wall-clock time where the item is.
    assert dinner.start.isoformat() == "2027-05-03T20:00:00+01:00"


async def test_an_update_that_breaks_the_item_is_rejected(ctx: ToolContext) -> None:
    record = await _run(ctx, _update(DINNER_REF, start="2027-05-02T22:00:00"))

    (rejection,) = record.rejected
    assert rejection.summary == "Change “Dinner in”"
    assert "end must be at or after start" in rejection.reason
    assert record.applied == []


async def test_a_second_command_on_the_same_item_is_rejected(ctx: ToolContext) -> None:
    record = await _run(ctx, _update(DINNER_REF, title="Dinner out"), _remove(DINNER_REF))

    assert record.rejected == [
        RejectedChange(summary="Remove “Dinner in”", reason="Change each item in one command")
    ]
    assert (await _item(ctx, DINNER_REF)).title == "Dinner in"


# ---------------------------------------------------------------------------
# All or nothing, and staging
# ---------------------------------------------------------------------------


async def test_nothing_is_written_when_one_command_is_rejected(ctx: ToolContext) -> None:
    version = (await _plan(ctx)).version
    # A stay of a few hours isn't a night's sleep.
    short_stay = _update(FLAT_REF, start="2027-05-01T15:00:00", end="2027-05-01T20:00:00")

    record = await _run(ctx, _remove(DINNER_REF), short_stay)

    assert record.applied == []
    (rejection,) = record.rejected
    assert rejection.summary == "Change “Friend's flat”"
    assert "overnight" in rejection.reason
    assert (await _item(ctx, DINNER_REF)).status == "proposed"
    assert (await _plan(ctx)).version == version


async def test_every_rejected_command_is_reported(ctx: ToolContext) -> None:
    record = await _run(ctx, _remove(7), _remove(8))

    assert [rejection.summary for rejection in record.rejected] == [
        "Remove item 7",
        "Remove item 8",
    ]


async def test_commands_are_checked_with_the_earlier_ones_staged(ctx: ToolContext) -> None:
    patch = PlanUpdate(budget=Money(amount=Decimal(100), currency="USD"))
    await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, patch)
    # Both together are 160 USD, over the 100 USD budget, so confirming one is turned down...
    await _add_activity(ctx, "Tram tour", 3, "proposed")
    await _add_activity(ctx, "Fado night", 4, "proposed")
    tour, fado = 3, 4

    alone = await _run(ctx, _update(fado, status="confirmed"))
    # ...but not with the other removed first.
    swapped = await _run(ctx, _remove(tour), _update(fado, status="confirmed"))

    assert alone.applied == [] and "budget" in alone.rejected[0].reason
    assert [change.summary for change in swapped.applied] == [
        "Removed “Tram tour”",
        "Changed “Fado night”: status",
    ]


async def test_too_many_commands_are_rejected(ctx: ToolContext) -> None:
    record = await _run(ctx, *[_remove(DINNER_REF)] * (MAX_COMMANDS + 1))

    assert record.rejected == [
        RejectedChange(
            summary="Change the trip", reason=f"At most {MAX_COMMANDS} changes at a time"
        )
    ]


async def test_no_commands_change_nothing(ctx: ToolContext) -> None:
    assert await _run(ctx) == ChangesRecord()


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


async def test_new_dates_are_written_with_the_old_ones_in_the_summary(ctx: ToolContext) -> None:
    dates: dict[str, JsonValue] = {"start": "2027-05-03", "end": "2027-05-10"}

    record = await _run(ctx, {"type": "set_dates", **dates})

    assert record.applied == [
        AppliedChange(
            name="set_dates",
            summary="Dates: May 1, 2027 to May 8, 2027 → May 3, 2027 to May 10, 2027",
        )
    ]
    plan = await _plan(ctx)
    assert (plan.start_date, plan.end_date) == (date(2027, 5, 3), date(2027, 5, 10))


async def test_a_first_budget_is_summarised_alone(ctx: ToolContext) -> None:
    record = await _run(ctx, {"type": "set_budget", "amount": 3000, "currency": "USD"})

    assert [change.summary for change in record.applied] == ["Budget: 3,000 USD"]


async def test_travelers_are_written_with_the_children(ctx: ToolContext) -> None:
    record = await _run(ctx, {"type": "set_travelers", "adults": 2, "children": 1})

    assert [change.name for change in record.applied] == ["set_travelers"]
    plan = await _plan(ctx)
    assert plan.travelers is not None
    assert (plan.travelers.adults, plan.travelers.children) == (2, 1)


async def test_a_new_destination_keeps_its_airport_code(ctx: ToolContext) -> None:
    record = await _run(
        ctx, {"type": "set_destination", "name": "Porto", "country": "Portugal", "iata": "OPO"}
    )

    assert [change.name for change in record.applied] == ["set_destination"]
    destination = (await _plan(ctx)).destination
    assert destination is not None
    assert (destination.name, destination.country, destination.iata) == (
        "Porto",
        "Portugal",
        "OPO",
    )


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        (
            {"type": "set_dates", "start": "2026-10-01", "end": "2026-10-05"},
            "The trip can't start in the past",
        ),
        (
            {"type": "set_dates", "start": "2027-05-08", "end": "2027-05-01"},
            "The trip has to end on or after",
        ),
        (
            {"type": "set_budget", "amount": 0, "currency": "USD"},
            "A budget has to be more than 0",
        ),
    ],
)
async def test_basics_follow_their_form_rules(
    ctx: ToolContext, value: dict[str, JsonValue], reason: str
) -> None:
    record = await _run(ctx, value)

    (rejection,) = record.rejected
    assert rejection.reason.startswith(reason)
    assert (await _plan(ctx)).start_date == date(2027, 5, 1)


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------


async def test_going_back_to_an_earlier_stage(ctx: ToolContext) -> None:
    record = await _run(ctx, _stage("go_to", "lodging"))

    assert [change.summary for change in record.applied] == ["Back to lodging"]
    assert (await _plan(ctx)).stage == PlanStage.LODGING


async def test_stage_changes_follow_on_from_each_other(ctx: ToolContext) -> None:
    record = await _run(ctx, _stage("back"), _stage("back"))

    assert [change.summary for change in record.applied] == [
        "Back to activities",
        "Back to lodging",
    ]
    assert (await _plan(ctx)).stage == PlanStage.LODGING


@pytest.mark.parametrize(
    ("command", "summary", "reason"),
    [
        (_stage("go_to", "review"), "Go back to review", "You can only go back to an earlier"),
        (_stage("go_to"), "Go back a stage", "go_to needs a stage"),
        (_stage("skip_flights"), "Skip flights", "Flights can only be skipped from"),
    ],
)
async def test_a_stage_change_the_workflow_does_not_allow(
    ctx: ToolContext, command: dict[str, JsonValue], summary: str, reason: str
) -> None:
    record = await _run(ctx, command)

    (rejection,) = record.rejected
    assert rejection.summary == summary and reason in rejection.reason
    assert (await _plan(ctx)).stage == PlanStage.GROUND


async def test_an_item_change_and_a_stage_change_go_together(ctx: ToolContext) -> None:
    flat: UUID = (await _item(ctx, FLAT_REF)).id

    record = await _run(ctx, _remove(FLAT_REF), _stage("go_to", "lodging"))

    assert record.applied == [
        AppliedChange(name="remove_item", summary="Removed “Friend's flat”", item_id=flat),
        AppliedChange(name="change_stage", summary="Back to lodging"),
    ]
    # Lodging is reached again, so the nights the flat covered are named with the fix.
    assert describe_changes(record) == (
        "Removed “Friend's flat”. Back to lodging. The trip needs a place to stay May 1 to "
        "May 8 now. Pick another place to stay, or tell me what you'd like instead."
    )


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


def _flights(**edits: JsonValue) -> dict[str, JsonValue]:
    return {"type": "set_flight_preferences", **edits}


def _lodging(**edits: JsonValue) -> dict[str, JsonValue]:
    return {"type": "set_lodging_preferences", **edits}


def _activities(**edits: JsonValue) -> dict[str, JsonValue]:
    return {"type": "set_activity_preferences", **edits}


async def test_new_preferences_are_written(ctx: ToolContext) -> None:
    version = (await _plan(ctx)).version

    record = await _run(ctx, _lodging(hotel_class="upscale"))

    assert record.applied == [
        AppliedChange(name="set_lodging_preferences", summary="Lodging preferences: Upscale hotel")
    ]
    plan = await _plan(ctx)
    assert plan.preferences.lodging == LodgingPreferences(hotel_class="upscale")
    assert plan.version > version


async def test_preferences_change_only_the_fields_given(ctx: ToolContext) -> None:
    await _run(ctx, _lodging(hotel_class="upscale", neighborhood="Alfama"))

    record = await _run(ctx, _lodging(nightly_budget={"amount": 200, "currency": "USD"}))

    assert [change.summary for change in record.applied] == [
        "Lodging preferences: Upscale hotel, in Alfama → "
        "Upscale hotel, up to 200 USD a night, in Alfama"
    ]
    lodging = (await _plan(ctx)).preferences.lodging
    assert lodging is not None and lodging.hotel_class == "upscale"


async def test_flight_preferences_keep_the_flight_out_they_pair_with(ctx: ToolContext) -> None:
    stored = PlanPreferences(flights=FlightPreferences(outbound_token="tok"))
    await plans_service.update_preferences(ctx.session, ctx.user_id, ctx.plan_id, stored)

    record = await _run(ctx, _flights(max_stops="nonstop"))

    assert [change.summary for change in record.applied] == [
        "Flight preferences: Economy cabin, any number of stops → Economy cabin, nonstop"
    ]
    flights = (await _plan(ctx)).preferences.flights
    assert flights == FlightPreferences(max_stops=0, outbound_token="tok")
    # "any" is how a command says None.
    await _run(ctx, _flights(max_stops="any"))
    flights = (await _plan(ctx)).preferences.flights
    assert flights is not None and flights.max_stops is None


async def test_preference_changes_in_one_message_build_on_each_other(ctx: ToolContext) -> None:
    record = await _run(
        ctx,
        _lodging(hotel_class="budget"),
        _lodging(kind="hostel"),
    )

    assert len(record.applied) == 2
    assert (await _plan(ctx)).preferences.lodging == LodgingPreferences(
        hotel_class="budget", kind="hostel"
    )


@pytest.mark.parametrize(
    ("command", "summary", "reason"),
    [
        (_lodging(), "Change the lodging preferences", "No changes were given"),
        # Nothing stored to merge into, and interests are required.
        (_activities(dietary="vegan"), "Change the activity preferences", "interests"),
        (
            _activities(interests=["museum", "food", "tour", "outdoors", "nightlife", "relax"]),
            "Change the activity preferences",
            "interests",
        ),
    ],
)
async def test_preferences_follow_their_form_rules(
    ctx: ToolContext, command: dict[str, JsonValue], summary: str, reason: str
) -> None:
    record = await _run(ctx, command)

    (rejection,) = record.rejected
    assert rejection.summary == summary and reason in rejection.reason
    assert (await _plan(ctx)).preferences == PlanPreferences()


@pytest.mark.parametrize(
    "command",
    [
        _lodging(hotel_class="upscale", cabin="business"),  # one stage's per command
        _lodging(stars=5),
        _lodging(nightly_budget={"amount": 200}),  # the currency is required
        _flights(max_stops=3),
        {"type": "set_preferences", "lodging": {"hotel_class": "upscale"}},  # the old shape
        {"type": "redo_search", "search": "transfer"},
        {"type": "redo_search", "search": "lodging", "hint": "near the sea"},
    ],
)
def test_a_preference_or_search_command_outside_the_vocabulary_is_malformed(
    command: dict[str, JsonValue],
) -> None:
    with pytest.raises(ValidationError):
        COMMANDS.validate_python([command])


# ---------------------------------------------------------------------------
# The agent loop's commands: adding what a search found, and trips without flights
# ---------------------------------------------------------------------------


def _tour(title: str = "Tram tour", cost: int | None = None) -> Candidate:
    """A search result: a two-hour tour from the flat on May 3."""
    arguments: dict[str, JsonValue] = {
        "title": title,
        "start": "2027-05-03T10:00",
        "end": "2027-05-03T12:00",
        "timezone": LISBON_TZ,
        "place": FLAT,
        "category": "tour",
    }
    if cost is not None:
        arguments["cost"] = {"amount": cost, "currency": "USD"}
    change = CandidateChange(name="add_activity", arguments=arguments, summary=f"Add {title}")
    return Candidate(id="1", title=title, changes=[change])


async def _agent(
    ctx: ToolContext, candidates: Mapping[str, Candidate], *commands: dict[str, JsonValue]
) -> ChangesRecord:
    parsed = AGENT_COMMANDS.validate_python(commands)
    return await check_and_apply(ctx, await _plan(ctx), parsed, candidates)


def _add(ref: str) -> dict[str, JsonValue]:
    return {"type": "add_candidate", "candidate": ref}


@pytest.mark.parametrize(
    "command",
    [
        _stage("back"),  # there are no stages to move between
        {"type": "redo_search", "search": "lodging"},  # the loop searches itself
        {"type": "add_candidate", "candidate": "a1", "title": "Mine"},  # never an item's contents
        {"type": "set_travel_mode"},
    ],
)
def test_a_command_outside_the_agent_vocabulary_is_malformed(
    command: dict[str, JsonValue],
) -> None:
    with pytest.raises(ValidationError):
        AGENT_COMMANDS.validate_python([command])


def test_the_stage_flow_reader_cannot_add_candidates_or_change_the_travel_mode() -> None:
    for command in (_add("a1"), {"type": "set_travel_mode", "no_flights": True}):
        with pytest.raises(ValidationError):
            COMMANDS.validate_python([command])


async def test_adding_a_candidate_writes_its_changes(ctx: ToolContext) -> None:
    version = (await _plan(ctx)).version

    record = await _agent(ctx, {"a1": _tour()}, _add("a1"))

    assert record.applied == [AppliedChange(name="add_candidate", summary="Added “Tram tour”")]
    assert describe_changes(record) == "Added “Tram tour”."
    plan = await _plan(ctx)
    assert [item.title for item in plan.items] == ["Friend's flat", "Dinner in", "Tram tour"]
    assert plan.items[-1].created_by == "assistant"
    assert plan.version > version


async def test_a_candidate_that_was_not_found_is_rejected(ctx: ToolContext) -> None:
    record = await _agent(ctx, {"a1": _tour()}, _add("a7"))

    assert record == ChangesRecord(
        rejected=[
            RejectedChange(
                summary="Add option a7", reason="There's no option a7 to add; search for it first"
            )
        ]
    )


async def test_a_candidate_follows_the_plans_rules(ctx: ToolContext) -> None:
    patch = PlanUpdate(budget=Money(amount=Decimal(100), currency="USD"))
    await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, patch)

    record = await _agent(ctx, {"a1": _tour(cost=150)}, _add("a1"))

    (rejection,) = record.rejected
    assert rejection.summary == "Add “Tram tour”" and "budget" in rejection.reason
    assert await _titles(ctx) == ["Friend's flat", "Dinner in"]


async def test_candidates_are_checked_with_the_earlier_commands_staged(ctx: ToolContext) -> None:
    patch = PlanUpdate(budget=Money(amount=Decimal(100), currency="USD"))
    await plans_service.update_plan(ctx.session, ctx.user_id, ctx.plan_id, patch)
    found = {"a1": _tour("Tram tour", cost=60), "a2": _tour("Fado night", cost=60)}

    both = await _agent(ctx, found, _add("a1"), _add("a2"))
    one = await _agent(ctx, found, _add("a1"))

    # Each fits the budget alone; the second doesn't on top of the first.
    assert both.applied == []
    assert [rejection.summary for rejection in both.rejected] == ["Add “Fado night”"]
    assert [change.summary for change in one.applied] == ["Added “Tram tour”"]


async def test_the_same_candidate_is_added_once(ctx: ToolContext) -> None:
    record = await _agent(ctx, {"a1": _tour()}, _add("a1"), _add("a1"))

    assert record.rejected == [
        RejectedChange(summary="Add “Tram tour”", reason="It's already being added")
    ]


async def test_adding_a_flight_out_keeps_its_token_for_the_flights_home(
    ctx: ToolContext,
) -> None:
    stored = PlanPreferences(flights=FlightPreferences())
    await plans_service.update_preferences(ctx.session, ctx.user_id, ctx.plan_id, stored)
    plan = await _plan(ctx)
    search = SearchContext(
        session=ctx.session,
        today=TODAY,
        flights=FakeFlightProvider(),
        hotels=FakeHotelProvider(),
        places=FakePlaceProvider(),
    )
    action = RunSearch(slot=SlotName.FLIGHT_OUTBOUND, stage=PlanStage.FLIGHTS, label="Find")
    flight = (await outbound_flights(search, plan, action))[0]
    assert flight.follow_up_token is not None

    record = await _agent(ctx, {"f1": flight}, _add("f1"))

    assert [change.summary for change in record.applied] == [f"Added “{flight.title}”"]
    flights = (await _plan(ctx)).preferences.flights
    assert flights is not None and flights.outbound_token == flight.follow_up_token


async def test_a_trip_without_flights_asks_for_none(
    client: AsyncClient, headers: dict[str, str], plan_id: str, session: AsyncSession
) -> None:
    # A new plan with its basics: both flights are missing.
    response = await client.get(f"/plans/{plan_id}", headers=headers)
    plan = TravelPlan.model_validate_json(response.content)
    assert [p.id for p in plan.problems][:2] == ["missing_outbound", "missing_return"]
    new_ctx = ToolContext(session=session, user_id=plan.user_id, plan_id=plan.id, today=TODAY)

    record = await _agent(new_ctx, {}, {"type": "set_travel_mode", "no_flights": True})

    assert record.applied == [
        AppliedChange(name="set_travel_mode", summary="The trip has no flights")
    ]
    after = await _plan(new_ctx)
    assert after.flights_skipped is True
    assert not {"missing_outbound", "missing_return"} & {p.id for p in after.problems}
    # The stage flow's position is left alone.
    assert after.stage == plan.stage and after.version > plan.version


async def test_flights_can_be_wanted_again(ctx: ToolContext) -> None:
    record = await _agent(ctx, {}, {"type": "set_travel_mode", "no_flights": False})

    assert [change.summary for change in record.applied] == ["The trip has flights again"]
    after = await _plan(ctx)
    assert {"missing_outbound", "missing_return"} <= {p.id for p in after.problems}


async def test_a_travel_mode_the_trip_already_has_is_rejected(ctx: ToolContext) -> None:
    # The fixture's trip got to ground transport without flights.
    record = await _agent(ctx, {}, {"type": "set_travel_mode", "no_flights": True})

    assert record.rejected == [
        RejectedChange(
            summary="Plan the trip without flights", reason="The trip already has no flights"
        )
    ]


# ---------------------------------------------------------------------------
# Dry run
# ---------------------------------------------------------------------------


async def test_a_dry_run_resolves_the_commands_and_writes_nothing(ctx: ToolContext) -> None:
    await _add_tram_tour(ctx)
    plan = await _plan(ctx)
    fado = _tour("Fado night")
    commands = AGENT_COMMANDS.validate_python([_remove(4), _add("a1")])

    result = await dry_run(ctx, plan, commands, {"a1": fado})

    assert result.rejected == []
    assert result.changes == [
        # The rides that go with the tour are part of what is suggested.
        ProposalChange(
            summary="Remove “Tram tour” and “Taxi there”, “Taxi back”", command=commands[0]
        ),
        ProposalChange(summary="Add “Fado night”", command=commands[1], candidate=fado),
    ]
    after = await _plan(ctx)
    assert (after.version, len(after.items)) == (plan.version, len(plan.items))


async def test_a_dry_run_reports_every_broken_rule_and_no_changes(ctx: ToolContext) -> None:
    commands = AGENT_COMMANDS.validate_python([_remove(DINNER_REF), _remove(9), _add("a1")])

    result = await dry_run(ctx, await _plan(ctx), commands)

    assert result.changes == []
    assert [rejection.summary for rejection in result.rejected] == [
        "Remove item 9",
        "Add option a1",
    ]


# ---------------------------------------------------------------------------
# Searching again
# ---------------------------------------------------------------------------


async def test_searching_again_needs_those_choices_to_be_pending(ctx: ToolContext) -> None:
    record = await _run(ctx, {"type": "redo_search", "search": "lodging"})

    assert record == ChangesRecord(
        rejected=[
            RejectedChange(
                summary="Look for other places to stay",
                reason="No places to stay are being offered right now; to find a different "
                "one, remove it",
            )
        ]
    )


async def test_searching_again_follows_a_preference_change(
    app: FastAPI, client: AsyncClient, headers: dict[str, str], session: AsyncSession
) -> None:
    # At lodging with no preferences, the form is pending: no search to run again until
    # the first command answers it.
    plan_id = await create_plan(client, headers, **BASICS)
    await reach(app, client, headers, plan_id, PlanStage.LODGING)
    plan = TravelPlan.model_validate_json(
        (await client.get(f"/plans/{plan_id}", headers=headers)).content
    )
    lodging_ctx = ToolContext(session=session, user_id=plan.user_id, plan_id=plan.id, today=TODAY)
    version = plan.version

    record = await _run(
        lodging_ctx,
        _lodging(hotel_class="midrange"),
        {"type": "redo_search", "search": "lodging"},
    )

    assert record.applied == [
        AppliedChange(
            name="set_lodging_preferences", summary="Lodging preferences: Midrange hotel"
        ),
        AppliedChange(name="redo_search", summary="Looking for other places to stay"),
    ]
    # Searching again writes nothing itself; the turn runs the search.
    assert (await _plan(lodging_ctx)).version == version + 1
