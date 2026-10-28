"""Kind-specific query shaping.

Small models tend to send terse queries ("cheap hotels", "flights"); the trip context fills
in what's missing, e.g. "flights" -> "flights from San Francisco to Tokyo May 2027". Anything
the query already mentions is left alone, so a specific query passes through unchanged.
"""

import re
from datetime import date

from shared.domain import Place
from shared.search.types import SearchContext, SearchKind

# A kind's noun is appended only when no word of the query starts with one of these.
_KIND_WORDS: dict[SearchKind, tuple[str, ...]] = {
    "flights": ("flight", "fly", "airfare", "airline"),
    "hotels": ("hotel", "hostel", "stay", "lodging", "accommodation", "ryokan", "resort"),
}
_KIND_NOUN: dict[SearchKind, str] = {"flights": "flights", "hotels": "hotels"}

# Prices depend on the date, so only these kinds get the travel month appended.
_DATED_KINDS: frozenset[SearchKind] = frozenset({"flights", "hotels"})

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip

# An uppercase 3-letter token like "SFO" probably means the model already gave a route.
_IATA_TOKEN = re.compile(r"\b[A-Z]{3}\b")
_YEAR = re.compile(r"\b20\d{2}\b")
_WORD = re.compile(r"[a-z]+")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _label(place: Place) -> str:
    return place.city or place.name


def _mentions(query: str, place: Place) -> bool:
    # Whole words only: "SFO" must not match inside "transform".
    names = (place.name, place.city, place.iata)
    return any(re.search(rf"\b{re.escape(name)}\b", query, re.IGNORECASE) for name in names if name)


def _mentions_date(query: str) -> bool:
    words = set(_WORD.findall(query.lower()))
    return bool(_YEAR.search(query)) or any(month in words for month in _MONTHS)


def _month_year(value: date) -> str:
    # Not strftime("%B"): month names there depend on the process locale.
    return f"{_MONTHS[value.month - 1].title()} {value.year}"


# ---------------------------------------------------------------------------
# Shaping
# ---------------------------------------------------------------------------


def shape_query(query: str, kind: SearchKind, context: SearchContext) -> str:
    query = " ".join(query.split())
    parts = [query]

    kind_words = _KIND_WORDS.get(kind)
    query_words = _WORD.findall(query.lower())
    if kind_words is not None and not any(word.startswith(kind_words) for word in query_words):
        parts.append(_KIND_NOUN[kind])

    if kind == "flights":
        if not _IATA_TOKEN.search(query):
            if context.origin is not None and not _mentions(query, context.origin):
                parts.append(f"from {_label(context.origin)}")
            if context.destination is not None and not _mentions(query, context.destination):
                parts.append(f"to {_label(context.destination)}")
    elif (
        # hotels / activities / transport are all "... in <destination>".
        kind != "general"
        and context.destination is not None
        and not _mentions(query, context.destination)
    ):
        parts.append(f"in {_label(context.destination)}")

    if kind in _DATED_KINDS and context.travel_date is not None and not _mentions_date(query):
        parts.append(_month_year(context.travel_date))

    return " ".join(part for part in parts if part)
