"""Planning stages: what each stage needs before the user can move on, and the rules for
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
    PlanDetails,
    PlanStage,
    StageChange,
    StageStatus,
    TravelPlan,
)

# How each stage is named in messages to the user and the agent.
STAGE_LABELS: dict[PlanStage, str] = {
    "basics": "basics",
    "flights": "flights",
    "lodging": "lodging",
    "activities": "activities",
    "ground": "ground transport",
    "review": "review",
}


class StageError(Exception):
    """A stage change the workflow doesn't allow; the message is shown to the user."""


@dataclass(frozen=True)
class Progress:
    stages: list[StageStatus]  # one per stage, in `STAGES` order
    gaps: list[Gap]


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def _basics_missing(details: PlanDetails) -> list[str]:
    missing: list[str] = []
    if details.start_date is None or details.end_date is None:
        missing.append("trip dates")
    if details.travelers is None:
        missing.append("who is travelling")
    if details.origin is None:
        missing.append("where the trip starts (home)")
    if details.destination is None:
        missing.append("a destination")
    return missing


def _flights_missing(details: PlanDetails, active: Sequence[ItineraryItem]) -> list[str]:
    if details.flights_skipped:
        return []
    directions = {item.direction for item in active if isinstance(item, FlightItem)}
    missing: list[str] = []
    if "outbound" not in directions:
        missing.append("a flight out")
    if "return" not in directions:
        missing.append("a flight home")
    return missing


def _night_label(night: date) -> str:
    return f"{night:%b} {night.day}"


def _lodging_missing(details: PlanDetails, active: Sequence[ItineraryItem]) -> list[str]:
    if details.start_date is None or details.end_date is None:
        return ["trip dates"]
    days = list(date_range(details.start_date, details.end_date))
    return [
        f"a place to stay the night of {_night_label(night)}"
        for night in uncovered_nights(days, active)
    ]


def _activities_missing(active: Sequence[ItineraryItem]) -> list[str]:
    return [] if any(item.type == "activity" for item in active) else ["at least one activity"]


def _ground_missing(gaps: Sequence[Gap]) -> list[str]:
    return [
        f"a transfer from {describe_place(gap.from_place, gap.after_item_id)} "
        f"to {describe_place(gap.to_place, gap.before_item_id)}"
        for gap in gaps
    ]


def plan_progress(details: PlanDetails, items: Sequence[ItineraryItem]) -> Progress:
    """Each stage's checklist for the plan as it stands. Cancelled items don't count."""
    active = [item for item in items if item.status != "cancelled"]
    gaps = find_gaps(active, details.origin)
    missing: dict[PlanStage, list[str]] = {
        "basics": _basics_missing(details),
        "flights": _flights_missing(details, active),
        "lodging": _lodging_missing(details, active),
        "activities": _activities_missing(active),
        "ground": _ground_missing(gaps),
    }
    # Review is the finish line: ready once every earlier stage is.
    missing["review"] = [
        f"the {STAGE_LABELS[stage]} stage" for stage in STAGES[:-1] if missing[stage]
    ]
    stages = [
        StageStatus(stage=stage, ready=not missing[stage], missing=missing[stage])
        for stage in STAGES
    ]
    return Progress(stages=stages, gaps=gaps)


def stage_status(plan: TravelPlan, stage: PlanStage) -> StageStatus:
    return next(status for status in plan.stages if status.stage == stage)


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def _has_round_trip(plan: TravelPlan) -> bool:
    directions = {
        item.direction
        for item in plan.items
        if isinstance(item, FlightItem) and item.status != "cancelled"
    }
    return {"outbound", "return"} <= directions


def apply_stage_change(plan: TravelPlan, change: StageChange) -> tuple[PlanStage, bool]:
    """The plan's new `(stage, flights_skipped)` after `change`, or `StageError`."""
    index = STAGES.index(plan.stage)
    match change.action:
        case "next":
            if plan.stage == "review":
                raise StageError("This is the last stage.")
            status = stage_status(plan, plan.stage)
            if not status.ready:
                raise StageError(
                    f"Finish the {STAGE_LABELS[plan.stage]} stage first: "
                    f"{', '.join(status.missing)}."
                )
            skipped = plan.flights_skipped
            if plan.stage == "flights" and _has_round_trip(plan):
                # The user went back and added flights after skipping them.
                skipped = False
            return STAGES[index + 1], skipped
        case "back":
            if index == 0:
                raise StageError("This is the first stage.")
            return STAGES[index - 1], plan.flights_skipped
        case "skip_flights":
            if plan.stage != "flights":
                raise StageError("Flights can only be skipped from the flights stage.")
            return "lodging", True
        case "go_to":
            # The validator guarantees a stage for go_to.
            assert change.stage is not None
            if STAGES.index(change.stage) >= index:
                raise StageError("You can only go back to an earlier stage; use Next to go on.")
            return change.stage, plan.flights_skipped
