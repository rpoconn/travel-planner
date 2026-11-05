"""SerpApiHotelProvider against a mocked SerpApi (httpx.MockTransport).

The fixture follows SerpApi's documented Google Hotels response
(https://serpapi.com/google-hotels-api), trimmed to the fields we read, with a property
that has no price and one with no name.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import JsonValue

from shared.domain import Money
from shared.hotels.serpapi import SERPAPI_URL, SerpApiHotelProvider, parse_time
from shared.hotels.types import HotelQuery, HotelSearchError
from tests.json_path import json_object

Handler = Callable[[httpx.Request], httpx.Response]

API_KEY = "serp-test-key"
FIXTURES = Path(__file__).parent / "fixtures"
QUERY = HotelQuery(
    location="Lisbon, Portugal",
    check_in=date(2027, 5, 1),
    check_out=date(2027, 5, 8),
    adults=2,
)


def _fixture(name: str) -> dict[str, JsonValue]:
    return json_object((FIXTURES / name).read_text())


def _provider(handler: Handler, *, max_attempts: int = 3) -> SerpApiHotelProvider:
    return SerpApiHotelProvider(
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


async def test_a_search_sends_the_stay_and_the_class() -> None:
    seen: list[httpx.Request] = []
    query = QUERY.model_copy(update={"stars": (3, 4), "max_nightly": 180, "currency": "EUR"})

    await _provider(_recording(seen)).search(query)

    (request,) = seen
    assert str(request.url).startswith(SERPAPI_URL)
    assert dict(request.url.params) == {
        "engine": "google_hotels",
        "q": "Lisbon, Portugal",
        "check_in_date": "2027-05-01",
        "check_out_date": "2027-05-08",
        "adults": "2",
        "currency": "EUR",
        "hl": "en",
        "gl": "us",
        "hotel_class": "3,4",
        "max_price": "180",
        "api_key": API_KEY,
    }


async def test_children_and_rentals() -> None:
    seen: list[httpx.Request] = []
    # Stars don't apply to rentals, so they aren't sent.
    query = QUERY.model_copy(update={"children": 2, "vacation_rentals": True, "stars": (4,)})

    await _provider(_recording(seen)).search(query)

    params = dict(seen[0].url.params)
    assert (params["children"], params["children_ages"]) == ("2", "10,10")
    assert params["vacation_rentals"] == "true"
    assert "hotel_class" not in params


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


async def test_properties_are_mapped_in_order() -> None:
    hotel, rental, unpriced = await _provider(_replying(_fixture("lisbon.json"))).search(QUERY)

    assert hotel.name == "Hotel Avenida Palace"
    assert (hotel.kind, hotel.stars, hotel.rating, hotel.reviews) == ("hotel", 5, 4.6, 3120)
    assert (hotel.check_in_time, hotel.check_out_time) == (time(15, 0), time(12, 0))
    assert hotel.nightly_rate == Money(amount=Decimal(214))
    assert hotel.total_rate == Money(amount=Decimal(1498))
    assert (hotel.lat, hotel.lng) == (38.7146, -9.1412)
    assert hotel.link == "https://www.hotel-avenida-palace.pt/"
    assert rental.kind == "vacation_rental" and rental.stars is None
    # No price and an unreadable check-in time are kept as unknown.
    assert (unpriced.nightly_rate, unpriced.total_rate, unpriced.check_in_time) == (
        None,
        None,
        None,
    )


async def test_prices_are_in_the_querys_currency() -> None:
    query = QUERY.model_copy(update={"currency": "EUR"})

    hotel, *_ = await _provider(_replying(_fixture("lisbon.json"))).search(query)

    assert hotel.nightly_rate == Money(amount=Decimal(214), currency="EUR")


def test_times_in_each_format_google_uses() -> None:
    assert parse_time("3:00 PM") == time(15, 0)
    assert parse_time("11 am") == time(11, 0)
    assert parse_time("14:30") == time(14, 30)
    assert parse_time("Anytime") is None
    assert parse_time(None) is None


async def test_no_hotels_is_an_empty_list() -> None:
    body = {"error": "Google Hotels hasn't returned any results for this query."}

    assert await _provider(_replying(body)).search(QUERY) == []


# ---------------------------------------------------------------------------
# Failures
# ---------------------------------------------------------------------------


async def test_an_api_error_in_the_body_is_reported() -> None:
    with pytest.raises(HotelSearchError, match="Hotel search failed: Something broke"):
        await _provider(_replying({"error": "Something broke"})).search(QUERY)


async def test_a_rejected_key_is_reported_as_misconfiguration() -> None:
    handler = _replying({"error": "Invalid API key."}, status=401)

    with pytest.raises(HotelSearchError, match="Hotel search is misconfigured"):
        await _provider(handler).search(QUERY)


async def test_server_errors_are_retried() -> None:
    replies = [httpx.Response(502), httpx.Response(200, json=_fixture("lisbon.json"))]

    def handler(_request: httpx.Request) -> httpx.Response:
        return replies.pop(0)

    assert len(await _provider(handler).search(QUERY)) == 3


async def test_failures_never_log_the_api_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(HotelSearchError, match="Could not reach the hotel search service"):
        await _provider(handler, max_attempts=2).search(QUERY)
    assert "SerpApi hotel search failed" in caplog.text
    assert API_KEY not in caplog.text
