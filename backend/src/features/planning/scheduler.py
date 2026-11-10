"""When picked activities happen: each one is placed in a free window of the trip.

Pure functions (no database). An offer lists several activities and any of them may be
picked together, so every candidate gets its own window up front, as if all were picked:
no two candidates share a time, and whichever ones the user picks fit together.

The rules:

- A day has fixed windows: morning (10:00), lunch (12:30), afternoon (14:30), dinner
  (19:30) and late (21:30). Restaurants take dinner, else lunch; bars take late; anything
  else takes morning, else afternoon.
- A window must fall while the traveller is at the destination: after the flight out lands
  (plus time to get from the airport) and before the flight home (minus time to get there).
- It must not touch anything already planned (other than lodging), with half an hour
  either side for the transfer added between them.
- It must be within the place's opening hours, when the listing gives them.
- Quieter days go first: days without a flight before travel days, and days with fewer
  activities before busier ones, so picks spread over the trip.
- Times are local to where the traveller is that day (`itinerary.zones`), else to
  `default_zone` (the destination's).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from features.itinerary.days import date_range
from features.itinerary.schemas import ActivityCategory, FlightItem, ItemType, ItineraryItem, active
from features.itinerary.zones import legs, same_zone, zone_at
from features.plans.schemas import PlanSnapshot
from shared.places.types import OpeningHours

Window = Literal["morning", "lunch", "afternoon", "dinner", "late"]

WINDOW_STARTS: dict[Window, time] = {
    "morning": time(10, 0),
    "lunch": time(12, 30),
    "afternoon": time(14, 30),
    "dinner": time(19, 30),
    "late": time(21, 30),
}

_DAYTIME: tuple[Window, ...] = ("morning", "afternoon")
CATEGORY_WINDOWS: dict[ActivityCategory, tuple[Window, ...]] = {
    "sightseeing": _DAYTIME,
    "food": ("dinner", "lunch"),
    "tour": _DAYTIME,
    "museum": _DAYTIME,
    "outdoors": _DAYTIME,
    "nightlife": ("late",),
    "shopping": ("afternoon", "morning"),
    "relax": ("afternoon", "morning"),
    "other": _DAYTIME,
}

DURATIONS: dict[ActivityCategory, timedelta] = {
    "sightseeing": timedelta(hours=2),
    "food": timedelta(hours=1, minutes=30),
    "tour": timedelta(hours=3),
    "museum": timedelta(hours=2),
    "outdoors": timedelta(hours=2),
    "nightlife": timedelta(hours=2),
    "shopping": timedelta(hours=2),
    "relax": timedelta(hours=2),
    "other": timedelta(hours=2),
}

# Room left around each planned item for getting to and from it.
BUFFER = timedelta(minutes=30)
# Getting through an airport: out of it after landing, into it before taking off.
AFTER_LANDING = timedelta(hours=1, minutes=30)
BEFORE_TAKEOFF = timedelta(hours=3)


@dataclass(frozen=True)
class Request:
    """One activity to place. `key` names it in the result."""

    key: str
    category: ActivityCategory
    hours: Sequence[OpeningHours] | None = None  # None: the listing doesn't say


@dataclass(frozen=True)
class Slot:
    start: datetime  # aware, in `zone`
    end: datetime
    zone: str


@dataclass(frozen=True)
class _Busy:
    start: datetime
    end: datetime


# ---------------------------------------------------------------------------
# What the trip already holds
# ---------------------------------------------------------------------------


def _active(plan: PlanSnapshot) -> list[ItineraryItem]:
    return active(plan.items)


def _busy(items: Sequence[ItineraryItem]) -> list[_Busy]:
    """Everything but lodging, padded: flights by airport time, the rest by `BUFFER`."""
    busy: list[_Busy] = []
    for item in items:
        if item.type == ItemType.LODGING:
            continue
        if isinstance(item, FlightItem):
            busy.append(_Busy(item.start - BEFORE_TAKEOFF, item.end + AFTER_LANDING))
        else:
            busy.append(_Busy(item.start - BUFFER, item.end + BUFFER))
    return busy


def _presence(items: Sequence[ItineraryItem]) -> tuple[datetime | None, datetime | None]:
    """When the traveller is at the destination: after the flights out land, before the
    flights home leave. None where there's no such flight (skipped flights)."""
    flights = [item for item in items if isinstance(item, FlightItem)]
    arrivals = [f.end + AFTER_LANDING for f in flights if f.direction == "outbound"]
    departures = [f.start - BEFORE_TAKEOFF for f in flights if f.direction == "return"]
    return (max(arrivals) if arrivals else None, min(departures) if departures else None)


def _flight_days(items: Sequence[ItineraryItem], zone_of: dict[date, str]) -> set[date]:
    days: set[date] = set()
    for item in items:
        if not isinstance(item, FlightItem):
            continue
        for moment in (item.start, item.end):
            for day, zone in zone_of.items():
                if moment.astimezone(ZoneInfo(zone)).date() == day:
                    days.add(day)
    return days


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def is_open(hours: Sequence[OpeningHours] | None, start: datetime, end: datetime) -> bool:
    """Whether a place is open from `start` to `end` (local, aware). Unknown hours count as
    open; a closing at or before the opening runs past midnight."""
    if hours is None:
        return True
    zone = start.tzinfo
    for opening in hours:
        if opening.weekday != start.weekday():
            continue
        opens = datetime.combine(start.date(), opening.opens, zone)
        closes = datetime.combine(start.date(), opening.closes, zone)
        if closes <= opens:
            closes += timedelta(days=1)
        if opens <= start and end <= closes:
            return True
    return False


def _clashes(start: datetime, end: datetime, busy: Sequence[_Busy]) -> bool:
    return any(start < other.end and other.start < end for other in busy)


# ---------------------------------------------------------------------------
# Placing
# ---------------------------------------------------------------------------


def _day_zone(day: date, ordered_legs: Sequence[ItineraryItem], default_zone: str) -> str:
    """Where the traveller is at midday on `day`."""
    probe = datetime.combine(day, time(12), ZoneInfo(default_zone))
    return zone_at(probe, ordered_legs) or default_zone


def schedule(plan: PlanSnapshot, requests: Sequence[Request], default_zone: str) -> dict[str, Slot]:
    """A window for each request that has one, by key, placed in order (the first get the
    best days). Requests with no free window are left out."""
    if plan.start_date is None or plan.end_date is None:
        return {}
    items = _active(plan)
    ordered_legs = legs(items)
    days = list(date_range(plan.start_date, plan.end_date))
    zone_of = {day: _day_zone(day, ordered_legs, default_zone) for day in days}
    travel_days = _flight_days(items, zone_of)
    arrive, leave = _presence(items)
    busy = _busy(items)
    per_day = {day: 0 for day in days}
    for item in items:
        # An item's times are local to its own zone, which is where the traveller is then.
        if item.type == ItemType.ACTIVITY and item.start.date() in per_day:
            per_day[item.start.date()] += 1

    placed: dict[str, Slot] = {}
    for request in requests:
        order = sorted(days, key=lambda d: (d in travel_days, per_day[d], d))
        slot = _first_free(request, order, zone_of, ordered_legs, (arrive, leave), busy)
        if slot is None:
            continue
        placed[request.key] = slot
        busy.append(_Busy(slot.start - BUFFER, slot.end + BUFFER))
        per_day[slot.start.date()] += 1
    return placed


def _first_free(
    request: Request,
    days: Sequence[date],
    zone_of: dict[date, str],
    ordered_legs: Sequence[ItineraryItem],
    presence: tuple[datetime | None, datetime | None],
    busy: Sequence[_Busy],
) -> Slot | None:
    """The first of the request's windows, over `days` in order, that is free."""
    arrive, leave = presence
    for day in days:
        for window in CATEGORY_WINDOWS[request.category]:
            duration = DURATIONS[request.category]
            slot = _try(day, window, duration, zone_of[day], ordered_legs, request)
            if slot is None:
                continue
            if arrive is not None and slot.start < arrive:
                continue
            if leave is not None and slot.end > leave:
                continue
            if not _clashes(slot.start, slot.end, busy):
                return slot
    return None


def _try(
    day: date,
    window: Window,
    duration: timedelta,
    zone: str,
    ordered_legs: Sequence[ItineraryItem],
    request: Request,
) -> Slot | None:
    """The window on `day` in `zone`, when the place is open then and the traveller is
    still in `zone` when it starts (a flight can move them during the day)."""
    start = datetime.combine(day, WINDOW_STARTS[window], ZoneInfo(zone))
    end = start + duration
    if not is_open(request.hours, start, end):
        return None
    actual = zone_at(start, ordered_legs)
    if actual is not None and not same_zone(actual, zone, start):
        return None
    return Slot(start=start, end=end, zone=zone)
