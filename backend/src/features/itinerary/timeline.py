"""Timeline derivation: turns a plan's items into day columns, lodging bars and warnings.

Pure functions over validated schemas (no database), so the layout rules are unit-tested
directly. Days are *local* calendar days: an item's first day is the date of `start` in its
timezone and its last day is the date of `end` in its end timezone, which is what the
traveller sees on their watch (an overnight LAX→SYD flight touches three days).
"""

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from itertools import pairwise
from uuid import UUID

from features.itinerary.schemas import (
    ActivityItem,
    FlightItem,
    ItineraryItem,
    LodgingItem,
    LodgingSpan,
    Timeline,
    TimelineCardItem,
    TimelineDay,
    TimelineEntry,
    TimelineWarning,
    TransferItem,
)
from shared.domain import Place

# ---------------------------------------------------------------------------
# Day helpers
# ---------------------------------------------------------------------------


def item_days(item: ItineraryItem) -> tuple[date, date]:
    """First and last local day an item touches (inclusive)."""
    first = item.start.date()
    last = item.end.date()
    # An end of exactly midnight belongs to the previous day (an activity until 00:00).
    if item.end > item.start and item.end.time() == time(0):
        last -= timedelta(days=1)
    # Flying east across the date line can land on an earlier local date than takeoff;
    # keep such items on their departure day.
    return first, max(first, last)


def _date_range(first: date, last: date) -> Iterator[date]:
    for offset in range((last - first).days + 1):
        yield first + timedelta(days=offset)


def _label(day: date) -> str:
    return f"{day:%b} {day.day}"


# ---------------------------------------------------------------------------
# Transfer-gap detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Stop:
    """A located stretch of one item on one day, used to spot missing transfers."""

    item_id: UUID
    start: datetime
    end: datetime
    start_place: Place
    end_place: Place
    is_transfer: bool


def _same_place(a: Place, b: Place) -> bool:
    if a.iata is not None and b.iata is not None:
        return a.iata == b.iata
    return a.name.strip().casefold() == b.name.strip().casefold()


def _stops_on(day: date, items: Sequence[ItineraryItem]) -> list[_Stop]:
    stops: list[_Stop] = []
    for item in items:
        first, last = item_days(item)
        if not first <= day <= last:
            continue
        match item:
            case LodgingItem():
                # A stay is only "located" on its check-in and check-out days; on the
                # days in between the traveller starts and ends at the hotel implicitly.
                if day == first:
                    stops.append(
                        _Stop(item.id, item.start, item.start, item.place, item.place, False)
                    )
                if day == last:
                    stops.append(_Stop(item.id, item.end, item.end, item.place, item.place, False))
            case ActivityItem():
                stops.append(_Stop(item.id, item.start, item.end, item.place, item.place, False))
            case FlightItem() | TransferItem():
                stops.append(
                    _Stop(
                        item.id,
                        item.start,
                        item.end,
                        item.from_place,
                        item.to_place,
                        isinstance(item, TransferItem),
                    )
                )
    return sorted(stops, key=lambda s: (s.start, s.end))


def _missing_transfers(
    days: Sequence[date], items: Sequence[ItineraryItem]
) -> list[TimelineWarning]:
    warnings: list[TimelineWarning] = []
    seen: set[tuple[UUID, UUID]] = set()
    for day in days:
        stops = _stops_on(day, items)
        for prev, nxt in pairwise(stops):
            if prev.is_transfer or nxt.is_transfer or prev.item_id == nxt.item_id:
                continue
            if _same_place(prev.end_place, nxt.start_place):
                continue
            # Items spanning several days would otherwise be reported once per day.
            pair = (prev.item_id, nxt.item_id)
            if pair in seen:
                continue
            seen.add(pair)
            warnings.append(
                TimelineWarning(
                    kind="missing_transfer",
                    message=(f"No transfer from {prev.end_place.name} to {nxt.start_place.name}"),
                    date=day,
                    item_ids=[prev.item_id, nxt.item_id],
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
                    kind="overlap",
                    message=f"{a.title} overlaps {b.title}",
                    date=item_days(b)[0],
                    item_ids=[a.id, b.id],
                )
            )
    return warnings


def _nights_without_lodging(
    days: Sequence[date], items: Sequence[ItineraryItem]
) -> list[TimelineWarning]:
    covered: set[date] = set()
    for item in items:
        if item.type == "activity":
            continue
        first, last = item_days(item)
        # Lodging covers each night from check-in to the night before check-out; a
        # flight/transfer running past midnight (red-eye, sleeper train) covers its nights.
        covered.update(_date_range(first, last - timedelta(days=1)) if last > first else ())
    # The last day is the trip's end, so it has no night to cover.
    return [
        TimelineWarning(
            kind="no_lodging", message=f"No lodging for the night of {_label(night)}", date=night
        )
        for night in days[:-1]
        if night not in covered
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
                    kind="outside_trip",
                    message=f"{item.title} is outside the trip dates",
                    date=first,
                    item_ids=[item.id],
                )
            )
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
) -> Timeline:
    spans_by_item = {item.id: item_days(item) for item in items}

    # Day range: the plan's dates widened to every item, so nothing falls off the panel.
    plan_dates = [d for d in (start_date, end_date) if d is not None]
    lows = plan_dates + [first for first, _ in spans_by_item.values()]
    highs = plan_dates + [last for _, last in spans_by_item.values()]
    if not lows:
        return Timeline(plan_id=plan_id, version=version, days=[], lodging=[], warnings=[])
    range_first, range_last = min(lows), max(highs)
    dates = list(_date_range(range_first, range_last))

    # Cards per day (everything except lodging, which is drawn as spans).
    ordered = sorted(items, key=lambda i: (i.start, i.end, i.title))
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
        days.append(TimelineDay(index=index, date=day, entries=entries))

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
    active = [item for item in ordered if item.status != "cancelled"]
    warnings = [
        *_outside_trip(start_date, end_date, active),
        *_nights_without_lodging(dates, active),
        *_overlaps([i for i in active if i.type != "lodging"]),
        *_overlaps([i for i in active if i.type == "lodging"]),
        *_missing_transfers(dates, active),
    ]
    warnings.sort(key=lambda w: w.date or range_first)

    return Timeline(plan_id=plan_id, version=version, days=days, lodging=lodging, warnings=warnings)
