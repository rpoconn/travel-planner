"""Deterministic `FlightProvider` for tests and `FLIGHT_PROVIDER=fake` (offline dev / E2E).

Without scripted replies it makes up obviously fake but well-formed nonstop flights for
the query ("Example Air"), so the flights stage can be exercised without network access or
an API key.
"""

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from shared.domain import Money
from shared.flights.airports import airport_timezone
from shared.flights.types import (
    FlightOption,
    FlightQuery,
    FlightSearchError,
    FlightSegment,
    FlightStop,
)

FakeFlightReply = list[FlightOption] | FlightSearchError

_FAKE_DEPARTURE_PREFIX = "fake-departure-"
# (departure time, minutes in the air, price per adult) of each made-up option.
_CANNED = ((time(9, 0), 13 * 60, 900), (time(18, 30), 14 * 60, 1200))


@dataclass(frozen=True)
class FakeFlightSearch:
    query: FlightQuery
    departure_token: str | None


def _stop(iata: str, at: datetime) -> FlightStop:
    zone = airport_timezone(iata) or "UTC"
    local = at.astimezone(ZoneInfo(zone))
    return FlightStop(iata=iata, name=f"{iata} Airport", time=local, timezone=zone)


def _flight(origin: str, destination: str, day: date, n: int, query: FlightQuery) -> FlightOption:
    departs_at, minutes, price = _CANNED[n % len(_CANNED)]
    zone = ZoneInfo(airport_timezone(origin) or "UTC")
    departs = datetime.combine(day, departs_at, tzinfo=zone)
    arrives = departs + timedelta(minutes=minutes)
    return FlightOption(
        segments=[
            FlightSegment(
                departure=_stop(origin, departs),
                arrival=_stop(destination, arrives),
                airline="Example Air",
                flight_number=f"EX {101 + n}",
                duration_minutes=minutes,
            )
        ],
        total_duration_minutes=minutes,
        price=Money(amount=Decimal(price * query.adults), currency=query.currency),
    )


def canned_flights(query: FlightQuery, departure_token: str | None) -> list[FlightOption]:
    """Two nonstop flights out, or (with a token from one of them) one flight back."""
    origin, destination = query.origin[0], query.destination[0]
    if departure_token is not None:
        if query.return_date is None:
            return []
        n = int(departure_token.removeprefix(_FAKE_DEPARTURE_PREFIX) or 0)
        back = _flight(destination, origin, query.return_date, n, query)
        # Priced as the whole round trip, as a real round-trip search is.
        return [back.model_copy(update={"booking_token": f"fake-booking-{n}"})]
    options = [_flight(origin, destination, query.depart_date, n, query) for n in range(2)]
    if query.return_date is None:
        return options
    return [
        option.model_copy(update={"departure_token": f"{_FAKE_DEPARTURE_PREFIX}{n}"})
        for n, option in enumerate(options)
    ]


class FakeFlightProvider:
    def __init__(self, replies: Iterable[FakeFlightReply] = ()) -> None:
        self._replies: deque[FakeFlightReply] = deque(replies)
        self.searches: list[FakeFlightSearch] = []

    def queue(self, *replies: FakeFlightReply) -> None:
        self._replies.extend(replies)

    async def search(
        self, query: FlightQuery, *, departure_token: str | None = None
    ) -> list[FlightOption]:
        self.searches.append(FakeFlightSearch(query, departure_token))
        if not self._replies:
            return canned_flights(query, departure_token)
        reply = self._replies.popleft()
        if isinstance(reply, FlightSearchError):
            raise reply
        return reply

    async def aclose(self) -> None:
        return None
