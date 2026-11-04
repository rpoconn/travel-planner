"""Gap finder: every change of place on a trip, home to home, that no transfer covers.

Pure functions over validated schemas (no database). The whole trip is one sequence of
"stops" (where the traveller is, and when), so a 23:30 landing is compared with the next
day's check-in. The plan's origin, when known, adds a home stop at each end; those two
gaps close only when the stop next to home is a transfer (home and the departure airport
are never "the same place", even in the same city).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from itertools import pairwise
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from features.itinerary.days import date_range, item_days
from features.itinerary.schemas import (
    ActivityItem,
    FlightItem,
    Gap,
    ItineraryItem,
    LodgingItem,
    TransferItem,
    active,
)
from shared.domain import Place

# ---------------------------------------------------------------------------
# Stops
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Stop:
    """Where the traveller is from `start` to `end`. `item_id` None is the home stop."""

    item_id: UUID | None
    start: datetime
    end: datetime
    start_place: Place
    end_place: Place
    start_zone: str
    end_zone: str
    is_transfer: bool = False
    is_leg: bool = False  # a flight or transfer: it moves the traveller
    stay_edge: Literal["check_in", "check_out"] | None = None


def same_place(a: Place, b: Place) -> bool:
    if a.iata is not None and b.iata is not None:
        return a.iata == b.iata
    return a.name.strip().casefold() == b.name.strip().casefold()


def _lodging_stops(item: LodgingItem) -> list[_Stop]:
    """Check-in, check-out, and a virtual "back at the hotel" stop at each midnight in
    between. The midnight stop ends one evening and starts the next morning, so a dinner
    out on a middle day is a gap both ways (hotel → restaurant → hotel)."""
    zone = item.timezone

    def at(moment: datetime, edge: Literal["check_in", "check_out"] | None = None) -> _Stop:
        return _Stop(item.id, moment, moment, item.place, item.place, zone, zone, stay_edge=edge)

    first, last = item_days(item)
    midnights = [
        datetime.combine(day, time(0), tzinfo=ZoneInfo(zone))
        for day in date_range(first + timedelta(days=1), last)
    ]
    return [
        at(item.start, "check_in"),
        *(at(midnight) for midnight in midnights),
        at(item.end, "check_out"),
    ]


def _stops(items: Sequence[ItineraryItem]) -> list[_Stop]:
    stops: list[_Stop] = []
    for item in items:
        match item:
            case LodgingItem():
                stops.extend(_lodging_stops(item))
            case ActivityItem():
                zone = item.end_timezone or item.timezone
                stops.append(
                    _Stop(
                        item.id, item.start, item.end, item.place, item.place, item.timezone, zone
                    )
                )
            case FlightItem() | TransferItem():
                stops.append(
                    _Stop(
                        item.id,
                        item.start,
                        item.end,
                        item.from_place,
                        item.to_place,
                        item.timezone,
                        item.end_timezone or item.timezone,
                        is_transfer=isinstance(item, TransferItem),
                        is_leg=True,
                    )
                )
    return _settle_stay_edges(sorted(stops, key=lambda stop: (stop.start, stop.end)))


def _position(stops: list[_Stop], stop: _Stop) -> int:
    # By identity: two stops can compare equal (frozen dataclass) yet be different stops.
    return next(index for index, candidate in enumerate(stops) if candidate is stop)


def _settle_stay_edges(stops: list[_Stop]) -> list[_Stop]:
    """Move check-ins and check-outs to when the traveller actually arrives and leaves.

    A stay's check-in and check-out times say when arriving and leaving is *allowed*, not
    when it happens. A flight landing at 18:00 for a 15:00 check-in, then a taxi to the
    hotel at 18:30, would otherwise sort the check-in between the flight and the taxi and
    report an "airport → hotel" gap the taxi already covers. So a check-in moves to just
    after the run of rides that follows it, when one of those rides ends at the stay; a
    check-out likewise moves to just before a preceding ride that starts there.
    """
    settled = list(stops)
    for stop in [s for s in stops if s.stay_edge == "check_in"]:
        index = _position(settled, stop)
        arrival: int | None = None
        for later in range(index + 1, len(settled)):
            if not settled[later].is_leg:
                break
            if same_place(settled[later].end_place, stop.start_place):
                arrival = later
        if arrival is not None:
            arrived = settled[arrival].end
            settled.insert(arrival + 1, replace(stop, start=arrived, end=arrived))
            del settled[index]  # before `arrival`, so the insert above didn't shift it
    for stop in [s for s in stops if s.stay_edge == "check_out"]:
        index = _position(settled, stop)
        departure: int | None = None
        for earlier in range(index - 1, -1, -1):
            if not settled[earlier].is_leg:
                break
            if same_place(settled[earlier].start_place, stop.end_place):
                departure = earlier
        if departure is not None:
            left = settled[departure].start
            del settled[index]  # after `departure`, so deleting it doesn't shift it
            settled.insert(departure, replace(stop, start=left, end=left))
    return settled


def _home_stops(stops: list[_Stop], origin: Place) -> list[_Stop]:
    """`stops` with home before the first and after the last (instants match the
    neighbouring stop: home has no times of its own)."""
    first, last = stops[0], stops[-1]
    leave = _Stop(
        None, first.start, first.start, origin, origin, first.start_zone, first.start_zone
    )
    back = _Stop(None, last.end, last.end, origin, origin, last.end_zone, last.end_zone)
    return [leave, *stops, back]


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------


def _bridged(prev: _Stop, nxt: _Stop) -> bool:
    """True when no transfer is needed between two consecutive stops."""
    if prev.is_transfer or nxt.is_transfer or prev.item_id == nxt.item_id:
        return True
    if prev.item_id is None or nxt.item_id is None:
        # Leaving or getting home always takes a transfer; only one can bridge it.
        return False
    return same_place(prev.end_place, nxt.start_place)


def _gap(prev: _Stop, nxt: _Stop) -> Gap:
    return Gap(
        from_place=prev.end_place,
        to_place=nxt.start_place,
        after_item_id=prev.item_id,
        before_item_id=nxt.item_id,
        leave_after=prev.end if prev.item_id is not None else None,
        arrive_before=nxt.start if nxt.item_id is not None else None,
        timezone=prev.end_zone if prev.item_id is not None else nxt.start_zone,
    )


def find_gaps(items: Sequence[ItineraryItem], origin: Place | None) -> list[Gap]:
    """Every unbridged change of place, in trip order. Cancelled items are ignored."""
    stops = _stops(active(items))
    if not stops:
        return []
    if origin is not None:
        stops = _home_stops(stops, origin)
    gaps: list[Gap] = []
    seen: set[tuple[UUID | None, UUID | None]] = set()
    for prev, nxt in pairwise(stops):
        if _bridged(prev, nxt):
            continue
        # A hotel's midnight stops can pair with the same item more than once.
        pair = (prev.item_id, nxt.item_id)
        if pair in seen:
            continue
        seen.add(pair)
        gaps.append(_gap(prev, nxt))
    return gaps


def home_label(home: Place) -> str:
    """How a gap names home, e.g. "Home (Philadelphia)"."""
    where = home.city or home.name
    return f"Home ({where})"


def describe_place(place: Place, item_id: UUID | None) -> str:
    """A gap end's name for people: home is called home, anything else by its name."""
    return home_label(place) if item_id is None else place.name
