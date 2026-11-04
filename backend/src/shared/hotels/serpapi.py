"""`HotelProvider` backed by SerpApi's Google Hotels engine
(https://serpapi.com/google-hotels-api).

Google Hotels gives check-in and check-out as "3:00 PM" and each price both as text and as
a number (`extracted_lowest`); only the numbers are used. A property with no name is left
out.
"""

import logging
from datetime import datetime, time
from decimal import Decimal

import httpx
from pydantic import BaseModel, ValidationError

from shared.domain import Money
from shared.hotels.types import HotelKind, HotelOption, HotelQuery, HotelSearchError
from shared.serpapi import SERPAPI_URL, SerpApiClient, describe_failure, loggable

logger = logging.getLogger(__name__)

__all__ = ["SERPAPI_URL", "SerpApiHotelProvider"]

# SerpApi reports "no hotels" as an error message, not an empty list.
_NO_RESULTS = "hasn't returned any results"
# Google Hotels needs every child's age; we only know how many, so each counts as this.
_CHILD_AGE = "10"
_TIME_FORMATS = ("%I:%M %p", "%I %p", "%H:%M")


# ---------------------------------------------------------------------------
# Response schema (only the fields we use)
# ---------------------------------------------------------------------------


class _Coordinates(BaseModel):
    latitude: float | None = None
    longitude: float | None = None


class _Rate(BaseModel):
    extracted_lowest: float | None = None


class _Property(BaseModel):
    type: str = "hotel"  # "hotel" or "vacation rental"
    name: str = ""
    description: str | None = None
    link: str | None = None
    address: str | None = None
    gps_coordinates: _Coordinates | None = None
    check_in_time: str | None = None
    check_out_time: str | None = None
    rate_per_night: _Rate | None = None
    total_rate: _Rate | None = None
    extracted_hotel_class: int | None = None
    overall_rating: float | None = None
    reviews: int | None = None
    amenities: list[str] = []


class _Response(BaseModel):
    properties: list[_Property] = []
    error: str | None = None


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def parse_time(text: str | None) -> time | None:
    """ "3:00 PM" → 15:00; None when missing or in a format we don't know."""
    if not text:
        return None
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(text.strip().upper(), fmt).time()
        except ValueError:
            continue
    logger.info("Unrecognised hotel time %r", text)
    return None


def _money(rate: _Rate | None, currency: str) -> Money | None:
    if rate is None or rate.extracted_lowest is None:
        return None
    return Money(amount=Decimal(str(rate.extracted_lowest)), currency=currency)


def _option(prop: _Property, currency: str) -> HotelOption | None:
    if not prop.name.strip():
        return None
    kind: HotelKind = "vacation_rental" if "rental" in prop.type.lower() else "hotel"
    coords = prop.gps_coordinates or _Coordinates()
    try:
        return HotelOption(
            name=prop.name.strip(),
            kind=kind,
            description=prop.description,
            address=prop.address,
            lat=coords.latitude,
            lng=coords.longitude,
            check_in_time=parse_time(prop.check_in_time),
            check_out_time=parse_time(prop.check_out_time),
            nightly_rate=_money(prop.rate_per_night, currency),
            total_rate=_money(prop.total_rate, currency),
            stars=prop.extracted_hotel_class,
            rating=prop.overall_rating,
            reviews=prop.reviews,
            amenities=prop.amenities,
            link=prop.link,
        )
    except ValidationError as exc:  # e.g. a rating or class out of range
        logger.warning("Skipping hotel %r: %s", prop.name, exc.errors(include_url=False)[:1])
        return None


def _params(query: HotelQuery) -> dict[str, str]:
    params = {
        "engine": "google_hotels",
        "q": query.location,
        "check_in_date": query.check_in.isoformat(),
        "check_out_date": query.check_out.isoformat(),
        "adults": str(query.adults),
        "currency": query.currency,
        "hl": "en",
        "gl": "us",
    }
    if query.children:
        params["children"] = str(query.children)
        params["children_ages"] = ",".join([_CHILD_AGE] * query.children)
    if query.vacation_rentals:
        params["vacation_rentals"] = "true"
    elif query.stars:
        params["hotel_class"] = ",".join(str(stars) for stars in query.stars)
    if query.max_nightly is not None:
        params["max_price"] = str(query.max_nightly)
    return params


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class SerpApiHotelProvider:
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
            name="hotel search",
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_wait_seconds=retry_wait_seconds,
            client=client,
        )

    async def search(self, query: HotelQuery) -> list[HotelOption]:
        params = _params(query)
        try:
            parsed = _Response.model_validate_json(await self._serpapi.get(params))
        except (httpx.HTTPError, ValidationError) as exc:
            logger.warning(
                "SerpApi hotel search failed for %r, %s to %s: %s",
                query.location,
                query.check_in,
                query.check_out,
                loggable(exc),
            )
            raise HotelSearchError(describe_failure(exc, "Hotel search")) from exc

        if parsed.error is not None:
            if _NO_RESULTS in parsed.error:
                return []
            logger.warning("SerpApi hotel search error: %s", parsed.error)
            raise HotelSearchError(f"Hotel search failed: {parsed.error}")
        options = [_option(prop, query.currency) for prop in parsed.properties]
        return [option for option in options if option is not None]

    async def aclose(self) -> None:
        await self._serpapi.aclose()
