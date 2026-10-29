"""Metropolitan-area IATA codes: city codes that cover several airports.

A flight lands at an airport, not a metro area, but models often send the city code they
see in fare searches ("PHL → TYO"). These are rejected on input with the real airports
listed, and used to treat airports of the same city as one place (HND and NRT are both
Tokyo) when working out a flight's direction.

Models also pair the right airport name with a sibling's code ("Tokyo Haneda Airport",
NRT). The airports of those cities have distinctive names, so a name that names a
different airport than its code is rejected too.
"""

import re

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

# Words that identify an airport by name, for the airports of the cities above. Only names
# that can't mean anything else: never the city name itself ("Tokyo"), which both share.
AIRPORT_NAMES: dict[str, tuple[str, ...]] = {
    "AEP": ("aeroparque",),
    "ARN": ("arlanda",),
    "BGY": ("bergamo", "orio al serio"),
    "BMA": ("bromma",),
    "CDG": ("de gaulle", "roissy"),
    "CGH": ("congonhas",),
    "CGK": ("soekarno",),
    "CIA": ("ciampino",),
    "DCA": ("reagan",),
    "DME": ("domodedovo",),
    "EWR": ("newark",),
    "EZE": ("ezeiza",),
    "FCO": ("fiumicino", "da vinci"),
    "GIG": ("galeao", "galeão"),
    "GMP": ("gimpo",),
    "GRU": ("guarulhos",),
    "HLP": ("halim",),
    "HND": ("haneda",),
    "IAD": ("dulles",),
    "ICN": ("incheon",),
    "ITM": ("itami",),
    "JFK": ("kennedy",),
    "KIX": ("kansai",),
    "LCY": ("london city",),
    "LGA": ("laguardia", "la guardia"),
    "LGW": ("gatwick",),
    "LHR": ("heathrow",),
    "LIN": ("linate",),
    "LTN": ("luton",),
    "MDW": ("midway",),
    "MXP": ("malpensa",),
    "NRT": ("narita",),
    "ORD": ("o'hare", "ohare"),
    "ORY": ("orly",),
    "PKX": ("daxing",),
    "SDU": ("santos dumont",),
    "SEN": ("southend",),
    "STN": ("stansted",),
    "SVO": ("sheremetyevo",),
    "VCP": ("viracopos",),
    "VKO": ("vnukovo",),
    "YTZ": ("billy bishop",),
    "YUL": ("trudeau",),
    "YYZ": ("pearson",),
}

# Airport code → the metro code it belongs to.
_METRO_OF: dict[str, str] = {
    airport: metro for metro, airports in METRO_AIRPORTS.items() for airport in airports
}

# Whole-word patterns, so "orly" doesn't match inside "poorly".
_NAME_PATTERNS: dict[str, re.Pattern[str]] = {
    code: re.compile(r"\b(?:" + "|".join(map(re.escape, words)) + r")\b")
    for code, words in AIRPORT_NAMES.items()
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


def name_code_error(name: str, iata: str) -> str | None:
    """An explanation when `name` names a different airport than `iata`, else None."""
    folded = name.casefold()
    named = [code for code, pattern in _NAME_PATTERNS.items() if pattern.search(folded)]
    # No recognisable name, or one that agrees with the code (or names both, ambiguously).
    if not named or iata in named:
        return None
    message = f"{name!r} is {' or '.join(named)}, not {iata}"
    words = AIRPORT_NAMES.get(iata)
    if words is not None:
        message += f" ({iata} is {words[0].title()})"
    return f"{message}; send the code that matches the airport's name"
