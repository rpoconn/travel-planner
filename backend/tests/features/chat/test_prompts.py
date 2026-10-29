from datetime import UTC, date, datetime
from uuid import uuid4

from features.chat.prompts import (
    APPROVAL_MARKER,
    FIX_REQUEST_MARKER,
    REVISE_MARKER,
    agent_system_prompt,
    approval_applied_message,
    approval_failed_message,
    fix_request_message,
    revise_request_message,
)
from features.chat.schemas import ProposedChange
from features.itinerary.schemas import ActivityItem, TimelineWarning
from features.plans.schemas import TravelPlan
from tests.features.itinerary.factories import PLAN_ID, TSUKIJI, activity

CREATED = datetime(2026, 10, 1, tzinfo=UTC)


def _plan(*items: ActivityItem) -> TravelPlan:
    return TravelPlan.model_validate(
        {
            "id": PLAN_ID,
            "user_id": uuid4(),
            "title": "Tokyo in May",
            "status": "draft",
            "origin": None,
            "destination": None,
            "start_date": None,
            "end_date": None,
            "travelers": None,
            "budget": None,
            "notes": "",
            "version": 1,
            "created_at": CREATED,
            "updated_at": CREATED,
            "items": list(items),
        }
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


def test_agent_prompt_skips_the_calendar_without_dates_or_for_long_trips() -> None:
    long_trip = _plan().model_copy(
        update={"start_date": date(2027, 5, 1), "end_date": date(2027, 7, 1)}
    )

    assert "Trip calendar" not in agent_system_prompt(_plan(), date(2026, 10, 29))
    assert "Trip calendar" not in agent_system_prompt(long_trip, date(2026, 10, 29))


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
