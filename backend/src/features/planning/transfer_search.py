"""Ways to get across one gap of the trip, for the ground stage. Rules only: no provider
and no model.

The engine names the gap (`RunSearch.gap`, from `itinerary.gaps`). What it joins decides
the ways offered:

| Gap                                     | Ways, in order                                |
|-----------------------------------------|-----------------------------------------------|
| home and an airport                     | taxi, rideshare, car service, train           |
| an airport and anywhere else            | taxi, rideshare, shuttle, train, car rental   |
| home and a stay, or stays in two cities | train, bus, car rental, taxi                  |
| anything else (around town)             | walk first when close, taxi, rideshare, metro |

How long each takes comes from the distance when both ends have coordinates, else from a
typical time for that kind of gap. Prices are estimates in the plan's currency: taxis and
cars per vehicle, trains, buses and shuttles per person.

Times are fitted into the gap's window (`fit`): out of a stay or from home, arrive just
before what's next (two hours early for a flight); after a flight, an activity or a ride,
leave as soon as it ends. A window too short for the ride is used whole, with a warning.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from features.itinerary.budget import party_size
from features.itinerary.gaps import describe_place
from features.itinerary.schemas import FlightItem, Gap, ItineraryItem, LodgingItem, TransferMode
from features.planning.schemas import Candidate, CandidateChange, RunSearch
from features.planning.search_types import SearchContext, SearchFailedError
from features.plans.schemas import PlanSnapshot, active_budget
from shared.costs import format_money
from shared.domain import Money, Place

GapKind = Literal["home_airport", "airport", "between_cities", "around_town"]

# Arrive this early for a flight.
BEFORE_FLIGHT = timedelta(hours=2)
_MIN_RIDE = timedelta(minutes=1)
# Close enough to walk.
WALKING_KM = 2.0
_MAX_TITLE = 200

MODES: dict[GapKind, tuple[TransferMode, ...]] = {
    "home_airport": ("taxi", "rideshare", "car_service", "train"),
    "airport": ("taxi", "rideshare", "shuttle", "train", "car_rental"),
    "between_cities": ("train", "bus", "car_rental", "taxi"),
    "around_town": ("taxi", "rideshare", "metro", "walk"),
}

# Minutes, when the distance isn't known.
TYPICAL_MINUTES: dict[GapKind, dict[TransferMode, int]] = {
    "home_airport": {"taxi": 45, "rideshare": 45, "car_service": 45, "train": 60},
    "airport": {"taxi": 35, "rideshare": 35, "shuttle": 60, "train": 45, "car_rental": 60},
    "between_cities": {"train": 150, "bus": 210, "car_rental": 180, "taxi": 120},
    "around_town": {"taxi": 15, "rideshare": 15, "metro": 25, "walk": 20},
}
# (km/h, minutes of waiting, parking or walking to the stop), when it is.
SPEEDS: dict[TransferMode, tuple[float, int]] = {
    "walk": (4.5, 0),
    "taxi": (30, 5),
    "rideshare": (30, 8),
    "car_service": (30, 5),
    "metro": (25, 10),
    "train": (70, 20),
    "bus": (40, 15),
    "shuttle": (25, 20),
    "car_rental": (50, 30),
    "ferry": (20, 20),
}

# Per vehicle when the distance isn't known; per km (plus a base fare) when it is.
_TYPICAL_FARE: dict[GapKind, int] = {
    "home_airport": 55,
    "airport": 40,
    "between_cities": 200,
    "around_town": 15,
}
_TAXI_BASE, _TAXI_PER_KM = 4, Decimal("1.5")
_VEHICLE_FACTOR: dict[TransferMode, Decimal] = {
    "taxi": Decimal(1),
    "rideshare": Decimal("0.9"),
    "car_service": Decimal("1.6"),
}
# Per person.
_TICKETS: dict[TransferMode, dict[GapKind, int]] = {
    "train": {"home_airport": 15, "airport": 10, "between_cities": 50, "around_town": 4},
    "bus": {"home_airport": 10, "airport": 8, "between_cities": 25, "around_town": 3},
    "metro": {"home_airport": 5, "airport": 5, "between_cities": 5, "around_town": 3},
    "shuttle": {"home_airport": 25, "airport": 18, "between_cities": 40, "around_town": 10},
}
_CAR_RENTAL_PER_DAY = 60

_LABELS: dict[TransferMode, str] = {
    "car_rental": "Rental car",
    "car_service": "Car service",
    "taxi": "Taxi",
    "rideshare": "Rideshare",
    "train": "Train",
    "bus": "Bus",
    "ferry": "Ferry",
    "shuttle": "Airport shuttle",
    "walk": "Walk",
    "metro": "Metro",
}


@dataclass(frozen=True)
class Ride:
    start: datetime
    end: datetime
    warning: str | None = None  # the window was too short


# ---------------------------------------------------------------------------
# What the gap joins
# ---------------------------------------------------------------------------


def _item(plan: PlanSnapshot, item_id: UUID | None) -> ItineraryItem | None:
    return next((item for item in plan.items if item.id == item_id), None)


def _is_airport(place: Place, item: ItineraryItem | None) -> bool:
    return place.iata is not None or isinstance(item, FlightItem)


def _city(place: Place) -> str:
    return (place.city or place.name).strip().casefold()


def gap_kind(plan: PlanSnapshot, gap: Gap) -> GapKind:
    after, before = _item(plan, gap.after_item_id), _item(plan, gap.before_item_id)
    home = gap.after_item_id is None or gap.before_item_id is None
    airport = _is_airport(gap.from_place, after) or _is_airport(gap.to_place, before)
    if home and airport:
        return "home_airport"
    if airport:
        return "airport"
    if home:
        # Home to a stay with no flight between (flights skipped): a trip between cities.
        return "between_cities"
    stays = isinstance(after, LodgingItem) and isinstance(before, LodgingItem)
    if stays and _city(gap.from_place) != _city(gap.to_place):
        return "between_cities"
    return "around_town"


def distance_km(a: Place, b: Place) -> float | None:
    """As the crow flies (haversine), when both places have coordinates."""
    if a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return None
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat, dlng = lat2 - lat1, math.radians(b.lng - a.lng)
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(h))


def modes(kind: GapKind, km: float | None) -> list[TransferMode]:
    """The ways to offer, best first. Around town, walking leads when it's close and is
    left out when it's far."""
    offered = list(MODES[kind])
    if kind == "around_town" and km is not None:
        offered.remove("walk")
        if km <= WALKING_KM:
            offered.insert(0, "walk")
    return offered


# ---------------------------------------------------------------------------
# Time and price
# ---------------------------------------------------------------------------


def duration(mode: TransferMode, kind: GapKind, km: float | None) -> timedelta:
    """How long the ride takes, rounded up to five minutes."""
    if km is None:
        return timedelta(minutes=TYPICAL_MINUTES[kind][mode])
    speed, overhead = SPEEDS[mode]
    minutes = km / speed * 60 + overhead
    return timedelta(minutes=max(5, math.ceil(minutes / 5) * 5))


def _trip_days_left(plan: PlanSnapshot, start: datetime) -> int:
    if plan.end_date is None:
        return 1
    return max(1, (plan.end_date - start.date()).days + 1)


def price(
    mode: TransferMode,
    kind: GapKind,
    km: float | None,
    plan: PlanSnapshot,
    start: datetime,
) -> Money:
    """An estimate for the whole party, in the plan's currency."""
    budget = active_budget(plan.budget)
    currency = budget.currency if budget is not None else "USD"
    people = party_size(plan.travelers)
    amount: Decimal
    if mode == "walk":
        amount = Decimal(0)
    elif mode == "car_rental":
        # For the rest of the trip: the car stays with them.
        amount = Decimal(_CAR_RENTAL_PER_DAY * _trip_days_left(plan, start))
    elif mode in _VEHICLE_FACTOR:
        fare = (
            Decimal(_TYPICAL_FARE[kind])
            if km is None
            else _TAXI_BASE + _TAXI_PER_KM * Decimal(str(round(km, 1)))
        )
        amount = (fare * _VEHICLE_FACTOR[mode]).quantize(Decimal(1))
    else:
        amount = Decimal(_TICKETS.get(mode, {}).get(kind, 10) * people)
    return Money(amount=amount, currency=currency)


def fit(plan: PlanSnapshot, gap: Gap, length: timedelta) -> Ride:
    """When the ride happens within the gap's window (see the module docstring)."""
    after, before = _item(plan, gap.after_item_id), _item(plan, gap.before_item_id)
    leave, arrive = gap.leave_after, gap.arrive_before
    if arrive is None:
        if leave is None:
            raise SearchFailedError("I can't tell when this ride would happen.")
        return Ride(leave, leave + length)
    # When to be there by: a flight wants its passengers early.
    arrive_by = arrive - BEFORE_FLIGHT if isinstance(before, FlightItem) else arrive
    if leave is None:
        return Ride(arrive_by - length, arrive_by)
    # Waiting at a stay is fine; waiting at the airport or after dinner isn't.
    if isinstance(after, LodgingItem) and arrive_by - length >= leave:
        return Ride(arrive_by - length, arrive_by)
    if leave + length <= arrive:
        return Ride(leave, leave + length)
    if arrive - leave >= _MIN_RIDE:
        minutes = int((arrive - leave).total_seconds() // 60)
        return Ride(leave, arrive, f"only {minutes} min to get there")
    return Ride(leave, leave + length, "overlaps what comes next")


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


def _clock(moment: datetime) -> str:
    return f"{moment:%b} {moment.day}, {moment:%H:%M}"


def _place(place: Place) -> dict[str, Any]:
    return place.model_dump(mode="json", exclude_none=True)


def _add_transfer(
    mode: TransferMode, gap: Gap, ride: Ride, cost: Money, title: str
) -> CandidateChange:
    arguments: dict[str, Any] = {
        "title": title,
        "mode": mode,
        "from_place": _place(gap.from_place),
        "to_place": _place(gap.to_place),
        # Local wall-clock times in the gap's zone, where the ride starts.
        "start": ride.start.astimezone(_zone(gap)).strftime("%Y-%m-%dT%H:%M:%S"),
        "end": ride.end.astimezone(_zone(gap)).strftime("%Y-%m-%dT%H:%M:%S"),
        "timezone": gap.timezone,
        "cost": {**cost.model_dump(mode="json"), "basis": "estimate"},
    }
    if mode == "car_rental":
        arguments["pickup_location"] = _place(gap.from_place)
    summary = f"Add transfer “{title}” on {_clock(ride.start.astimezone(_zone(gap)))}"
    return CandidateChange(name="add_transfer", arguments=arguments, summary=summary)


def _zone(gap: Gap) -> ZoneInfo:
    return ZoneInfo(gap.timezone)


def _candidate(
    number: int, mode: TransferMode, kind: GapKind, km: float | None, gap: Gap, plan: PlanSnapshot
) -> Candidate:
    length = duration(mode, kind, km)
    ride = fit(plan, gap, length)
    cost = price(mode, kind, km, plan, ride.start)
    destination = describe_place(gap.to_place, gap.before_item_id)
    title = f"{_LABELS[mode]} to {destination}"[:_MAX_TITLE]
    zone = _zone(gap)
    minutes = int(length.total_seconds() // 60)
    details = [
        f"{_clock(ride.start.astimezone(zone))}-{ride.end.astimezone(zone):%H:%M}",
        f"About {minutes} min",
        "Free" if cost.amount == 0 else f"About {format_money(cost)} (estimate)",
    ]
    if km is not None:
        details.append(f"{km:.1f} km")
    origin = describe_place(gap.from_place, gap.after_item_id)
    return Candidate(
        id=str(number),
        title=title,
        subtitle=f"From {origin}",
        details=details,
        warnings=[ride.warning] if ride.warning else [],
        price=None,  # an estimate, shown in the details
        changes=[_add_transfer(mode, gap, ride, cost, title)],
    )


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def transfers(ctx: SearchContext, plan: PlanSnapshot, action: RunSearch) -> list[Candidate]:
    """The ways across the action's gap, best first."""
    gap = action.gap
    if gap is None:
        raise SearchFailedError("There's no gap to find a way across.")
    kind = gap_kind(plan, gap)
    km = distance_km(gap.from_place, gap.to_place)
    return [
        _candidate(number, mode, kind, km, gap, plan)
        for number, mode in enumerate(modes(kind, km), start=1)
    ]
