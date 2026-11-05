"""SerpApiPlaceProvider against a mocked SerpApi (httpx.MockTransport), and the readers for
Google Maps' hours and price levels.

The fixture follows SerpApi's documented Google Maps response
(https://serpapi.com/google-maps-api), trimmed to the fields we read: hours with Google's
narrow no-break spaces and en dashes, a split lunch-and-dinner day, a price range instead
of a level, hours that don't read, a place with no name and one with a rating out of range.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import time
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from shared.places.serpapi import SERPAPI_URL, SerpApiPlaceProvider, parse_hours, parse_price
from shared.places.types import OpeningHours, PlaceQuery, PlaceSearchError
from tests.json_path import json_object

Handler = Callable[[httpx.Request], httpx.Response]

API_KEY = "serp-test-key"
FIXTURES = Path(__file__).parent / "fixtures"
QUERY = PlaceQuery(what="museums", near="Lisbon, Portugal")
# Google's separators, by code point: in the source they'd pass for a space and a hyphen.
NB, DASH = chr(0x202F), chr(0x2013)


def _fixture(name: str) -> dict[str, JsonValue]:
    return json_object((FIXTURES / name).read_text())


def _provider(handler: Handler, *, max_attempts: int = 3) -> SerpApiPlaceProvider:
    return SerpApiPlaceProvider(
        api_key=API_KEY,
        max_attempts=max_attempts,
        retry_wait_seconds=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _replying(body: Mapping[str, JsonValue], status: int = 200) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


def _recording(seen: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_fixture("lisbon.json"))

    return handler


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


async def test_a_search_sends_what_and_where() -> None:
    seen: list[httpx.Request] = []

    await _provider(_recording(seen)).search(QUERY)

    (request,) = seen
    assert str(request.url).startswith(SERPAPI_URL)
    assert dict(request.url.params) == {
        "engine": "google_maps",
        "type": "search",
        "q": "museums in Lisbon, Portugal",
        "hl": "en",
        "api_key": API_KEY,
    }


async def test_coordinates_centre_the_map() -> None:
    seen: list[httpx.Request] = []
    query = QUERY.model_copy(update={"lat": 38.72, "lng": -9.14})

    await _provider(_recording(seen)).search(query)

    assert seen[0].url.params["ll"] == "@38.72,-9.14,14z"


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


async def test_places_are_mapped_in_order() -> None:
    museum, taberna, bar = await _provider(_replying(_fixture("lisbon.json"))).search(QUERY)

    assert museum.name == "Museu Calouste Gulbenkian"
    assert (museum.kind, museum.rating, museum.reviews) == ("Art museum", 4.7, 23541)
    assert (museum.lat, museum.lng) == (38.7375, -9.1545)
    assert museum.link == "https://gulbenkian.pt/museu/"
    assert museum.price_level is None
    assert museum.hours is not None
    # Closed on Tuesdays (weekday 1), 10 to 6 on the other six days.
    assert {h.weekday for h in museum.hours} == {0, 2, 3, 4, 5, 6}
    assert museum.hours[0] == OpeningHours(weekday=0, opens=time(10), closes=time(18))
    assert taberna.price_level == 2
    assert taberna.hours is not None
    assert [(h.opens, h.closes) for h in taberna.hours if h.weekday == 0] == [
        (time(12), time(15)),
        (time(18, 30), time(23)),
    ]
    # A price range isn't a level, and hours that don't read count as not given.
    assert (bar.price_level, bar.hours, bar.link) == (None, None, None)


async def test_a_single_match_comes_back_as_one_place() -> None:
    body: dict[str, JsonValue] = {"place_results": {"title": "Oceanário de Lisboa", "rating": 4.6}}

    (place,) = await _provider(_replying(body)).search(QUERY)

    assert place.name == "Oceanário de Lisboa"


async def test_no_places_is_an_empty_list() -> None:
    body = {"error": "Google hasn't returned any results for this query."}

    assert await _provider(_replying(body)).search(QUERY) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"10{NB}AM{DASH}6{NB}PM", [(time(10), time(18))]),
        (f"9:30{NB}AM{DASH}5:30{NB}PM", [(time(9, 30), time(17, 30))]),
        # The opening takes the closing's AM/PM, flipped when it would open after closing.
        (f"11{DASH}2{NB}PM", [(time(11), time(14))]),
        (f"6{DASH}1{NB}AM", [(time(18), time(1))]),
        (f"6{NB}PM{DASH}12{NB}AM", [(time(18), time(0))]),
        (f"12{DASH}3{NB}PM, 7{DASH}11{NB}PM", [(time(12), time(15)), (time(19), time(23))]),
        ("Open 24 hours", [(time(0), time(0))]),
        ("Closed", []),
        ("17:00-23:30", [(time(17), time(23, 30))]),
    ],
)
def test_hours_in_each_format_google_uses(text: str, expected: list[tuple[time, time]]) -> None:
    hours = parse_hours({"friday": text})

    assert hours is not None
    assert [(h.opens, h.closes) for h in hours] == expected
    assert all(h.weekday == 4 for h in hours)


@pytest.mark.parametrize(
    "days", [None, {}, {"monday": "Hours might differ"}, {"someday": "10 AM-6 PM"}]
)
def test_hours_that_dont_read_are_unknown(days: dict[str, str] | None) -> None:
    assert parse_hours(days) is None


@pytest.mark.parametrize(
    ("text", "level"),
    [("$", 1), ("$$$$", 4), ("€€", 2), ("$10-20", None), ("$$$$$", None), (None, None)],
)
def test_price_levels(text: str | None, level: int | None) -> None:
    assert parse_price(text) == level


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_an_api_error_in_the_body_is_reported() -> None:
    with pytest.raises(PlaceSearchError, match="Place search failed: Something broke"):
        await _provider(_replying({"error": "Something broke"})).search(QUERY)


async def test_a_rejected_key_is_reported_as_misconfiguration() -> None:
    handler = _replying({"error": "Invalid API key."}, status=401)

    with pytest.raises(PlaceSearchError, match="Place search is misconfigured"):
        await _provider(handler).search(QUERY)


async def test_server_errors_are_retried() -> None:
    replies = [httpx.Response(503), httpx.Response(200, json=_fixture("lisbon.json"))]

    def handler(_request: httpx.Request) -> httpx.Response:
        return replies.pop(0)

    assert len(await _provider(handler).search(QUERY)) == 3


async def test_failures_never_log_the_api_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(PlaceSearchError, match="Could not reach the place search service"):
        await _provider(handler, max_attempts=2).search(QUERY)
    assert "SerpApi place search failed" in caplog.text
    assert API_KEY not in caplog.text
