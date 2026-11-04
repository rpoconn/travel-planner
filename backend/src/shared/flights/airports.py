"""Airport timezones, from the `airportsdata` package (about 8,000 airports with an IATA code).

Flight search results give times in each airport's local time with no zone attached, so
the zone is looked up here to make them aware.
"""

from dataclasses import dataclass
from functools import cache

import airportsdata


@cache
def _airports() -> dict[str, airportsdata.Airport]:
    # Parsed from a bundled CSV; once per process.
    return airportsdata.load("IATA")


def airport_timezone(iata: str) -> str | None:
    """The IANA timezone of airport `iata` ("NRT" -> "Asia/Tokyo"), or None if unknown."""
    airport = _airports().get(iata.upper())
    return airport["tz"] if airport is not None and airport["tz"] else None


# ---------------------------------------------------------------------------
# Cities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class City:
    """A city whose airports are known by hand: one served by several airports, or that
    shares its name with smaller places elsewhere ("Paris, TX", "Rome, NY")."""

    names: tuple[str, ...]  # casefolded name and aliases
    airports: tuple[str, ...]  # most important first; a search uses the first few
    metro: str | None = None  # the metropolitan-area code fares quote, e.g. TYO


# A flight search needs airport codes, but a trip's origin and destination are usually a
# city ("Lisbon"). Cities listed here are searched at their airports; the rest are looked
# up by city name in the bundled data. The metro codes are also what `itinerary.airports`
# rejects on input and uses to treat a city's airports as one place.
CITIES: tuple[City, ...] = (
    City(("bangkok",), ("BKK", "DMK")),
    City(("beijing",), ("PEK", "PKX"), metro="BJS"),
    City(("berlin",), ("BER",)),
    City(("buenos aires",), ("EZE", "AEP"), metro="BUE"),
    City(("chicago",), ("ORD", "MDW"), metro="CHI"),
    City(("dallas",), ("DFW", "DAL")),
    City(("detroit",), ("DTW",), metro="DTT"),
    City(("houston",), ("IAH", "HOU")),
    City(("istanbul",), ("IST", "SAW")),
    City(("jakarta",), ("CGK", "HLP"), metro="JKT"),
    City(("london",), ("LHR", "LGW", "STN", "LCY", "LTN", "SEN"), metro="LON"),
    City(("los angeles",), ("LAX",)),
    City(("miami",), ("MIA", "FLL")),
    City(("milan",), ("MXP", "LIN", "BGY"), metro="MIL"),
    City(("montreal",), ("YUL",), metro="YMQ"),
    City(("moscow",), ("SVO", "DME", "VKO"), metro="MOW"),
    City(("new york", "new york city"), ("JFK", "EWR", "LGA"), metro="NYC"),
    City(("osaka",), ("KIX", "ITM"), metro="OSA"),
    City(("paris",), ("CDG", "ORY"), metro="PAR"),
    City(("philadelphia",), ("PHL",)),
    City(("rio de janeiro",), ("GIG", "SDU"), metro="RIO"),
    City(("rome",), ("FCO", "CIA"), metro="ROM"),
    City(("san francisco",), ("SFO", "OAK")),
    City(("são paulo", "sao paulo"), ("GRU", "CGH", "VCP"), metro="SAO"),
    City(("seoul",), ("ICN", "GMP"), metro="SEL"),
    City(("shanghai",), ("PVG", "SHA")),
    City(("stockholm",), ("ARN", "BMA"), metro="STO"),
    City(("tokyo",), ("HND", "NRT"), metro="TYO"),
    City(("toronto",), ("YYZ", "YTZ"), metro="YTO"),
    City(("venice",), ("VCE",)),
    City(("washington", "washington dc"), ("IAD", "DCA", "BWI"), metro="WAS"),
)

# A search takes at most this many airports per end (`FlightQuery`).
MAX_AIRPORTS = 4

_CITY_AIRPORTS: dict[str, tuple[str, ...]] = {
    name: city.airports[:MAX_AIRPORTS] for city in CITIES for name in city.names
}


def _by_city(city: str) -> tuple[str, ...]:
    found = [
        code
        for code, airport in _airports().items()
        if airport["city"].casefold() == city.casefold()
    ]
    # Small fields share city names with big airports ("Wings Field", Philadelphia); an
    # international airport, when there is one, is the one people fly from.
    international = [code for code in found if "international" in _airports()[code]["name"].lower()]
    return tuple((international or found)[:MAX_AIRPORTS])


def city_airports(*names: str | None) -> tuple[str, ...]:
    """The airports to search for a place, trying each of its names in turn (e.g. its
    city, then its name): the first that knows any airports wins. Empty when none do."""
    for name in names:
        if not name:
            continue
        key = name.strip().casefold()
        if key in _CITY_AIRPORTS:
            return _CITY_AIRPORTS[key]
        # A name that is itself an airport code ("LIS").
        if len(key) == 3 and key.upper() in _airports():
            return (key.upper(),)
        found = _by_city(key)
        if found:
            return found
    return ()
