"""The validation engine: the problems each plan state has (pure, no database)."""

from datetime import date
from uuid import uuid4

from features.itinerary.schemas import TimelineWarning
from features.planning.schemas import NightRange, Problem
from features.planning.validation import is_complete, night_ranges, range_label, validate
from features.plans.schemas import PlanStage, SlotName
from shared.domain import IssueCode
from tests.features.itinerary.factories import HOME, OKURA, SFO, lodging
from tests.features.plans.builders import complete_trip, outbound_flight, return_flight, snapshot


def _ids(problems: list[Problem]) -> list[str]:
    return [problem.id for problem in problems]


# ---------------------------------------------------------------------------
# Blocking problems
# ---------------------------------------------------------------------------


def test_an_empty_plan_lists_every_problem_whatever_its_stage() -> None:
    """Nothing is held back until a stage is reached: the plan is at basics, and the
    flights, the activities and (once, though two checklists report them) the dates are
    all there, in the order their fixes are asked."""
    plan = snapshot(
        stage=PlanStage.BASICS, destination=None, start_date=None, end_date=None, travelers=None
    )

    problems = validate(plan, [])

    assert _ids(problems) == [
        "missing_destination",
        "missing_dates",
        "missing_travelers",
        "missing_outbound",
        "missing_return",
        "no_activities",
    ]
    assert {problem.severity for problem in problems} == {"blocking"}
    assert not is_complete(problems)


def test_each_problem_says_how_it_is_usually_fixed() -> None:
    plan = snapshot(origin=None)

    fixes = {problem.code: problem.fix for problem in validate(plan, [])}

    assert (fixes[IssueCode.MISSING_ORIGIN].how, fixes[IssueCode.MISSING_ORIGIN].slot) == (
        "ask",
        SlotName.ORIGIN,
    )
    assert (fixes[IssueCode.MISSING_RETURN].how, fixes[IssueCode.MISSING_RETURN].slot) == (
        "search",
        SlotName.FLIGHT_RETURN,
    )
    assert fixes[IssueCode.UNCOVERED_NIGHT].slot == SlotName.LODGING_STAY


def test_uncovered_nights_are_one_problem_per_run() -> None:
    """May 1-4 with a stay on the night of May 2 only: a night before it and one after."""
    plan = snapshot([lodging("2027-05-02T15:00", "2027-05-03T11:00")])

    nights = [p for p in validate(plan, []) if p.code == IssueCode.UNCOVERED_NIGHT]

    assert [(p.id, p.message, p.nights) for p in nights] == [
        (
            "uncovered_night:2027-05-01",
            "a place to stay the night of May 1",
            NightRange(check_in=date(2027, 5, 1), check_out=date(2027, 5, 2)),
        ),
        (
            "uncovered_night:2027-05-03",
            "a place to stay the night of May 3",
            NightRange(check_in=date(2027, 5, 3), check_out=date(2027, 5, 4)),
        ),
    ]


def test_skipped_flights_are_not_a_problem() -> None:
    plan = snapshot(flights_skipped=True)

    codes = {problem.code for problem in validate(plan, [])}

    assert IssueCode.MISSING_OUTBOUND not in codes
    assert IssueCode.MISSING_RETURN not in codes


def test_each_missing_transfer_is_a_problem_named_by_its_ends() -> None:
    out, back = outbound_flight(), return_flight()
    plan = snapshot([out, back])

    transfers = [p for p in validate(plan, []) if p.code == IssueCode.MISSING_TRANSFER]

    # The flight out lands where the flight home leaves, so only the rides from and to
    # home are missing.
    assert _ids(transfers) == [
        f"missing_transfer:home:{out.id}",
        f"missing_transfer:{back.id}:home",
    ]
    first = transfers[0]
    assert first.gap is not None
    assert (first.gap.from_place, first.gap.to_place) == (HOME, SFO)
    assert first.fix.slot == SlotName.TRANSFER


def test_a_complete_trip_has_no_problems() -> None:
    problems = validate(snapshot(complete_trip()), [])

    assert problems == []
    assert is_complete(problems)


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------


def test_timeline_warnings_follow_the_blocking_problems_and_do_not_block() -> None:
    stay = lodging("2027-05-02T17:00", "2027-05-04T09:00", place=OKURA)
    warnings = [
        TimelineWarning(kind=IssueCode.MISSING_COST, message="No cost yet", item_ids=[stay.id]),
        TimelineWarning(kind=IssueCode.OVER_BUDGET, message="Over the budget by $200"),
    ]

    problems = validate(snapshot(complete_trip()), warnings)

    assert [(p.id, p.severity, p.fix.how) for p in problems] == [
        (f"missing_cost:{stay.id}", "warning", "edit"),
        ("over_budget", "warning", "edit"),
    ]
    assert is_complete(problems)


def test_warnings_the_checklists_already_report_are_left_out() -> None:
    warnings = [
        TimelineWarning(
            kind=IssueCode.UNCOVERED_NIGHT, message="No lodging", date=date(2027, 5, 1)
        ),
        TimelineWarning(kind=IssueCode.MISSING_TRANSFER, message="No ride", item_ids=[uuid4()]),
    ]

    assert validate(snapshot(complete_trip()), warnings) == []


def test_different_warnings_that_would_share_an_id_are_numbered() -> None:
    warnings = [
        TimelineWarning(kind=IssueCode.OVER_LIMIT, message="Food is over its limit"),
        TimelineWarning(kind=IssueCode.OVER_LIMIT, message="Lodging is over its limit"),
        TimelineWarning(kind=IssueCode.OVER_LIMIT, message="Food is over its limit"),
    ]

    problems = validate(snapshot(complete_trip()), warnings)

    assert _ids(problems) == ["over_limit", "over_limit#2"]


# ---------------------------------------------------------------------------
# Night runs
# ---------------------------------------------------------------------------


def test_night_ranges_join_consecutive_nights() -> None:
    nights = [date(2027, 5, 3), date(2027, 5, 1), date(2027, 5, 2), date(2027, 5, 5)]

    assert night_ranges(nights) == [
        NightRange(check_in=date(2027, 5, 1), check_out=date(2027, 5, 4)),
        NightRange(check_in=date(2027, 5, 5), check_out=date(2027, 5, 6)),
    ]
    assert night_ranges([]) == []


def test_range_label_names_one_night_or_a_span() -> None:
    one = NightRange(check_in=date(2027, 5, 1), check_out=date(2027, 5, 2))
    span = NightRange(check_in=date(2027, 5, 1), check_out=date(2027, 5, 4))

    assert range_label(one) == "the night of May 1"
    assert range_label(span) == "May 1 to May 4"
