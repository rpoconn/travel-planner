"""SerpApiFlightProvider against a mocked SerpApi (httpx.MockTransport).

The fixtures are real Google Flights responses (PHL -> Tokyo, May 2027), trimmed to a few
options and with their tokens replaced.
"""

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
from pydantic import JsonValue

from shared.domain import Money
from shared.flights.serpapi import SERPAPI_URL, SerpApiFlightProvider
from shared.flights.types import FlightQuery, FlightSearchError
from tests.json_path import json_object, obj

Handler = Callable[[httpx.Request], httpx.Response]

API_KEY = "serp-test-key"
FIXTURES = Path(__file__).parent / "fixtures"
ROUND_TRIP = FlightQuery(
    origin=("PHL",),
    destination=("HND", "NRT"),
    depart_date=date(2027, 5, 1),
    return_date=date(2027, 5, 8),
)


def _fixture(name: str) -> dict[str, JsonValue]:
    return json_object((FIXTURES / name).read_text())


def _provider(handler: Handler, *, max_attempts: int = 3) -> SerpApiFlightProvider:
    return SerpApiFlightProvider(
        api_key=API_KEY,
        max_attempts=max_attempts,
        retry_wait_seconds=0,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _replying(body: Mapping[str, JsonValue], status: int = 200) -> Handler:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


async def test_a_round_trip_search_sends_both_dates() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_fixture("outbound.json"))

    query = ROUND_TRIP.model_copy(update={"cabin": "business", "adults": 2})
    await _provider(handler).search(query)

    (request,) = seen
    assert str(request.url).startswith(SERPAPI_URL)
    assert dict(request.url.params) == {
        "engine": "google_flights",
        "departure_id": "PHL",
        "arrival_id": "HND,NRT",
        "outbound_date": "2027-05-01",
        "return_date": "2027-05-08",
        "type": "1",
        "travel_class": "3",
        "adults": "2",
        "currency": "USD",
        "hl": "en",
        "gl": "us",
        "api_key": API_KEY,
    }


async def test_a_one_way_search_has_no_return_date() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_fixture("outbound.json"))

    await _provider(handler).search(ROUND_TRIP.model_copy(update={"return_date": None}))

    (request,) = seen
    assert request.url.params["type"] == "2"
    assert "return_date" not in request.url.params


async def test_the_flights_back_are_the_same_search_with_the_chosen_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_fixture("return.json"))

    (back,) = await _provider(handler).search(ROUND_TRIP, departure_token="departure-token-1")

    (request,) = seen
    assert request.url.params["departure_token"] == "departure-token-1"
    assert request.url.params["return_date"] == "2027-05-08"
    assert [s.flight_number for s in back.segments] == ["QR 807", "QR 727"]
    assert back.booking_token == "booking-token-1"
    assert back.price == Money(amount=Decimal(1579), currency="USD")


# ---------------------------------------------------------------------------
# Mapping
# ---------------------------------------------------------------------------


async def test_options_are_mapped_best_first_with_local_times() -> None:
    options = await _provider(_replying(_fixture("outbound.json"))).search(ROUND_TRIP)

    qatar, china_airlines = options
    assert [s.flight_number for s in qatar.segments] == ["QR 728", "QR 808"]
    assert qatar.departure.iata == "PHL"
    assert qatar.departure.time == datetime(2027, 5, 1, 21, 30, tzinfo=ZoneInfo("America/New_York"))
    assert qatar.arrival.iata == "NRT"
    assert qatar.arrival.time == datetime(2027, 5, 3, 12, 55, tzinfo=ZoneInfo("Asia/Tokyo"))
    assert qatar.arrival.timezone == "Asia/Tokyo"
    # Aware times in their own zones still subtract to the real time in the air.
    elapsed = qatar.arrival.time - qatar.departure.time
    assert elapsed.total_seconds() / 60 == qatar.total_duration_minutes == 1585
    assert [(stop.iata, stop.duration_minutes) for stop in qatar.layovers] == [("DOH", 215)]
    assert qatar.price == Money(amount=Decimal(1579), currency="USD")
    assert qatar.departure_token == "departure-token-1"
    assert qatar.airlines == ["Qatar Airways"]
    # Google's own names, as listed.
    assert china_airlines.airlines == ["Alaska", "China Airlines"]


async def test_an_option_through_an_unknown_airport_is_left_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = _fixture("outbound.json")
    obj(body, "best_flights", 0, "flights", 0, "arrival_airport")["id"] = "ZZZ"

    options = await _provider(_replying(body)).search(ROUND_TRIP)

    assert [option.price for option in options] == [Money(amount=Decimal(1487))]
    assert "no timezone for airport ZZZ" in caplog.text


async def test_no_flights_is_an_empty_list() -> None:
    body = {"error": "Google Flights hasn't returned any results for this query."}

    assert await _provider(_replying(body)).search(ROUND_TRIP) == []


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


async def test_an_api_error_in_the_body_is_reported() -> None:
    with pytest.raises(FlightSearchError, match="Flight search failed: Something broke"):
        await _provider(_replying({"error": "Something broke"})).search(ROUND_TRIP)


async def test_a_rejected_key_is_reported_as_misconfiguration() -> None:
    handler = _replying({"error": "Invalid API key."}, status=401)

    with pytest.raises(FlightSearchError, match="SerpApi key was rejected"):
        await _provider(handler).search(ROUND_TRIP)


async def test_a_bad_request_reports_serpapis_message() -> None:
    handler = _replying({"error": "Unsupported `ZZZ` departure_id."}, status=400)

    with pytest.raises(FlightSearchError, match="Unsupported `ZZZ` departure_id"):
        await _provider(handler).search(ROUND_TRIP)


async def test_a_used_up_allowance_is_not_retried() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={"error": "Your account has run out of searches."})

    with pytest.raises(FlightSearchError, match="allowance is used up"):
        await _provider(handler).search(ROUND_TRIP)
    assert calls == 1


async def test_server_errors_are_retried() -> None:
    replies = [httpx.Response(503), httpx.Response(200, json=_fixture("outbound.json"))]

    def handler(_request: httpx.Request) -> httpx.Response:
        return replies.pop(0)

    assert len(await _provider(handler).search(ROUND_TRIP)) == 2


async def test_requests_never_log_the_api_key(caplog: pytest.LogCaptureFixture) -> None:
    # httpx logs each request's URL, key included, unless it's redacted.
    caplog.set_level(logging.DEBUG)
    replies = [httpx.Response(503), httpx.Response(200, json=_fixture("outbound.json"))]

    def handler(_request: httpx.Request) -> httpx.Response:
        return replies.pop(0)

    await _provider(handler).search(ROUND_TRIP)

    assert "api_key=[redacted]" in caplog.text
    assert API_KEY not in caplog.text


async def test_failures_never_log_the_api_key(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("unreachable", request=request)

    with pytest.raises(FlightSearchError, match="Could not reach"):
        await _provider(handler, max_attempts=2).search(ROUND_TRIP)
    assert "SerpApi flight search failed" in caplog.text
    assert API_KEY not in caplog.text
