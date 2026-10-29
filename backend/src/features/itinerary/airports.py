"""Metropolitan-area IATA codes: city codes that cover several airports.

A flight lands at an airport, not a metro area, but models often send the city code they
see in fare searches ("PHL → TYO"). These are rejected on input with the real airports
listed, and used to treat airports of the same city as one place (HND and NRT are both
Tokyo) when working out a flight's direction.
"""

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

METRO_AIRPORTS: dict[str, tuple[str, ...]] = {
    "BJS": ("PEK", "PKX"),
    "BUE": ("EZE", "AEP"),
    "CHI": ("ORD", "MDW"),
    "DTT": ("DTW",),
    "JKT": ("CGK", "HLP"),
    "LON": ("LHR", "LGW", "STN", "LTN", "LCY", "SEN"),
    "MIL": ("MXP", "LIN", "BGY"),
    "MOW": ("SVO", "DME", "VKO"),
    "NYC": ("JFK", "EWR", "LGA"),
    "OSA": ("KIX", "ITM"),
    "PAR": ("CDG", "ORY"),
    "RIO": ("GIG", "SDU"),
    "ROM": ("FCO", "CIA"),
    "SAO": ("GRU", "CGH", "VCP"),
    "SEL": ("ICN", "GMP"),
    "STO": ("ARN", "BMA"),
    "TYO": ("HND", "NRT"),
    "WAS": ("IAD", "DCA", "BWI"),
    "YMQ": ("YUL",),
    "YTO": ("YYZ", "YTZ"),
}

# Airport code → the metro code it belongs to.
_METRO_OF: dict[str, str] = {
    airport: metro for metro, airports in METRO_AIRPORTS.items() for airport in airports
}


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


def metro_code_error(iata: str) -> str | None:
    """An explanation when `iata` is a metro code rather than an airport, else None."""
    airports = METRO_AIRPORTS.get(iata)
    if airports is None:
        return None
    return (
        f"{iata} is a city code, not an airport; use the airport the flight actually uses "
        f"({', '.join(airports)})"
    )


def same_area(a: str, b: str) -> bool:
    """True for the same airport, or two airports serving the same city (HND / NRT)."""
    return a == b or _METRO_OF.get(a, a) == _METRO_OF.get(b, b)
