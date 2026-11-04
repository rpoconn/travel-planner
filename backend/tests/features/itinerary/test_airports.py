"""The airport tables: metro codes and airport names come from one city table."""

from features.itinerary.airports import AIRPORT_NAMES, METRO_AIRPORTS, metro_code_error, same_area
from shared.flights.airports import CITIES


def test_every_named_airport_is_in_the_city_table() -> None:
    listed = {code for city in CITIES for code in city.airports}

    assert set(AIRPORT_NAMES) <= listed


def test_metro_codes_come_from_the_city_table() -> None:
    assert METRO_AIRPORTS["LON"] == ("LHR", "LGW", "STN", "LCY", "LTN", "SEN")
    assert METRO_AIRPORTS["SAO"] == ("GRU", "CGH", "VCP")
    assert "(HND, NRT)" in (metro_code_error("TYO") or "")


def test_airports_of_one_city_are_the_same_area() -> None:
    assert same_area("HND", "NRT")
    assert not same_area("HND", "KIX")
