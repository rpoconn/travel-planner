from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from features.chat.prompts import (
    APPROVAL_MARKER,
    FIX_REQUEST_MARKER,
    GAPS_MARKER,
    REVISE_MARKER,
    agent_system_prompt,
    approval_applied_message,
    approval_failed_message,
    fix_request_message,
    parse_gap_line,
    proposal_note,
    revise_request_message,
    strip_proposal_notes,
)
from features.chat.schemas import ProposedChange
from features.itinerary.schemas import ItineraryItem, TimelineWarning
from features.plans.schemas import PlanDetails, PlanSnapshot, PlanStage, StageStatus
from features.plans.stages import plan_progress
from shared.domain import Cost, Money
from tests.features.itinerary.factories import (
    HOME,
    PLAN_ID,
    TOKYO_TZ,
    TSUKIJI,
    activity,
    lodging,
)

CREATED = datetime(2026, 10, 1, tzinfo=UTC)


def _plan(*items: ItineraryItem, **overrides: Any) -> PlanSnapshot:
    details = PlanDetails.model_validate(
        {
            "id": PLAN_ID,
            "user_id": uuid4(),
            "title": "Tokyo in May",
            "status": "draft",
            "stage": "basics",
            "flights_skipped": False,
            "origin": None,
            "destination": None,
            "start_date": None,
            "end_date": None,
            "travelers": None,
            "budget": None,
            "limits": [],
            "notes": "",
            "version": 1,
            "created_at": CREATED,
            "updated_at": CREATED,
            **overrides,
        }
    )
    progress = plan_progress(details, items)
    return PlanSnapshot.model_validate(
        {**dict(details), "items": list(items), "stages": progress.stages, "gaps": progress.gaps}
    )


# ---------------------------------------------------------------------------
# agent_system_prompt
# ---------------------------------------------------------------------------


def test_agent_prompt_explains_that_tools_only_propose() -> None:
    prompt = agent_system_prompt(_plan(), date(2026, 10, 29))

    assert "does not change the trip" in prompt
    assert "Approve / Deny / Other" in prompt
    assert "never say you added" in prompt


def test_agent_prompt_has_a_checklist_and_never_assumes_travelers() -> None:
    prompt = agent_system_prompt(_plan(), date(2026, 10, 29))

    assert "travel dates, number of travelers (adults and children)" in prompt
    assert "Never assume one adult" in prompt
    # Unknown travelers are left out of the plan JSON rather than shown as a default.
    plan_json = prompt.split("is unknown):\n", 1)[1].split("\n\nHow changes work", 1)[0]
    assert '"Tokyo in May"' in plan_json and '"travelers"' not in plan_json
    # The worked example asks instead of calling a tool, with the right year.
    assert "Which dates in May 2027 work for you" in prompt


def test_agent_prompt_spells_out_weekdays() -> None:
    plan = _plan().model_copy(update={"start_date": date(2027, 5, 1), "end_date": date(2027, 5, 8)})

    prompt = agent_system_prompt(plan, date(2026, 10, 29))

    assert "Today is Thursday 2026-10-29" in prompt
    calendar = prompt.split("Trip calendar", 1)[1].split("\n\nHow changes work", 1)[0]
    assert "Sat 2027-05-01, Sun 2027-05-02" in calendar
    assert "Tue 2027-05-04" in calendar and "Thu 2027-05-06" in calendar
    assert calendar.endswith("Sat 2027-05-08")


def test_agent_prompt_totals_costs_against_the_budget() -> None:
    dinner = activity("2027-05-02T19:00", "2027-05-02T22:00")
    ramen = activity("2027-05-03T19:00", "2027-05-03T21:00")
    skipped = activity("2027-05-04T19:00", "2027-05-04T21:00")
    plan = _plan(
        dinner.model_copy(update={"cost": Cost(amount=Decimal("252.12"))}),
        ramen.model_copy(update={"cost": Cost(amount=Decimal(30))}),
        skipped.model_copy(update={"cost": Cost(amount=Decimal(999)), "status": "cancelled"}),
    ).model_copy(update={"budget": Money(amount=Decimal(5000))})

    prompt = agent_system_prompt(plan, date(2026, 10, 29))

    # The cancelled dinner is not counted.
    assert "Costs so far: 282.12 USD of the 5,000.00 USD budget (4,717.88 USD left)" in prompt
    assert "not per person" in prompt
    assert 'Only call a price "within"' in prompt
    assert "every item needs a cost" in prompt and 'basis "estimate"' in prompt
    assert "save it as a limit with set_plan_details" in prompt


def test_agent_prompt_has_no_cost_line_without_budget_or_costs() -> None:
    assert "Costs so far" not in agent_system_prompt(_plan(), date(2026, 10, 29))


def test_agent_prompt_skips_the_calendar_without_dates_or_for_long_trips() -> None:
    long_trip = _plan().model_copy(
        update={"start_date": date(2027, 5, 1), "end_date": date(2027, 7, 1)}
    )

    assert "Trip calendar" not in agent_system_prompt(_plan(), date(2026, 10, 29))
    assert "Trip calendar" not in agent_system_prompt(long_trip, date(2026, 10, 29))


# ---------------------------------------------------------------------------
# Proposal notes
# ---------------------------------------------------------------------------


def test_proposal_note_lists_the_changes_with_their_status() -> None:
    note = proposal_note("pending", ["Add lodging “Hotel Okura”", "Remove item “Tsukiji”"])

    assert note == (
        "[Proposed changes (pending):\n- Add lodging “Hotel Okura”\n- Remove item “Tsukiji”]"
    )


def test_strip_proposal_notes_removes_copied_notes_and_keeps_the_rest() -> None:
    note = proposal_note("approved", ["Add flight “Flight NRT to PHL” [ANA]"])

    assert strip_proposal_notes(f"I've proposed the flight.\n\n{note}") == (
        "I've proposed the flight."
    )
    assert strip_proposal_notes(f"{note}\nAnything else?") == "Anything else?"
    assert strip_proposal_notes(note) == ""
    assert strip_proposal_notes("Rooms are [usually] $300.") == "Rooms are [usually] $300."


# ---------------------------------------------------------------------------
# fix_request_message
# ---------------------------------------------------------------------------


def test_fix_request_message_lists_the_warning_and_its_items() -> None:
    senso_ji = activity("2027-05-02T09:00", "2027-05-02T11:00")
    market = activity("2027-05-02T10:00", "2027-05-02T12:00", place=TSUKIJI)
    warning = TimelineWarning(
        kind="overlap",
        message="Senso-ji overlaps Tsukiji Outer Market",
        date=date(2027, 5, 2),
        item_ids=[senso_ji.id, market.id],
    )

    message = fix_request_message(
        "Fix: Senso-ji overlaps Tsukiji Outer Market", warning, _plan(senso_ji, market)
    )

    assert message.startswith("Fix: Senso-ji overlaps Tsukiji Outer Market\n\n")
    assert FIX_REQUEST_MARKER in message
    assert "kind: overlap" in message
    assert "message: Senso-ji overlaps Tsukiji Outer Market" in message
    assert "date: 2027-05-02" in message
    assert f"- {senso_ji.id}: Senso-ji (activity, 2027-05-02 09:00)" in message
    assert f"- {market.id}: Tsukiji Outer Market (activity, 2027-05-02 10:00)" in message
    assert "propose it with the tools" in message


def test_fix_request_message_reports_items_no_longer_in_the_plan() -> None:
    gone = uuid4()
    warning = TimelineWarning(kind="outside_trip", message="X is outside", item_ids=[gone])

    message = fix_request_message("Fix: X is outside", warning, _plan())

    assert f"- {gone}: no longer in the plan" in message
    assert "date:" not in message


# ---------------------------------------------------------------------------
# Proposal decisions
# ---------------------------------------------------------------------------

CHANGE = ProposedChange(
    call_id="c1",
    name="add_lodging",
    arguments={},
    summary="Add lodging “Hotel Okura” on 2027-05-02 15:00",
)


def test_approval_applied_message_lists_the_tool_results() -> None:
    message = approval_applied_message(['Added lodging "Hotel Okura" with id 1.'])

    assert message.startswith(APPROVAL_MARKER)
    assert '- Added lodging "Hotel Okura" with id 1.' in message
    assert "suggest the next step" in message


def test_approval_failed_message_says_what_was_applied() -> None:
    nothing = approval_failed_message([], ["Remove “X”: No item with that id"])
    partly = approval_failed_message(["Removed X."], ["Change “X”: No item with that id"])

    assert nothing.startswith(APPROVAL_MARKER) and "Nothing was applied." in nothing
    assert "- Remove “X”: No item with that id" in nothing
    assert "applied before the failure:\n- Removed X." in partly


def test_revise_request_message_frames_the_users_text() -> None:
    message = revise_request_message("A ryokan instead", [CHANGE])

    assert message.startswith(f"{REVISE_MARKER}\nA ryokan instead\n")
    assert f"- {CHANGE.summary}" in message


# ---------------------------------------------------------------------------
# Planning stage block
# ---------------------------------------------------------------------------

TODAY = date(2026, 10, 29)


def test_agent_prompt_names_the_stage_and_what_is_missing() -> None:
    prompt = agent_system_prompt(_plan(), TODAY)

    assert "Current stage: basics (1 of 6)" in prompt
    assert "Still missing: trip dates; who is travelling;" in prompt
    assert "suggest pressing Next (next: flights)" in prompt
    assert "Ask where the trip starts" in prompt
    # The workflow is described in words, not dumped as JSON.
    assert '"stages"' not in prompt
    assert '"stage"' not in prompt


def test_agent_prompt_says_the_origin_comes_from_home() -> None:
    prompt = agent_system_prompt(_plan(origin=HOME), TODAY)

    assert "The origin is prefilled from the user's home" in prompt
    assert "Never replace it with just an airport code" in prompt


def test_agent_prompt_reports_a_complete_checklist() -> None:
    plan = _plan(
        origin=HOME,
        destination={"name": "Tokyo"},
        start_date=date(2027, 5, 1),
        end_date=date(2027, 5, 4),
        travelers={"adults": 2},
    )

    assert "Checklist: complete." in agent_system_prompt(plan, TODAY)


def test_agent_prompt_mentions_skipped_flights_after_the_flights_stage() -> None:
    plan = _plan(stage="lodging", flights_skipped=True)

    prompt = agent_system_prompt(plan, TODAY)

    assert "The user skipped flights" in prompt
    assert "Current stage: lodging (3 of 6)" in prompt


def test_agent_prompt_says_where_out_of_stage_requests_belong() -> None:
    prompt = agent_system_prompt(_plan(stage="lodging"), TODAY)

    # Small models otherwise force a request into this stage's tool (a dinner as lodging).
    assert "never use this stage's tools for it" in prompt
    assert "meals and restaurants to activities" in prompt


def test_agent_prompt_tells_the_activities_stage_how_to_schedule_an_activity() -> None:
    prompt = agent_system_prompt(_plan(stage="activities"), TODAY)

    # qwen3:8b described three activities as "added" without calling add_activity once.
    assert "call add_activity once per activity" in prompt
    assert "describing them in text adds nothing" in prompt
    assert "start and end on a trip day" in prompt
    assert "call add_activity once per activity" not in agent_system_prompt(
        _plan(stage="lodging"), TODAY
    )


def test_agent_prompt_asks_for_lodging_preferences_before_searching() -> None:
    prompt = agent_system_prompt(_plan(stage="lodging"), TODAY)

    # The stage's button says "Add a hotel"; the model must still ask what kind first.
    assert "Before you search for or propose any lodging" in prompt
    assert "star rating" in prompt
    assert "budget for lodging" in prompt
    assert "neighborhood or area" in prompt
    assert "Before you search for or propose any lodging" not in agent_system_prompt(
        _plan(stage="flights"), TODAY
    )


def test_agent_prompt_lists_each_gap_in_the_ground_stage() -> None:
    stay = lodging("2027-05-02T15:00", "2027-05-06T11:00")
    plan = _plan(stay, stage="ground", origin=HOME)

    prompt = agent_system_prompt(plan, TODAY)

    assert GAPS_MARKER in prompt
    gaps = [parse_gap_line(line) for line in prompt.splitlines()]
    assert [gap for gap in gaps if gap is not None] == [
        {
            "from_name": "Home (San Francisco)",
            "to_name": "Hotel Okura",
            "leave": None,
            "arrive": "2027-05-02T15:00",
            "timezone": TOKYO_TZ,
        },
        {
            "from_name": "Hotel Okura",
            "to_name": "Home (San Francisco)",
            "leave": "2027-05-06T11:00",
            "arrive": None,
            "timezone": TOKYO_TZ,
        },
    ]
    assert "propose every transfer on one card with add_transfer" in prompt


def test_review_has_no_next_stage() -> None:
    prompt = agent_system_prompt(_plan(stage="review"), TODAY)

    assert "Current stage: review (6 of 6)" in prompt
    assert "suggest pressing Next" not in prompt
    assert "there is no Next button" in prompt


def test_approval_message_points_at_next_when_a_stage_completes() -> None:
    done = StageStatus(stage=PlanStage.LODGING, issues=[])

    message = approval_applied_message(["Added lodging"], done)

    assert message.endswith(
        "The lodging stage is now complete; tell the user they can press Next to move on to "
        "activities."
    )
    assert "stage is now complete" not in approval_applied_message(["Added lodging"])


def test_agent_prompt_says_a_pick_from_an_earlier_list_is_that_option() -> None:
    # "add #1" once got a fresh search and a different hotel than the one listed first.
    prompt = agent_system_prompt(_plan(stage="lodging"), TODAY)

    assert "propose exactly that option, by its name" in prompt
    assert "Never swap in a different place a new search turned up" in prompt
