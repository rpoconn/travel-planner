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
