"""Flight provider construction, the fake provider, airport timezones and the query."""

from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from shared.config import Settings
from shared.flights.airports import airport_timezone
from shared.flights.fake import FakeFlightProvider
from shared.flights.provider import UnavailableFlightProvider, create_flight_provider
from shared.flights.serpapi import SerpApiFlightProvider
from shared.flights.types import FlightQuery, FlightSearchError

QUERY = FlightQuery(
    origin=("PHL",),
    destination=("HND",),
    depart_date=date(2027, 5, 1),
    return_date=date(2027, 5, 8),
)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_fake_provider_is_selected(settings: Settings) -> None:
    assert isinstance(create_flight_provider(settings), FakeFlightProvider)


async def test_serpapi_without_a_key_is_unavailable(settings: Settings) -> None:
    configured = settings.model_copy(update={"flight_provider": "serpapi", "serpapi_api_key": None})

    provider = create_flight_provider(configured)

    assert isinstance(provider, UnavailableFlightProvider)
    with pytest.raises(FlightSearchError, match="SERPAPI_API_KEY"):
        await provider.search(QUERY)


async def test_serpapi_with_a_key(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"flight_provider": "serpapi", "serpapi_api_key": "serp-test"}
    )

    provider = create_flight_provider(configured)

    assert isinstance(provider, SerpApiFlightProvider)
    await provider.aclose()


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


async def test_fake_round_trip_lists_flights_out_then_one_back() -> None:
    provider = FakeFlightProvider()

    out = await provider.search(QUERY)
    (back,) = await provider.search(QUERY, departure_token=out[1].departure_token)

    assert [option.departure_token for option in out] == ["fake-departure-0", "fake-departure-1"]
    assert {(o.departure.iata, o.arrival.iata) for o in out} == {("PHL", "HND")}
    assert (back.departure.iata, back.arrival.iata) == ("HND", "PHL")
    assert back.departure.time.date() == date(2027, 5, 8)
    # Priced as the whole trip, like the flight out it was chosen with.
    assert back.price == out[1].price
    assert [s.departure_token for s in provider.searches] == [None, "fake-departure-1"]


async def test_fake_flights_are_in_their_airports_zones() -> None:
    (flight, _) = await FakeFlightProvider().search(QUERY.model_copy(update={"adults": 2}))

    assert flight.departure.time.utcoffset() == timedelta(hours=-4)  # EDT
    assert flight.arrival.time.utcoffset() == timedelta(hours=9)  # JST
    assert flight.arrival.time - flight.departure.time == timedelta(hours=13)
    assert flight.price is not None and flight.price.amount == 1800


async def test_fake_one_way_has_no_tokens() -> None:
    out = await FakeFlightProvider().search(QUERY.model_copy(update={"return_date": None}))

    assert [option.departure_token for option in out] == [None, None]


async def test_fake_replies_are_scripted_in_order() -> None:
    provider = FakeFlightProvider([[], FlightSearchError("Flight search timed out.")])

    assert await provider.search(QUERY) == []
    with pytest.raises(FlightSearchError, match="timed out"):
        await provider.search(QUERY)


# ---------------------------------------------------------------------------
# Airports and query
# ---------------------------------------------------------------------------


def test_airport_timezones() -> None:
    assert airport_timezone("NRT") == "Asia/Tokyo"
    assert airport_timezone("phl") == "America/New_York"
    assert airport_timezone("ZZZ") is None


def test_a_query_cant_return_before_it_leaves() -> None:
    with pytest.raises(ValidationError, match="return_date must be on or after depart_date"):
        FlightQuery.model_validate({**QUERY.model_dump(), "return_date": date(2027, 4, 30)})


def test_a_query_needs_airport_codes() -> None:
    with pytest.raises(ValidationError):
        FlightQuery.model_validate({**QUERY.model_dump(), "origin": ("Philadelphia",)})
