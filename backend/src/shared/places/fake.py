"""Deterministic `PlaceProvider` for tests and `PLACES_PROVIDER=fake` (offline dev / E2E).

Without scripted replies it makes up obviously fake but well-formed places ("Example ...")
for the query: two per kind of place the activity search looks for (museums,
restaurants...), and two generic ones for anything else, so the search runs with no network
access or API key.
"""

from collections import deque
from collections.abc import Iterable
from datetime import time

from shared.places.types import (
    OpeningHours,
    PlaceOption,
    PlaceQuery,
    PlaceSearchError,
    PriceLevel,
)

FakePlaceReply = list[PlaceOption] | PlaceSearchError

# (name, kind, rating, price level) of two places for each search.
_Canned = tuple[tuple[str, str, float, PriceLevel | None], ...]
_PLACES: dict[str, _Canned] = {
    "museums": (
        ("Example Art Museum", "Art museum", 4.7, None),
        ("Example History Museum", "History museum", 4.4, None),
    ),
    "restaurants": (
        ("Example Bistro", "Restaurant", 4.6, 2),
        ("Example Tasca", "Restaurant", 4.5, 1),
    ),
    "tourist attractions": (
        ("Example Castle", "Castle", 4.6, None),
        ("Example Viewpoint", "Scenic spot", 4.8, None),
    ),
    "parks": (
        ("Example Gardens", "Park", 4.6, None),
        ("Example Riverside Park", "Park", 4.3, None),
    ),
    "bars": (
        ("Example Jazz Bar", "Bar", 4.5, 2),
        ("Example Rooftop", "Cocktail bar", 4.2, 3),
    ),
}

# When each kind of place opens: museums close on Mondays, bars open late.
_DAYS = range(7)
_HOURS: dict[str, list[OpeningHours]] = {
    "museums": [OpeningHours(weekday=d, opens=time(10), closes=time(18)) for d in _DAYS if d],
    "restaurants": [OpeningHours(weekday=d, opens=time(12), closes=time(23)) for d in _DAYS],
    "bars": [OpeningHours(weekday=d, opens=time(18), closes=time(2)) for d in _DAYS],
}


def canned_places(query: PlaceQuery) -> list[PlaceOption]:
    """Two places for the query's kind, or two generic ones named after it."""
    label = query.what.strip().capitalize()
    generic: _Canned = (
        (f"Example {label} One", label, 4.4, None),
        (f"Example {label} Two", label, 4.1, 2),
    )
    found = _PLACES.get(query.what, generic)
    return [
        PlaceOption(
            name=name,
            kind=kind,
            address=f"{index} Example Street, {query.near}",
            rating=rating,
            reviews=250 * index,
            price_level=price,
            hours=_HOURS.get(query.what),
            link=f"https://example.com/places/{index}-{name.lower().replace(' ', '-')}",
        )
        for index, (name, kind, rating, price) in enumerate(found, start=1)
    ]


class FakePlaceProvider:
    def __init__(self, replies: Iterable[FakePlaceReply] = ()) -> None:
        self._replies: deque[FakePlaceReply] = deque(replies)
        self.searches: list[PlaceQuery] = []

    def queue(self, *replies: FakePlaceReply) -> None:
        self._replies.extend(replies)

    async def search(self, query: PlaceQuery) -> list[PlaceOption]:
        self.searches.append(query)
        if not self._replies:
            return canned_places(query)
        reply = self._replies.popleft()
        if isinstance(reply, PlaceSearchError):
            raise reply
        return reply

    async def aclose(self) -> None:
        return None
