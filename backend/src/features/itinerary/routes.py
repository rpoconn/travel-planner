"""Flight routes: the traveller can't be on two flights at once, and each flight leaves from
the airport they're at.

Where they are comes from the flight that landed last: after PHL → NRT they're in Tokyo,
so the next flight has to leave from NRT or HND (airports of the same city count as one).
A ground transfer between two flights can take them anywhere (land at NRT, train to Osaka,
fly out of KIX), and transfer places rarely have airport codes, so a transfer in between
skips the departure check.

Pure functions over validated schemas, like the zone and budget rules: the agent's tools
reject a flight that breaks them, and the timeline warns about flights already in the plan.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from features.itinerary.airports import same_area
from features.itinerary.schemas import FlightFields, ItemFields, TransferFields
from shared.domain import Place

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteProblem:
    message: str
    other: FlightFields  # the flight it clashes with


def _flights(items: Sequence[ItemFields]) -> list[FlightFields]:
    return [i for i in items if isinstance(i, FlightFields) and i.status != "cancelled"]


def _code(place: Place) -> str:
    return place.iata or place.name


def _same_airport(a: Place, b: Place) -> bool:
    if a.iata and b.iata:
        return same_area(a.iata, b.iata)
    return a.name.casefold() == b.name.casefold()


def _ground_between(items: Sequence[ItemFields], after: datetime, before: datetime) -> bool:
    return any(
        isinstance(item, TransferFields)
        and item.status != "cancelled"
        and item.start < before
        and item.end > after
        for item in items
    )


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def overlap_problem(flight: FlightFields, others: Sequence[ItemFields]) -> RouteProblem | None:
    """The first of `others`' flights that is in the air at the same time as `flight`."""
    for other in sorted(_flights(others), key=lambda f: f.start):
        if other.start < flight.end and flight.start < other.end:
            return RouteProblem(f"{flight.title} overlaps the flight {other.title}", other)
    return None


def departure_problem(flight: FlightFields, others: Sequence[ItemFields]) -> RouteProblem | None:
    """Why `flight` doesn't leave from where the flight before it landed, or None. The
    first flight of a trip is never wrong: the traveller may drive to another airport."""
    landed = [f for f in _flights(others) if f.end <= flight.start]
    if not landed:
        return None
    previous = max(landed, key=lambda f: f.end)
    if _same_airport(previous.to_place, flight.from_place):
        return None
    if _ground_between(others, previous.end, flight.start):
        return None
    return RouteProblem(
        f"{flight.title} leaves from {_code(flight.from_place)}, but after {previous.title} "
        f"the traveller is at {_code(previous.to_place)}",
        previous,
    )


def route_problem(flight: FlightFields, others: Sequence[ItemFields]) -> RouteProblem | None:
    """Why a new or changed `flight` doesn't fit the other flights, or None. `others` is the
    rest of the plan, without `flight` itself. Besides overlaps and its own departure, the
    flight after it has to leave from where this one lands: slotting a duplicate PHL → NRT
    in front of the planned one strands that one at PHL."""
    if flight.status == "cancelled":
        return None
    problem = overlap_problem(flight, others) or departure_problem(flight, others)
    if problem is not None:
        return problem
    later = [f for f in _flights(others) if f.start >= flight.end]
    if not later:
        return None
    following = min(later, key=lambda f: f.start)
    rest = [item for item in others if item is not following]
    problem = departure_problem(following, [*rest, flight])
    return None if problem is None else RouteProblem(problem.message, following)
