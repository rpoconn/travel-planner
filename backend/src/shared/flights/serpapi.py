"""`FlightProvider` backed by SerpApi's Google Flights engine
(https://serpapi.com/google-flights-api).

Google Flights gives each time as the airport's local wall-clock time ("2027-05-01 21:30")
with no zone; the zone comes from `airports.airport_timezone`. An option with an airport
whose zone isn't known is left out rather than guessed at.
"""

import logging
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, ValidationError

from shared.domain import Money
from shared.flights.airports import airport_timezone
from shared.flights.types import (
    FlightCabin,
    FlightLayover,
    FlightOption,
    FlightQuery,
    FlightSearchError,
    FlightSegment,
    FlightStop,
)
from shared.serpapi import SERPAPI_URL, SerpApiClient, describe_failure, loggable

logger = logging.getLogger(__name__)

__all__ = ["SERPAPI_URL", "SerpApiFlightProvider"]

_TRAVEL_CLASS: dict[FlightCabin, str] = {
    FlightCabin.ECONOMY: "1",
    FlightCabin.PREMIUM: "2",
    FlightCabin.BUSINESS: "3",
    FlightCabin.FIRST: "4",
}
_TIME_FORMAT = "%Y-%m-%d %H:%M"
# SerpApi reports "no flights" as an error message, not an empty list.
_NO_RESULTS = "hasn't returned any results"


# ---------------------------------------------------------------------------
# Response schema (only the fields we use)
# ---------------------------------------------------------------------------


class _Airport(BaseModel):
    id: str
    name: str = ""
    time: str


class _Segment(BaseModel):
    departure_airport: _Airport
    arrival_airport: _Airport
    airline: str = ""
    flight_number: str | None = None
    duration: int
    airplane: str | None = None
    overnight: bool = False


class _Layover(BaseModel):
    id: str
    name: str = ""
    duration: int
    overnight: bool = False


class _Option(BaseModel):
    flights: list[_Segment]
    layovers: list[_Layover] = []
    total_duration: int
    price: int | None = None
    departure_token: str | None = None
    booking_token: str | None = None


class _Response(BaseModel):
    best_flights: list[_Option] = []
    other_flights: list[_Option] = []
    error: str | None = None


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


class _UnknownAirportError(Exception):
    pass


def _stop(airport: _Airport) -> FlightStop:
    zone = airport_timezone(airport.id)
    if zone is None:
        raise _UnknownAirportError(airport.id)
    local = datetime.strptime(airport.time, _TIME_FORMAT)
    return FlightStop(
        iata=airport.id,
        name=airport.name or airport.id,
        time=local.replace(tzinfo=ZoneInfo(zone)),
        timezone=zone,
    )


def _segment(segment: _Segment) -> FlightSegment:
    mapped = FlightSegment(
        departure=_stop(segment.departure_airport),
        arrival=_stop(segment.arrival_airport),
        airline=segment.airline or "Unknown airline",
        flight_number=segment.flight_number,
        duration_minutes=segment.duration,
        aircraft=segment.airplane,
        overnight=segment.overnight,
    )
    # The times and the listed duration are independent; if they disagree, an airport's
    # zone (or the data) is off, and the item's times would be too.
    elapsed = (mapped.arrival.time - mapped.departure.time).total_seconds() / 60
    if abs(elapsed - segment.duration) > 5:
        logger.warning(
            "Flight %s %s->%s: times give %d min, listed as %d min",
            segment.flight_number,
            mapped.departure.iata,
            mapped.arrival.iata,
            elapsed,
            segment.duration,
        )
    return mapped


def _option(option: _Option, currency: str) -> FlightOption | None:
    try:
        segments = [_segment(segment) for segment in option.flights]
    except _UnknownAirportError as exc:
        logger.warning("Skipping a flight option: no timezone for airport %s", exc)
        return None
    except ValueError as exc:  # a time in an unexpected format
        logger.warning("Skipping a flight option: %s", exc)
        return None
    return FlightOption(
        segments=segments,
        layovers=[
            FlightLayover(
                iata=layover.id,
                name=layover.name or layover.id,
                duration_minutes=layover.duration,
                overnight=layover.overnight,
            )
            for layover in option.layovers
        ],
        total_duration_minutes=option.total_duration,
        price=(
            Money(amount=Decimal(option.price), currency=currency)
            if option.price is not None
            else None
        ),
        departure_token=option.departure_token,
        booking_token=option.booking_token,
    )


def _params(query: FlightQuery, departure_token: str | None) -> dict[str, str]:
    params = {
        "engine": "google_flights",
        "departure_id": ",".join(query.origin),
        "arrival_id": ",".join(query.destination),
        "outbound_date": query.depart_date.isoformat(),
        "type": "1" if query.return_date is not None else "2",
        "travel_class": _TRAVEL_CLASS[query.cabin],
        "adults": str(query.adults),
        "currency": query.currency,
        "hl": "en",
        "gl": "us",
    }
    if query.return_date is not None:
        params["return_date"] = query.return_date.isoformat()
    # The flights back are the same search plus the chosen flight out's token.
    if departure_token is not None:
        params["departure_token"] = departure_token
    return params


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class SerpApiFlightProvider:
    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: float = 30.0,
        max_attempts: int = 3,
        retry_wait_seconds: float = 0.5,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._serpapi = SerpApiClient(
            api_key=api_key,
            name="flight search",
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_wait_seconds=retry_wait_seconds,
            client=client,
        )

    async def search(
        self, query: FlightQuery, *, departure_token: str | None = None
    ) -> list[FlightOption]:
        params = _params(query, departure_token)
        try:
            parsed = _Response.model_validate_json(await self._serpapi.get(params))
        except (httpx.HTTPError, ValidationError) as exc:
            logger.warning(
                "SerpApi flight search failed for %s->%s on %s: %s",
                params["departure_id"],
                params["arrival_id"],
                params["outbound_date"],
                loggable(exc),
            )
            raise FlightSearchError(describe_failure(exc, "Flight search")) from exc

        if parsed.error is not None:
            if _NO_RESULTS in parsed.error:
                return []
            logger.warning("SerpApi flight search error: %s", parsed.error)
            raise FlightSearchError(f"Flight search failed: {parsed.error}")
        options = [
            _option(option, query.currency)
            for option in [*parsed.best_flights, *parsed.other_flights]
        ]
        return [option for option in options if option is not None]

    async def aclose(self) -> None:
        await self._serpapi.aclose()
