"""Planning stages: what each stage needs before the plan can move on, and the rules for
moving between them.

Pure functions (no database). This lives apart from `plans.service` because readiness
depends on itinerary items, and `itinerary.service` already imports `plans.service`.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from features.itinerary.days import date_range, uncovered_nights
from features.itinerary.gaps import describe_place, find_gaps
from features.itinerary.schemas import FlightItem, Gap, ItineraryItem
from features.plans.schemas import (
    STAGES,
    Issue,
    IssueCode,
    PlanDetails,
    PlanSnapshot,
    PlanStage,
    StageChange,
    StageStatus,
)

# How each stage is named in messages to the user.
STAGE_LABELS: dict[PlanStage, str] = {
    PlanStage.BASICS: "basics",
    PlanStage.FLIGHTS: "flights",
    PlanStage.LODGING: "lodging",
    PlanStage.ACTIVITIES: "activities",
    PlanStage.GROUND: "ground transport",
    PlanStage.REVIEW: "review",
}


class StageError(Exception):
    """A stage change the workflow doesn't allow; the message is shown to the user."""


@dataclass(frozen=True)
class Progress:
    stages: list[StageStatus]  # one per stage, in `STAGES` order
    gaps: list[Gap]


def _blocking(
    code: IssueCode,
    stage: PlanStage,
    message: str,
    *,
    on: date | None = None,
    gap: Gap | None = None,
) -> Issue:
    # A gap's neighbours; None is home, which isn't an item.
    ends = () if gap is None else (gap.after_item_id, gap.before_item_id)
    item_ids = [item_id for item_id in ends if item_id is not None]
    return Issue(
        code=code,
        stage=stage,
        severity="blocking",
        message=message,
        date=on,
        item_ids=item_ids,
        gap=gap,
    )


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def _basics_issues(details: PlanDetails) -> list[Issue]:
    basics = PlanStage.BASICS
    issues: list[Issue] = []
    if details.start_date is None or details.end_date is None:
        issues.append(_blocking(IssueCode.MISSING_DATES, basics, "trip dates"))
    if details.travelers is None:
        issues.append(_blocking(IssueCode.MISSING_TRAVELERS, basics, "who is travelling"))
    if details.origin is None:
        issues.append(_blocking(IssueCode.MISSING_ORIGIN, basics, "where the trip starts (home)"))
    if details.destination is None:
        issues.append(_blocking(IssueCode.MISSING_DESTINATION, basics, "a destination"))
    return issues


def _flights_issues(details: PlanDetails, active: Sequence[ItineraryItem]) -> list[Issue]:
    if details.flights_skipped:
        return []
    directions = {item.direction for item in active if isinstance(item, FlightItem)}
    issues: list[Issue] = []
    if "outbound" not in directions:
        issues.append(_blocking(IssueCode.MISSING_OUTBOUND, PlanStage.FLIGHTS, "a flight out"))
    if "return" not in directions:
        issues.append(_blocking(IssueCode.MISSING_RETURN, PlanStage.FLIGHTS, "a flight home"))
    return issues


def _night_label(night: date) -> str:
    return f"{night:%b} {night.day}"


def _lodging_issues(details: PlanDetails, active: Sequence[ItineraryItem]) -> list[Issue]:
    if details.start_date is None or details.end_date is None:
        return [_blocking(IssueCode.MISSING_DATES, PlanStage.LODGING, "trip dates")]
    days = list(date_range(details.start_date, details.end_date))
    return [
        _blocking(
            IssueCode.UNCOVERED_NIGHT,
            PlanStage.LODGING,
            f"a place to stay the night of {_night_label(night)}",
            on=night,
        )
        for night in uncovered_nights(days, active)
    ]


def _activities_issues(active: Sequence[ItineraryItem]) -> list[Issue]:
    if any(item.type == "activity" for item in active):
        return []
    return [_blocking(IssueCode.NO_ACTIVITIES, PlanStage.ACTIVITIES, "at least one activity")]


def _ground_issues(gaps: Sequence[Gap]) -> list[Issue]:
    issues: list[Issue] = []
    for gap in gaps:
        moment = gap.arrive_before or gap.leave_after
        issues.append(
            _blocking(
                IssueCode.MISSING_TRANSFER,
                PlanStage.GROUND,
                f"a transfer from {describe_place(gap.from_place, gap.after_item_id)} "
                f"to {describe_place(gap.to_place, gap.before_item_id)}",
                on=moment.date() if moment is not None else None,
                gap=gap,
            )
        )
    return issues


def plan_progress(details: PlanDetails, items: Sequence[ItineraryItem]) -> Progress:
    """Each stage's checklist for the plan as it stands. Cancelled items don't count."""
    active = [item for item in items if item.status != "cancelled"]
    gaps = find_gaps(active, details.origin)
    issues: dict[PlanStage, list[Issue]] = {
        PlanStage.BASICS: _basics_issues(details),
        PlanStage.FLIGHTS: _flights_issues(details, active),
        PlanStage.LODGING: _lodging_issues(details, active),
        PlanStage.ACTIVITIES: _activities_issues(active),
        PlanStage.GROUND: _ground_issues(gaps),
    }
    # Review is the finish line: ready once every earlier stage is.
    issues[PlanStage.REVIEW] = [
        _blocking(IssueCode.STAGE_INCOMPLETE, PlanStage.REVIEW, f"the {STAGE_LABELS[stage]} stage")
        for stage in STAGES[:-1]
        if issues[stage]
    ]
    stages = [StageStatus(stage=stage, issues=issues[stage]) for stage in STAGES]
    return Progress(stages=stages, gaps=gaps)


def stage_status(plan: PlanSnapshot, stage: PlanStage) -> StageStatus:
    return next(status for status in plan.stages if status.stage == stage)


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _has_round_trip(plan: PlanSnapshot) -> bool:
    directions = {
        item.direction
        for item in plan.items
        if isinstance(item, FlightItem) and item.status != "cancelled"
    }
    return {"outbound", "return"} <= directions


def apply_stage_change(plan: PlanSnapshot, change: StageChange) -> tuple[PlanStage, bool]:
    """The plan's new `(stage, flights_skipped)` after `change`, or `StageError`."""
    index = STAGES.index(plan.stage)
    match change.action:
        case "next":
            if plan.stage == PlanStage.REVIEW:
                raise StageError("This is the last stage.")
            status = stage_status(plan, plan.stage)
            if not status.ready:
                raise StageError(
                    f"Finish the {STAGE_LABELS[plan.stage]} stage first: "
                    f"{', '.join(status.missing)}."
                )
            skipped = plan.flights_skipped
            if plan.stage == PlanStage.FLIGHTS and _has_round_trip(plan):
                # The user went back and added flights after skipping them.
                skipped = False
            return STAGES[index + 1], skipped
        case "back":
            if index == 0:
                raise StageError("This is the first stage.")
            return STAGES[index - 1], plan.flights_skipped
        case "skip_flights":
            if plan.stage != PlanStage.FLIGHTS:
                raise StageError("Flights can only be skipped from the flights stage.")
            return PlanStage.LODGING, True
        case "go_to":
            # The validator guarantees a stage for go_to.
            assert change.stage is not None
            if STAGES.index(change.stage) >= index:
                raise StageError("You can only go back to an earlier stage; use Next to go on.")
            return change.stage, plan.flights_skipped
