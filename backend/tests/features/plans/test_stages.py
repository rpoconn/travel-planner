"""Stage readiness rules and the transitions between stages (pure, no database)."""

from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from uuid import uuid4

import pytest

from features.itinerary.schemas import ItineraryItem
from features.plans.schemas import PlanDetails, PlanStage, StageChange, TravelPlan
from features.plans.stages import StageError, apply_stage_change, plan_progress
from shared.domain import Place
from tests.features.itinerary.factories import (
    HOME,
    LA_TZ,
    NRT,
    OKURA,
    PLAN_ID,
    SFO,
    TOKYO_TZ,
    activity,
    flight,
    lodging,
    transfer,
)

CREATED = datetime(2026, 10, 1, tzinfo=UTC)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _details(**overrides: Any) -> PlanDetails:
    """A plan whose basics are complete: May 1-4 2027 in Tokyo, from HOME, two adults."""
    fields: dict[str, Any] = {
        "id": PLAN_ID,
        "user_id": uuid4(),
        "title": "Tokyo",
        "status": "draft",
        "stage": "basics",
        "flights_skipped": False,
        "origin": HOME,
        "destination": Place(name="Tokyo"),
        "start_date": date(2027, 5, 1),
        "end_date": date(2027, 5, 4),
        "travelers": {"adults": 2},
        "budget": None,
        "limits": [],
        "notes": "",
        "version": 1,
        "created_at": CREATED,
        "updated_at": CREATED,
    }
    return PlanDetails.model_validate({**fields, **overrides})


def _plan(items: Sequence[ItineraryItem] = (), **overrides: Any) -> TravelPlan:
    details = _details(**overrides)
    progress = plan_progress(details, items)
    return TravelPlan.model_validate(
        {**dict(details), "items": list(items), "stages": progress.stages, "gaps": progress.gaps}
    )


def _missing(plan: TravelPlan, stage: PlanStage) -> list[str]:
    return next(status.missing for status in plan.stages if status.stage == stage)


def _complete_trip() -> list[ItineraryItem]:
    """Every stage done: flights both ways, three nights, a visit, and every transfer."""
    return [
        transfer(
            "2027-05-01T07:00", "2027-05-01T08:00", from_place=HOME, to_place=SFO, timezone=LA_TZ
        ),
        flight("2027-05-01T11:00", "2027-05-02T14:30"),
        transfer("2027-05-02T15:00", "2027-05-02T16:30", from_place=NRT, to_place=OKURA),
        # Arrives May 2 local, so the night of May 1 is spent on the plane.
        lodging("2027-05-02T17:00", "2027-05-04T09:00"),
        activity("2027-05-02T18:00", "2027-05-02T19:00", place=OKURA, title="Hotel dinner"),
        transfer("2027-05-04T09:00", "2027-05-04T10:30", from_place=OKURA, to_place=NRT),
        flight(
            "2027-05-04T17:00",
            "2027-05-04T10:00",
            from_place=NRT,
            to_place=SFO,
            timezone=TOKYO_TZ,
            end_timezone=LA_TZ,
        ).model_copy(update={"direction": "return"}),
        transfer(
            "2027-05-04T11:00", "2027-05-04T12:00", from_place=SFO, to_place=HOME, timezone=LA_TZ
        ),
    ]


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def test_a_new_plan_lists_every_basic_it_lacks() -> None:
    plan = _plan(origin=None, destination=None, start_date=None, end_date=None, travelers=None)

    assert _missing(plan, "basics") == [
        "trip dates",
        "who is travelling",
        "where the trip starts (home)",
        "a destination",
    ]


def test_basics_are_ready_with_dates_travelers_origin_and_destination() -> None:
    assert _missing(_plan(), "basics") == []


def test_flights_need_an_outbound_and_a_return_flight() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T14:30")

    assert _missing(_plan(), "flights") == ["a flight out", "a flight home"]
    assert _missing(_plan([out]), "flights") == ["a flight home"]


def test_cancelled_flights_dont_count() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T14:30", status="cancelled")

    assert _missing(_plan([out]), "flights") == ["a flight out", "a flight home"]


def test_skipped_flights_count_as_done() -> None:
    assert _missing(_plan(flights_skipped=True), "flights") == []


def test_lodging_lists_each_uncovered_night() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-02T11:00")

    assert _missing(_plan([stay]), "lodging") == [
        "a place to stay the night of May 2",
        "a place to stay the night of May 3",
    ]


def test_lodging_needs_trip_dates() -> None:
    assert _missing(_plan(start_date=None), "lodging") == ["trip dates"]


def test_activities_need_at_least_one() -> None:
    assert _missing(_plan(), "activities") == ["at least one activity"]
    assert _missing(_plan([activity("2027-05-02T10:00", "2027-05-02T11:00")]), "activities") == []


def test_ground_lists_each_gap_with_home_by_name() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-04T11:00")

    plan = _plan([stay])

    assert _missing(plan, "ground") == [
        "a transfer from Home (San Francisco) to Hotel Okura",
        "a transfer from Hotel Okura to Home (San Francisco)",
    ]
    assert len(plan.gaps) == 2


def test_review_waits_for_every_earlier_stage() -> None:
    assert _missing(_plan(), "review") == [
        "the flights stage",
        "the lodging stage",
        "the activities stage",
    ]


def test_a_complete_trip_is_ready_at_every_stage() -> None:
    plan = _plan(_complete_trip())

    assert [status.missing for status in plan.stages] == [[]] * 6
    assert plan.gaps == []


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def test_next_moves_on_when_the_stage_is_ready() -> None:
    assert apply_stage_change(_plan(), StageChange(action="next")) == ("flights", False)


def test_next_is_refused_with_what_is_missing() -> None:
    plan = _plan(stage="flights")

    with pytest.raises(StageError, match="Finish the flights stage first: a flight out"):
        apply_stage_change(plan, StageChange(action="next"))


def test_next_from_review_is_refused() -> None:
    with pytest.raises(StageError):
        apply_stage_change(_plan(_complete_trip(), stage="review"), StageChange(action="next"))


def test_back_goes_to_the_previous_stage_even_when_unfinished() -> None:
    plan = _plan(stage="lodging", flights_skipped=True)

    assert apply_stage_change(plan, StageChange(action="back")) == ("flights", True)


def test_back_from_basics_is_refused() -> None:
    with pytest.raises(StageError):
        apply_stage_change(_plan(), StageChange(action="back"))


def test_skip_flights_goes_to_lodging() -> None:
    plan = _plan(stage="flights")

    assert apply_stage_change(plan, StageChange(action="skip_flights")) == ("lodging", True)


def test_skip_flights_only_from_the_flights_stage() -> None:
    with pytest.raises(StageError):
        apply_stage_change(_plan(), StageChange(action="skip_flights"))


def test_next_with_flights_clears_an_earlier_skip() -> None:
    plan = _plan(_complete_trip(), stage="flights", flights_skipped=True)

    assert apply_stage_change(plan, StageChange(action="next")) == ("lodging", False)


def test_next_without_flights_keeps_the_skip() -> None:
    plan = _plan(stage="flights", flights_skipped=True)

    assert apply_stage_change(plan, StageChange(action="next")) == ("lodging", True)


def test_go_to_jumps_back() -> None:
    plan = _plan(stage="ground")

    assert apply_stage_change(plan, StageChange(action="go_to", stage="basics")) == (
        "basics",
        False,
    )


def test_go_to_cannot_jump_ahead() -> None:
    with pytest.raises(StageError):
        apply_stage_change(_plan(), StageChange(action="go_to", stage="review"))


def test_go_to_needs_a_stage_and_other_actions_take_none() -> None:
    with pytest.raises(ValueError, match="go_to needs a stage"):
        StageChange(action="go_to")
    with pytest.raises(ValueError, match="next takes no stage"):
        StageChange(action="next", stage="flights")
