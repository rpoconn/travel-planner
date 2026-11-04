"""Builders for flight search results in tests."""

from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from shared.domain import Money
from shared.flights.airports import airport_timezone
from shared.flights.types import FlightLayover, FlightOption, FlightSegment, FlightStop


def flight_option(
    origin: str,
    destination: str,
    departs: str,
    minutes: int,
    price: int | None,
    *,
    airline: str = "ANA",
    via: str | None = None,
    departure_token: str | None = None,
) -> FlightOption:
    """A search result leaving `origin` at local time `departs` ("2027-05-01T15:00") and
    landing `minutes` later at `destination`, in each airport's own zone; with `via`, one
    stop there (half the time each way, no layover time, to keep the arithmetic simple)."""
    start = datetime.fromisoformat(departs).replace(tzinfo=_zone(origin))
    end = start + timedelta(minutes=minutes)
    hops = [origin, via, destination] if via else [origin, destination]
    times = [start, start + timedelta(minutes=minutes // 2), end] if via else [start, end]
    segments = [
        FlightSegment(
            departure=_stop(hops[i], times[i]),
            arrival=_stop(hops[i + 1], times[i + 1]),
            airline=airline,
            flight_number=f"NH {100 + i}",
            duration_minutes=int((times[i + 1] - times[i]).total_seconds() // 60),
        )
        for i in range(len(hops) - 1)
    ]
    return FlightOption(
        segments=segments,
        layovers=[FlightLayover(iata=via, name=f"{via} Airport", duration_minutes=0)]
        if via
        else [],
        total_duration_minutes=minutes,
        price=Money(amount=Decimal(price), currency="USD") if price is not None else None,
        departure_token=departure_token,
    )


def _zone(iata: str) -> ZoneInfo:
    zone = airport_timezone(iata)
    assert zone is not None, iata
    return ZoneInfo(zone)


def _stop(iata: str, at: datetime) -> FlightStop:
    zone = _zone(iata)
    return FlightStop(
        iata=iata, name=f"{iata} Airport", time=at.astimezone(zone), timezone=str(zone)
    )
