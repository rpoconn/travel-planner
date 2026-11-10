"""Local-calendar-day helpers shared by the timeline, the gap finder and plan validation.

Days are *local* calendar days: an item's first day is the date of `start` in its timezone
and its last day is the date of `end` in its end timezone, which is what the traveller
sees on their watch (an overnight LAX→SYD flight touches three days).
"""

from collections.abc import Iterator, Sequence
from datetime import date, time, timedelta

from features.itinerary.schemas import ItemType, ItineraryItem


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


def date_range(first: date, last: date) -> Iterator[date]:
    """Every day from `first` to `last`, inclusive."""
    for offset in range((last - first).days + 1):
        yield first + timedelta(days=offset)


def uncovered_nights(days: Sequence[date], items: Sequence[ItineraryItem]) -> list[date]:
    """The nights in `days` with nowhere to sleep (the last day has no night to cover).

    Lodging covers each night from check-in to the night before check-out; a flight or
    transfer running past midnight (red-eye, sleeper train) covers its nights too.
    Activities never do. The caller filters out cancelled items.
    """
    covered: set[date] = set()
    for item in items:
        if item.type == ItemType.ACTIVITY:
            continue
        first, last = item_days(item)
        if last > first:
            covered.update(date_range(first, last - timedelta(days=1)))
    return [night for night in days[:-1] if night not in covered]
