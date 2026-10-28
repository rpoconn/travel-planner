from datetime import date

import pytest

from shared.domain import Place
from shared.search.query import shape_query
from shared.search.types import SearchContext, SearchKind

TRIP = SearchContext(
    origin=Place(name="San Francisco International Airport", city="San Francisco", iata="SFO"),
    destination=Place(name="Tokyo", country="Japan"),
    travel_date=date(2027, 5, 1),
)


@pytest.mark.parametrize(
    ("query", "kind", "expected"),
    [
        # Terse queries are filled in from the trip.
        ("cheap", "flights", "cheap flights from San Francisco to Tokyo May 2027"),
        ("cheap hotels", "hotels", "cheap hotels in Tokyo May 2027"),
        ("ryokan with onsen", "hotels", "ryokan with onsen in Tokyo May 2027"),
        ("sushi breakfast", "activities", "sushi breakfast in Tokyo"),
        ("airport to Shinjuku train", "transport", "airport to Shinjuku train in Tokyo"),
        # Whatever the query already says is kept as is.
        ("nonstop flights SFO to NRT", "flights", "nonstop flights SFO to NRT May 2027"),
        ("flights to Tokyo in June", "flights", "flights to Tokyo in June from San Francisco"),
        ("Tokyo hotels 2028", "hotels", "Tokyo hotels 2028"),
        ("Hotels  near   tokyo station", "hotels", "Hotels near tokyo station May 2027"),
        # "general" passes through untouched.
        ("Japan visa rules for US citizens", "general", "Japan visa rules for US citizens"),
    ],
)
def test_shape_query_with_trip_context(query: str, kind: SearchKind, expected: str) -> None:
    assert shape_query(query, kind, TRIP) == expected


def test_places_match_whole_words_only() -> None:
    # "sfo" appears inside "transform", but that's not a mention of SFO.
    context = SearchContext(origin=Place(name="SFO", iata="SFO"))

    assert shape_query("transform flights", "flights", context) == "transform flights from SFO"


def test_shape_query_without_context_only_adds_the_kind_noun() -> None:
    empty = SearchContext()

    assert shape_query("cheap", "flights", empty) == "cheap flights"
    assert shape_query("sushi", "activities", empty) == "sushi"


def test_city_is_preferred_over_place_name() -> None:
    context = SearchContext(destination=Place(name="Hotel Okura", city="Tokyo"))

    assert shape_query("dinner", "activities", context) == "dinner in Tokyo"
