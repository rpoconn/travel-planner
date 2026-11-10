"""The validation engine: what stands between the plan and a finished trip.

A pure function (no database, no model). `validate` returns a flat list of problems and
nothing else: it does not say what to do next, and it moves nothing. Whoever drives the
plan (the model, through the `validate_plan` tool) decides which problem to work on; the
plan is complete once no blocking problem is left.

Blocking problems are what a finished trip needs and the plan lacks, in the order their
fixes are usually made: the basics, the flights, the stays, the activities, then the rides
between them. Warnings come from the timeline (`itinerary.timeline`): overlaps, costs over
a budget, items outside the trip. They don't block.
"""

from collections.abc import Sequence
from datetime import date, timedelta

from features.itinerary.days import date_range, uncovered_nights
from features.itinerary.gaps import describe_place
from features.itinerary.schemas import (
    FlightItem,
    Gap,
    ItemType,
    ItineraryItem,
    TimelineWarning,
    active,
)
from features.planning.schemas import NightRange, Problem, ProblemFix
from features.planning.slots import FIX_SLOT, SLOT_ORDER, SLOTS
from features.plans.schemas import PlanSnapshot
from shared.domain import IssueCode

# Timeline warnings that aren't reported as warnings: they are blocking problems already.
BLOCKING_WARNINGS = frozenset({IssueCode.UNCOVERED_NIGHT, IssueCode.MISSING_TRANSFER})

# ---------------------------------------------------------------------------
# Night runs
# ---------------------------------------------------------------------------


def night_ranges(nights: Sequence[date]) -> list[NightRange]:
    """Runs of consecutive nights, each as one stay (check out the morning after)."""
    ranges: list[NightRange] = []
    for night in sorted(nights):
        if ranges and ranges[-1].check_out == night:
            ranges[-1] = NightRange(check_in=ranges[-1].check_in, check_out=night + timedelta(1))
        else:
            ranges.append(NightRange(check_in=night, check_out=night + timedelta(1)))
    return ranges


def range_label(nights: NightRange) -> str:
    check_in, check_out = nights.check_in, nights.check_out
    if check_out - check_in == timedelta(days=1):
        return f"the night of {check_in:%b} {check_in.day}"
    return f"{check_in:%b} {check_in.day} to {check_out:%b} {check_out.day}"


# ---------------------------------------------------------------------------
# Blocking problems: what a finished trip needs
# ---------------------------------------------------------------------------


def _fix(code: IssueCode) -> ProblemFix:
    """How a problem is usually fixed: by asking the slot that answers it, by searching for
    candidates to pick from, or (no slot) by editing the items it names."""
    slot = FIX_SLOT.get(code)
    if slot is None:
        return ProblemFix(how="edit")
    return ProblemFix(how="ask" if SLOTS[slot].kind == "input" else "search", slot=slot)


def _missing(code: IssueCode, message: str) -> Problem:
    return Problem(id=code.value, code=code, severity="blocking", message=message, fix=_fix(code))


def _basics(plan: PlanSnapshot) -> list[Problem]:
    problems: list[Problem] = []
    if plan.start_date is None or plan.end_date is None:
        problems.append(_missing(IssueCode.MISSING_DATES, "trip dates"))
    if plan.travelers is None:
        problems.append(_missing(IssueCode.MISSING_TRAVELERS, "who is travelling"))
    if plan.origin is None:
        problems.append(_missing(IssueCode.MISSING_ORIGIN, "where the trip starts (home)"))
    if plan.destination is None:
        problems.append(_missing(IssueCode.MISSING_DESTINATION, "a destination"))
    return problems


def _flights(plan: PlanSnapshot, items: Sequence[ItineraryItem]) -> list[Problem]:
    if plan.no_flights:
        return []
    directions = {item.direction for item in items if isinstance(item, FlightItem)}
    problems: list[Problem] = []
    if "outbound" not in directions:
        problems.append(_missing(IssueCode.MISSING_OUTBOUND, "a flight out"))
    if "return" not in directions:
        problems.append(_missing(IssueCode.MISSING_RETURN, "a flight home"))
    return problems


def _stays(plan: PlanSnapshot, items: Sequence[ItineraryItem]) -> list[Problem]:
    """One problem per run of uncovered nights: a run is fixed by one stay. A trip without
    dates has no nights to cover yet."""
    if plan.start_date is None or plan.end_date is None:
        return []
    days = list(date_range(plan.start_date, plan.end_date))
    return [
        Problem(
            id=f"{IssueCode.UNCOVERED_NIGHT.value}:{run.check_in.isoformat()}",
            code=IssueCode.UNCOVERED_NIGHT,
            severity="blocking",
            message=f"a place to stay {range_label(run)}",
            date=run.check_in,
            nights=run,
            fix=_fix(IssueCode.UNCOVERED_NIGHT),
        )
        for run in night_ranges(uncovered_nights(days, items))
    ]


def _activities(items: Sequence[ItineraryItem]) -> list[Problem]:
    if any(item.type == ItemType.ACTIVITY for item in items):
        return []
    return [_missing(IssueCode.NO_ACTIVITIES, "at least one activity")]


def _ride(gap: Gap) -> Problem:
    moment = gap.arrive_before or gap.leave_after
    # A gap's neighbours. Home is not an item, so an end without one is home.
    ends = (gap.after_item_id, gap.before_item_id)
    code = IssueCode.MISSING_TRANSFER
    return Problem(
        id=f"{code.value}:{ends[0] or 'home'}:{ends[1] or 'home'}",
        code=code,
        severity="blocking",
        message=(
            f"a transfer from {describe_place(gap.from_place, gap.after_item_id)} "
            f"to {describe_place(gap.to_place, gap.before_item_id)}"
        ),
        date=moment.date() if moment is not None else None,
        item_ids=[item_id for item_id in ends if item_id is not None],
        gap=gap,
        fix=_fix(code),
    )


def blocking_problems(plan: PlanSnapshot) -> list[Problem]:
    """What the plan lacks for a finished trip. Cancelled items don't count."""
    current = active(plan.items)
    problems = [
        *_basics(plan),
        *_flights(plan, current),
        *_stays(plan, current),
        *_activities(current),
        *(_ride(gap) for gap in plan.gaps),
    ]
    # The order their fix slots are listed in (stable, so nights and gaps keep trip order).
    last = len(SLOT_ORDER)
    problems.sort(key=lambda p: SLOT_ORDER[p.fix.slot] if p.fix.slot is not None else last)
    return problems


# ---------------------------------------------------------------------------
# Warnings: what the timeline flags
# ---------------------------------------------------------------------------


def _warning_problem(warning: TimelineWarning) -> Problem:
    parts = [warning.kind.value]
    if warning.date is not None:
        parts.append(warning.date.isoformat())
    parts += [str(item_id) for item_id in warning.item_ids]
    return Problem(
        id=":".join(parts),
        code=warning.kind,
        severity="warning",
        message=warning.message,
        date=warning.date,
        item_ids=warning.item_ids,
        fix=_fix(warning.kind),
    )


def _unique(problems: Sequence[Problem]) -> list[Problem]:
    """`problems` with every id used once. The same problem reported twice is kept once;
    different ones that share an id (two spending limits exceeded, neither about an item)
    are numbered."""
    kept: dict[str, Problem] = {}
    for problem in problems:
        twin = kept.get(problem.id)
        if twin is None:
            kept[problem.id] = problem
        elif twin.model_dump(exclude={"id"}) != problem.model_dump(exclude={"id"}):
            number = 2
            while f"{problem.id}#{number}" in kept:
                number += 1
            renamed = problem.model_copy(update={"id": f"{problem.id}#{number}"})
            kept[renamed.id] = renamed
    return list(kept.values())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def validate(plan: PlanSnapshot, warnings: Sequence[TimelineWarning]) -> list[Problem]:
    """Everything wrong with the plan as it stands: blocking problems first, then the
    timeline's warnings."""
    flagged = [_warning_problem(w) for w in warnings if w.kind not in BLOCKING_WARNINGS]
    return _unique([*blocking_problems(plan), *flagged])


def is_complete(problems: Sequence[Problem]) -> bool:
    """A plan is complete when nothing blocking is left; warnings are the user's call."""
    return not any(problem.severity == "blocking" for problem in problems)
