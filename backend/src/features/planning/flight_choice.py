"""Flight search results as the planner uses them: ranked by total travel time,
then price, described for a candidate card, and turned into `add_flight` arguments with
the provider's own airport-local times (so nothing here does timezone arithmetic).
"""

from collections.abc import Iterable
from decimal import Decimal

from pydantic import JsonValue

from shared.domain import Money
from shared.flights.types import FlightCabin, FlightOption, FlightStop

# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


def _rank_key(option: FlightOption) -> tuple[int, bool, Decimal]:
    # An option without a price sorts after the priced ones of the same length.
    price = option.price.amount if option.price is not None else Decimal(0)
    return option.total_duration_minutes, option.price is None, price


def rank_flights(options: Iterable[FlightOption]) -> list[FlightOption]:
    """`options` shortest first, then cheapest."""
    return sorted(options, key=_rank_key)


# ---------------------------------------------------------------------------
# Describing
# ---------------------------------------------------------------------------


def format_minutes(minutes: int) -> str:
    """'13h 5m', '45m'."""
    hours, rest = divmod(minutes, 60)
    if hours == 0:
        return f"{rest}m"
    return f"{hours}h" if rest == 0 else f"{hours}h {rest}m"


def stops_label(option: FlightOption) -> str:
    if not option.layovers:
        return "nonstop"
    where = ", ".join(
        f"{layover.iata} {format_minutes(layover.duration_minutes)}" for layover in option.layovers
    )
    count = len(option.layovers)
    return f"{count} stop{'s' if count > 1 else ''} ({where})"


def local_time(stop: FlightStop) -> str:
    # `day` rather than `%-d`, which Windows' strftime doesn't support.
    return f"{stop.iata} {stop.time:%a %b} {stop.time.day} {stop.time:%H:%M}"


# ---------------------------------------------------------------------------
# add_flight arguments
# ---------------------------------------------------------------------------


def flight_arguments(
    option: FlightOption, cabin: FlightCabin, cost: Money | None
) -> dict[str, JsonValue]:
    """`add_flight` arguments for `option` (no direction: the caller sets it). A
    connection is one flight item from the first takeoff to the last landing."""
    depart, arrive = option.departure, option.arrival
    numbers = [segment.flight_number for segment in option.segments if segment.flight_number]
    arguments: dict[str, JsonValue] = {
        "carrier": " / ".join(option.airlines),
        "from_place": {"name": depart.name, "iata": depart.iata},
        "to_place": {"name": arrive.name, "iata": arrive.iata},
        # Aware times in each airport's own zone, so no timezone arithmetic happens here.
        "start": depart.time.isoformat(),
        "timezone": depart.timezone,
        "end": arrive.time.isoformat(),
        "end_timezone": arrive.timezone,
        "cabin": cabin,
        "layovers": [{"name": stop.name, "iata": stop.iata} for stop in option.layovers],
    }
    if numbers:
        arguments["flight_number"] = ", ".join(numbers)
    if option.layovers:
        arguments["notes"] = f"Flight time {format_minutes(option.total_duration_minutes)}, " + (
            stops_label(option)
        )
    if cost is not None:
        # A price from the search itself, not a guess.
        arguments["cost"] = {**cost.model_dump(mode="json"), "basis": "exact"}
    return arguments
