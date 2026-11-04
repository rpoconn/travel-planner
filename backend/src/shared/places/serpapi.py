"""`PlaceProvider` backed by SerpApi's Google Maps engine
(https://serpapi.com/google-maps-api).

Google Maps lists a place's price as "$" to "$$$$" and its hours per weekday as text:
"10 AM-6 PM", "11 AM-3 PM, 6-10 PM", "Closed", "Open 24 hours". The hours are read with
`parse_hours`; a listing whose hours don't read cleanly counts as not saying (None), so a
misread never closes a place that is open. A place with no name is left out. A query that
matches one place exactly comes back as `place_results` instead of a list.
"""

import logging
from datetime import datetime, time

import httpx
from pydantic import BaseModel, ValidationError

from shared.places.types import OpeningHours, PlaceOption, PlaceQuery, PlaceSearchError
from shared.serpapi import SERPAPI_URL, SerpApiClient, describe_failure, loggable

logger = logging.getLogger(__name__)

__all__ = ["SERPAPI_URL", "SerpApiPlaceProvider", "parse_hours", "parse_price"]

# SerpApi reports "no places" as an error message, not an empty list.
_NO_RESULTS = "hasn't returned any results"
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_CURRENCY_SIGNS = frozenset("$€£¥₩₹")
_MIDNIGHT = time(0, 0)
# By code point: in the source they would pass for a hyphen and a space.
_EN_DASH, _EM_DASH = chr(0x2013), chr(0x2014)
_NARROW_SPACE, _THIN_SPACE = chr(0x202F), chr(0x2009)
# How far the map is zoomed in around `ll`: a city.
_ZOOM = "14z"


# ---------------------------------------------------------------------------
# Response schema (only the fields we use)
# ---------------------------------------------------------------------------


class _Coordinates(BaseModel):
    latitude: float | None = None
    longitude: float | None = None


class _Place(BaseModel):
    title: str = ""
    type: str | None = None
    description: str | None = None
    address: str | None = None
    gps_coordinates: _Coordinates | None = None
    rating: float | None = None
    reviews: int | None = None
    price: str | None = None
    operating_hours: dict[str, str] | None = None
    website: str | None = None


class _Response(BaseModel):
    local_results: list[_Place] = []
    place_results: _Place | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Hours
# ---------------------------------------------------------------------------


def _clean(text: str) -> str:
    # Google puts a narrow no-break space before AM/PM (and en dashes between times,
    # which `_range` handles).
    return " ".join(text.replace(_NARROW_SPACE, " ").replace(_THIN_SPACE, " ").split()).upper()


def _suffix(text: str) -> str | None:
    for suffix in ("AM", "PM"):
        if text.endswith(suffix):
            return suffix
    return None


def _clock(text: str, suffix: str | None) -> time | None:
    """ "6:30 PM" / "6 PM" / "18:30" (or "6" read with `suffix`) as a time."""
    text = text.strip()
    own = _suffix(text)
    if own is None and suffix is not None:
        text = f"{text} {suffix}"
    formats = ("%I:%M %p", "%I %p", "%I:%M%p", "%I%p") if own or suffix else ("%H:%M", "%H")
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).time()
        except ValueError:
            continue
    return None


def _range(text: str) -> tuple[time, time] | None:
    """ "10 AM-6 PM" → (10:00, 18:00). An opening time without AM/PM takes the closing
    time's, flipped when that would open after closing ("11-2 PM" is 11 AM, "6-1 AM" is
    6 PM: a late opening that runs past midnight)."""
    parts = text.replace(_EN_DASH, "-").replace(_EM_DASH, "-").split("-")
    if len(parts) != 2:
        return None
    open_text, close_text = (part.strip() for part in parts)
    closes = _clock(close_text, None)
    if closes is None:
        return None
    close_suffix = _suffix(close_text)
    if _suffix(open_text) is not None or close_suffix is None:
        opens = _clock(open_text, None)
        return (opens, closes) if opens is not None else None
    opens = _clock(open_text, close_suffix)
    if opens is None:
        return None
    if opens > closes and closes != _MIDNIGHT:
        flipped = _clock(open_text, "AM" if close_suffix == "PM" else "PM")
        if flipped is not None:
            opens = flipped
    return opens, closes


def _day(weekday: int, text: str) -> list[OpeningHours] | None:
    cleaned = _clean(text)
    if cleaned == "CLOSED":
        return []
    if cleaned in ("OPEN 24 HOURS", "24 HOURS"):
        return [OpeningHours(weekday=weekday, opens=_MIDNIGHT, closes=_MIDNIGHT)]
    hours: list[OpeningHours] = []
    for part in cleaned.split(","):
        found = _range(part)
        if found is None:
            return None
        hours.append(OpeningHours(weekday=weekday, opens=found[0], closes=found[1]))
    return hours


def parse_hours(days: dict[str, str] | None) -> list[OpeningHours] | None:
    """A listing's weekly hours, or None when it gives none or one day doesn't read."""
    if not days:
        return None
    hours: list[OpeningHours] = []
    for name, text in days.items():
        key = name.strip().lower()
        if key not in _WEEKDAYS:
            logger.info("Unrecognised weekday %r in place hours", name)
            return None
        found = _day(_WEEKDAYS.index(key), text)
        if found is None:
            logger.info("Unrecognised place hours %r", text)
            return None
        hours.extend(found)
    return hours


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


def parse_price(text: str | None) -> int | None:
    """ "$$" (or "€€") → 2; None for anything else ("$10-20", missing)."""
    if not text:
        return None
    signs = text.strip()
    if 1 <= len(signs) <= 4 and len(set(signs)) == 1 and signs[0] in _CURRENCY_SIGNS:
        return len(signs)
    return None


def _option(place: _Place) -> PlaceOption | None:
    if not place.title.strip():
        return None
    coords = place.gps_coordinates or _Coordinates()
    try:
        return PlaceOption.model_validate(
            {
                "name": place.title.strip(),
                "kind": place.type,
                "description": place.description,
                "address": place.address,
                "lat": coords.latitude,
                "lng": coords.longitude,
                "rating": place.rating,
                "reviews": place.reviews,
                "price_level": parse_price(place.price),
                "hours": parse_hours(place.operating_hours),
                "link": place.website,
            }
        )
    except ValidationError as exc:  # e.g. a rating out of range
        logger.warning("Skipping place %r: %s", place.title, exc.errors(include_url=False)[:1])
        return None


def _params(query: PlaceQuery) -> dict[str, str]:
    params = {
        "engine": "google_maps",
        "type": "search",
        "q": f"{query.what} in {query.near}",
        "hl": "en",
    }
    if query.lat is not None and query.lng is not None:
        params["ll"] = f"@{query.lat},{query.lng},{_ZOOM}"
    return params


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class SerpApiPlaceProvider:
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
            name="place search",
            timeout_seconds=timeout_seconds,
            max_attempts=max_attempts,
            retry_wait_seconds=retry_wait_seconds,
            client=client,
        )

    async def search(self, query: PlaceQuery) -> list[PlaceOption]:
        params = _params(query)
        try:
            parsed = _Response.model_validate_json(await self._serpapi.get(params))
        except (httpx.HTTPError, ValidationError) as exc:
            logger.warning(
                "SerpApi place search failed for %r near %r: %s",
                query.what,
                query.near,
                loggable(exc),
            )
            raise PlaceSearchError(describe_failure(exc, "Place search")) from exc

        if parsed.error is not None:
            if _NO_RESULTS in parsed.error:
                return []
            logger.warning("SerpApi place search error: %s", parsed.error)
            raise PlaceSearchError(f"Place search failed: {parsed.error}")
        places = parsed.local_results or ([parsed.place_results] if parsed.place_results else [])
        options = [_option(place) for place in places]
        return [option for option in options if option is not None]

    async def aclose(self) -> None:
        await self._serpapi.aclose()
