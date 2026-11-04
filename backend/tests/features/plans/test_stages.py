"""Stage readiness rules and the transitions between stages (pure, no database)."""

from datetime import date

import pytest

from features.plans.schemas import PlanSnapshot, PlanStage, StageChange
from features.plans.stages import StageError, apply_stage_change
from shared.domain import IssueCode
from tests.features.itinerary.factories import activity, flight, lodging
from tests.features.plans.builders import complete_trip, snapshot

# Short names: these tests build many plans.
_plan = snapshot
_complete_trip = complete_trip


def _missing(plan: PlanSnapshot, stage: PlanStage) -> list[str]:
    return next(status.missing for status in plan.stages if status.stage == stage)


def _codes(plan: PlanSnapshot, stage: PlanStage) -> list[IssueCode]:
    return next([i.code for i in status.issues] for status in plan.stages if status.stage == stage)


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def test_a_new_plan_lists_every_basic_it_lacks() -> None:
    plan = _plan(origin=None, destination=None, start_date=None, end_date=None, travelers=None)

    assert _missing(plan, PlanStage.BASICS) == [
        "trip dates",
        "who is travelling",
        "where the trip starts (home)",
        "a destination",
    ]


def test_basics_are_ready_with_dates_travelers_origin_and_destination() -> None:
    assert _missing(_plan(), PlanStage.BASICS) == []


def test_flights_need_an_outbound_and_a_return_flight() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T14:30")

    assert _missing(_plan(), PlanStage.FLIGHTS) == ["a flight out", "a flight home"]
    assert _missing(_plan([out]), PlanStage.FLIGHTS) == ["a flight home"]


def test_cancelled_flights_dont_count() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T14:30", status="cancelled")

    assert _missing(_plan([out]), PlanStage.FLIGHTS) == ["a flight out", "a flight home"]


def test_skipped_flights_count_as_done() -> None:
    assert _missing(_plan(flights_skipped=True), PlanStage.FLIGHTS) == []


def test_lodging_lists_each_uncovered_night() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-02T11:00")

    assert _missing(_plan([stay]), PlanStage.LODGING) == [
        "a place to stay the night of May 2",
        "a place to stay the night of May 3",
    ]


def test_lodging_needs_trip_dates() -> None:
    assert _missing(_plan(start_date=None), PlanStage.LODGING) == ["trip dates"]


def test_activities_need_at_least_one() -> None:
    assert _missing(_plan(), PlanStage.ACTIVITIES) == ["at least one activity"]
    assert (
        _missing(_plan([activity("2027-05-02T10:00", "2027-05-02T11:00")]), PlanStage.ACTIVITIES)
        == []
    )


def test_ground_lists_each_gap_with_home_by_name() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-04T11:00")

    plan = _plan([stay])

    assert _missing(plan, PlanStage.GROUND) == [
        "a transfer from Home (San Francisco) to Hotel Okura",
        "a transfer from Hotel Okura to Home (San Francisco)",
    ]
    assert len(plan.gaps) == 2


def test_review_waits_for_every_earlier_stage() -> None:
    assert _missing(_plan(), PlanStage.REVIEW) == [
        "the flights stage",
        "the lodging stage",
        "the activities stage",
    ]


def test_a_complete_trip_is_ready_at_every_stage() -> None:
    plan = _plan(_complete_trip())

    assert [status.missing for status in plan.stages] == [[]] * 6
    assert plan.gaps == []


# ---------------------------------------------------------------------------
# Typed issues
# ---------------------------------------------------------------------------


def test_basics_issues_carry_one_code_per_missing_field() -> None:
    plan = _plan(origin=None, destination=None, start_date=None, end_date=None, travelers=None)

    assert _codes(plan, PlanStage.BASICS) == [
        IssueCode.MISSING_DATES,
        IssueCode.MISSING_TRAVELERS,
        IssueCode.MISSING_ORIGIN,
        IssueCode.MISSING_DESTINATION,
    ]


def test_every_checklist_issue_is_blocking_and_names_its_stage() -> None:
    plan = _plan(destination=None)

    for status in plan.stages:
        assert all(issue.severity == "blocking" for issue in status.issues)
        assert all(issue.stage == status.stage for issue in status.issues)


def test_ready_and_missing_are_derived_from_issues() -> None:
    status = next(s for s in _plan().stages if s.stage == PlanStage.FLIGHTS)

    assert status.ready is False
    assert status.missing == [issue.message for issue in status.issues]
    dumped = status.model_dump()
    assert dumped["ready"] is False
    assert dumped["missing"] == ["a flight out", "a flight home"]


def test_flight_issues_say_which_direction() -> None:
    out = flight("2027-05-01T11:00", "2027-05-02T14:30")

    assert _codes(_plan([out]), PlanStage.FLIGHTS) == [IssueCode.MISSING_RETURN]


def test_uncovered_nights_are_dated() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-02T11:00")

    issues = next(s.issues for s in _plan([stay]).stages if s.stage == PlanStage.LODGING)

    assert [(i.code, i.date) for i in issues] == [
        (IssueCode.UNCOVERED_NIGHT, date(2027, 5, 2)),
        (IssueCode.UNCOVERED_NIGHT, date(2027, 5, 3)),
    ]


def test_missing_transfers_carry_their_gap_and_items() -> None:
    stay = lodging("2027-05-01T15:00", "2027-05-04T11:00")

    issues = next(s.issues for s in _plan([stay]).stages if s.stage == PlanStage.GROUND)

    assert [i.code for i in issues] == [IssueCode.MISSING_TRANSFER] * 2
    assert all(i.gap is not None for i in issues)
    # Home isn't an item, so each gap names only the hotel.
    assert [i.item_ids for i in issues] == [[stay.id], [stay.id]]


def test_review_issues_name_each_unfinished_stage() -> None:
    assert _codes(_plan(), PlanStage.REVIEW) == [IssueCode.STAGE_INCOMPLETE] * 3


def test_activities_issue_clears_with_one_activity() -> None:
    visit = activity("2027-05-02T10:00", "2027-05-02T11:00")

    assert _codes(_plan(), PlanStage.ACTIVITIES) == [IssueCode.NO_ACTIVITIES]
    assert _codes(_plan([visit]), PlanStage.ACTIVITIES) == []


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

    assert apply_stage_change(plan, StageChange(action="go_to", stage=PlanStage.BASICS)) == (
        "basics",
        False,
    )


def test_go_to_cannot_jump_ahead() -> None:
    with pytest.raises(StageError):
        apply_stage_change(_plan(), StageChange(action="go_to", stage=PlanStage.REVIEW))


def test_go_to_needs_a_stage_and_other_actions_take_none() -> None:
    with pytest.raises(ValueError, match="go_to needs a stage"):
        StageChange(action="go_to")
    with pytest.raises(ValueError, match="next takes no stage"):
        StageChange(action="next", stage=PlanStage.FLIGHTS)
