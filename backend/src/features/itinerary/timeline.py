"""Timeline derivation: turns a plan's items into day columns, lodging bars and warnings.

Pure functions over validated schemas (no database), so the layout rules are unit-tested
directly. Days are local calendar days (see `itinerary.days`).
"""

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from features.itinerary.budget import (
    cost_error,
    limit_error,
    needs_cost,
    total_error,
)
from features.itinerary.days import date_range, item_days, uncovered_nights
from features.itinerary.gaps import describe_place, find_gaps
from features.itinerary.routes import departure_problem
from features.itinerary.schemas import (
    FlightItem,
    Gap,
    ItemType,
    ItineraryItem,
    LodgingItem,
    LodgingSpan,
    SpendingLimit,
    Timeline,
    TimelineCardItem,
    TimelineDay,
    TimelineEntry,
    TimelineWarning,
    active,
)
from features.itinerary.zones import arrival_zone, legs, zone_error
from shared.domain import IssueCode, Money, Place, Travelers

# Re-exported: tests and callers have always imported it from here.
__all__ = ["build_timeline", "item_days", "warnings_from_gaps"]

# ---------------------------------------------------------------------------
# Day helpers
# ---------------------------------------------------------------------------


def _label(day: date) -> str:
    return f"{day:%b} {day.day}"


def _day_zones(days: Sequence[date], items: Sequence[ItineraryItem]) -> list[str | None]:
    """The zone the traveller ends each day in: the arrival zone of the last leg that has
    landed by then (on its local arrival day), else the first leg's departure zone."""
    ordered = legs(items)
    if not ordered:
        return [None for _ in days]
    zones: list[str | None] = []
    for day in days:
        landed = [leg for leg in ordered if item_days(leg)[1] <= day]
        zones.append(arrival_zone(landed[-1]) if landed else ordered[0].timezone)
    return zones


# ---------------------------------------------------------------------------
# Transfer gaps
# ---------------------------------------------------------------------------


def warnings_from_gaps(gaps: Sequence[Gap]) -> list[TimelineWarning]:
    """One `missing_transfer` warning per gap, dated by when the traveller must arrive."""
    warnings: list[TimelineWarning] = []
    for gap in gaps:
        moment = gap.arrive_before or gap.leave_after
        warnings.append(
            TimelineWarning(
                kind=IssueCode.MISSING_TRANSFER,
                message=(
                    f"No transfer from {describe_place(gap.from_place, gap.after_item_id)} "
                    f"to {describe_place(gap.to_place, gap.before_item_id)}"
                ),
                date=moment.date() if moment is not None else None,
                item_ids=[i for i in (gap.after_item_id, gap.before_item_id) if i is not None],
            )
        )
    return warnings


# ---------------------------------------------------------------------------
# Other warnings
# ---------------------------------------------------------------------------


def _overlaps(items: Sequence[ItineraryItem]) -> list[TimelineWarning]:
    warnings: list[TimelineWarning] = []
    ordered = sorted(items, key=lambda i: i.start)
    for index, a in enumerate(ordered):
        for b in ordered[index + 1 :]:
            if b.start >= a.end:
                break  # sorted by start: nothing later can overlap `a`
            warnings.append(
                TimelineWarning(
                    kind=IssueCode.OVERLAP,
                    message=f"{a.title} overlaps {b.title}",
                    date=item_days(b)[0],
                    item_ids=[a.id, b.id],
                )
            )
    return warnings


def _nights_without_lodging(
    days: Sequence[date], items: Sequence[ItineraryItem]
) -> list[TimelineWarning]:
    return [
        TimelineWarning(
            kind=IssueCode.UNCOVERED_NIGHT,
            message=f"No lodging for the night of {_label(night)}",
            date=night,
        )
        for night in uncovered_nights(days, items)
    ]


def _outside_trip(
    start_date: date | None, end_date: date | None, items: Sequence[ItineraryItem]
) -> list[TimelineWarning]:
    warnings: list[TimelineWarning] = []
    for item in items:
        first, last = item_days(item)
        if (start_date is not None and first < start_date) or (
            end_date is not None and last > end_date
        ):
            warnings.append(
                TimelineWarning(
                    kind=IssueCode.OUTSIDE_TRIP,
                    message=f"{item.title} is outside the trip dates",
                    date=first,
                    item_ids=[item.id],
                )
            )
    return warnings


def _zone_warnings(items: Sequence[ItineraryItem]) -> list[TimelineWarning]:
    warnings: list[TimelineWarning] = []
    for item in items:
        result = zone_error(item, [other for other in items if other.id != item.id])
        if result is not None:
            warnings.append(
                TimelineWarning(
                    kind=IssueCode.WRONG_TIMEZONE,
                    message=result[0],
                    date=item_days(item)[0],
                    item_ids=[item.id],
                )
            )
    return warnings


def _route_warnings(items: Sequence[ItineraryItem]) -> list[TimelineWarning]:
    # Each flight against the one before it, so a broken link is reported once. Flights in
    # the air together are already an "overlap".
    warnings: list[TimelineWarning] = []
    for flight in items:
        if not isinstance(flight, FlightItem):
            continue
        problem = departure_problem(flight, [other for other in items if other.id != flight.id])
        if problem is not None and isinstance(problem.other, FlightItem):
            warnings.append(
                TimelineWarning(
                    kind=IssueCode.WRONG_DEPARTURE,
                    message=problem.message,
                    date=item_days(flight)[0],
                    item_ids=[problem.other.id, flight.id],
                )
            )
    return warnings


def _budget_warnings(
    budget: Money | None,
    limits: Sequence[SpendingLimit],
    travelers: Travelers | None,
    items: Sequence[ItineraryItem],
) -> list[TimelineWarning]:
    # A pick can't add items that break these rules, but the user can add items,
    # set a budget after the fact or lower it, so the plan is checked as it stands.
    warnings: list[TimelineWarning] = []
    for item in items:
        if not needs_cost(item, budget, limits):
            continue
        day = item_days(item)[0]
        error = cost_error(item, budget)
        if error is not None:
            warnings.append(
                TimelineWarning(
                    kind=IssueCode.MISSING_COST, message=error, date=day, item_ids=[item.id]
                )
            )
            continue
        error = limit_error(item, limits, travelers)
        if error is not None:
            warnings.append(
                TimelineWarning(
                    kind=IssueCode.OVER_LIMIT, message=error, date=day, item_ids=[item.id]
                )
            )
    error = total_error(budget, (item.cost for item in items))
    if error is not None:
        # About the whole trip, so no date or items.
        warnings.append(TimelineWarning(kind=IssueCode.OVER_BUDGET, message=f"The trip {error}"))
    return warnings


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_timeline(
    *,
    plan_id: UUID,
    version: int,
    start_date: date | None,
    end_date: date | None,
    items: Sequence[ItineraryItem],
    budget: Money | None = None,
    limits: Sequence[SpendingLimit] = (),
    travelers: Travelers | None = None,
    origin: Place | None = None,
) -> Timeline:
    spans_by_item = {item.id: item_days(item) for item in items}

    # Day range: the plan's dates widened to every item, so nothing falls off the panel.
    plan_dates = [d for d in (start_date, end_date) if d is not None]
    lows = plan_dates + [first for first, _ in spans_by_item.values()]
    highs = plan_dates + [last for _, last in spans_by_item.values()]
    if not lows:
        return Timeline(
            plan_id=plan_id, version=version, days=[], lodging=[], warnings=[], home=origin
        )
    range_first, range_last = min(lows), max(highs)
    dates = list(date_range(range_first, range_last))

    # Cards per day (everything except lodging, which is drawn as spans).
    ordered = sorted(items, key=lambda i: (i.start, i.end, i.title))
    zones = _day_zones(dates, active(ordered))
    days: list[TimelineDay] = []
    for index, day in enumerate(dates):
        entries: list[TimelineEntry] = []
        for item in ordered:
            if isinstance(item, LodgingItem):
                continue
            first, last = spans_by_item[item.id]
            if first <= day <= last:
                card: TimelineCardItem = item
                entries.append(
                    TimelineEntry(
                        item=card,
                        continues_from_previous_day=day > first,
                        continues_to_next_day=day < last,
                    )
                )
        days.append(TimelineDay(index=index, date=day, entries=entries, timezone=zones[index]))

    lodging = [
        LodgingSpan(
            item=item,
            start_day_index=(spans_by_item[item.id][0] - range_first).days,
            end_day_index=(spans_by_item[item.id][1] - range_first).days,
        )
        for item in ordered
        if isinstance(item, LodgingItem)
    ]

    # Cancelled items stay visible (the UI greys them out) but don't count for warnings.
    current = active(ordered)
    gaps = find_gaps(current, origin)
    warnings = [
        *_outside_trip(start_date, end_date, current),
        *_nights_without_lodging(dates, current),
        *_overlaps([i for i in current if i.type != ItemType.LODGING]),
        *_overlaps([i for i in current if i.type == ItemType.LODGING]),
        *warnings_from_gaps(gaps),
        *_zone_warnings(current),
        *_route_warnings(current),
        *_budget_warnings(budget, limits, travelers, current),
    ]
    warnings.sort(key=lambda w: w.date or range_first)

    return Timeline(
        plan_id=plan_id,
        version=version,
        days=days,
        lodging=lodging,
        warnings=warnings,
        gaps=gaps,
        home=origin,
    )
