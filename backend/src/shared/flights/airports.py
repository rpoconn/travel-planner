"""Airport timezones, from the `airportsdata` package (about 8,000 airports with an IATA code).

Flight search results give times in each airport's local time with no zone attached, so
the zone is looked up here to make them aware.
"""

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
# City → airports
# ---------------------------------------------------------------------------

# A flight search needs airport codes, but a trip's origin and destination are usually a
# city ("Lisbon"). Cities served by several airports, or that share a name with smaller
# places elsewhere ("Paris, TX", "Rome, NY"), are listed here so the search covers the
# right ones; the rest are looked up by city name in the bundled data.
_CITY_AIRPORTS: dict[str, tuple[str, ...]] = {
    "bangkok": ("BKK", "DMK"),
    "beijing": ("PEK", "PKX"),
    "berlin": ("BER",),
    "buenos aires": ("EZE", "AEP"),
    "chicago": ("ORD", "MDW"),
    "dallas": ("DFW", "DAL"),
    "houston": ("IAH", "HOU"),
    "istanbul": ("IST", "SAW"),
    "london": ("LHR", "LGW", "STN", "LCY"),
    "los angeles": ("LAX",),
    "miami": ("MIA", "FLL"),
    "milan": ("MXP", "LIN", "BGY"),
    "montreal": ("YUL",),
    "moscow": ("SVO", "DME", "VKO"),
    "new york": ("JFK", "EWR", "LGA"),
    "new york city": ("JFK", "EWR", "LGA"),
    "osaka": ("KIX", "ITM"),
    "paris": ("CDG", "ORY"),
    "philadelphia": ("PHL",),
    "rio de janeiro": ("GIG", "SDU"),
    "rome": ("FCO", "CIA"),
    "san francisco": ("SFO", "OAK"),
    "sao paulo": ("GRU", "CGH"),
    "são paulo": ("GRU", "CGH"),
    "seoul": ("ICN", "GMP"),
    "shanghai": ("PVG", "SHA"),
    "stockholm": ("ARN", "BMA"),
    "tokyo": ("HND", "NRT"),
    "toronto": ("YYZ", "YTZ"),
    "venice": ("VCE",),
    "washington": ("IAD", "DCA", "BWI"),
    "washington dc": ("IAD", "DCA", "BWI"),
}

# A search takes at most this many airports per end (`FlightQuery`).
MAX_AIRPORTS = 4


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
