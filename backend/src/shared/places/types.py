"""Provider-neutral place search types: the query, the places found (restaurants, museums,
parks...), the `PlaceProvider` protocol and its error.

Opening hours are local wall-clock times with no zone: a place's hours are in its own
city's zone, which the planner already knows from the trip.
"""

from datetime import time
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


class PlaceQuery(BaseModel):
    """What to look for and where, as typed into a map search: `what` "museums", `near`
    "Lisbon, Portugal". Coordinates, when known, centre the search."""

    model_config = ConfigDict(frozen=True)

    what: str = Field(min_length=1, max_length=100)
    near: str = Field(min_length=1, max_length=300)
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

# Google's "$" to "$$$$".
PriceLevel = Literal[1, 2, 3, 4]


class OpeningHours(BaseModel):
    """One opening on one weekday (0 is Monday). A `closes` at or before `opens` runs past
    midnight; `opens == closes == 00:00` is open all day."""

    model_config = ConfigDict(frozen=True)

    weekday: int = Field(ge=0, le=6)
    opens: time
    closes: time


class PlaceOption(BaseModel):
    """One place a search found."""

    name: str = Field(min_length=1)
    kind: str | None = None  # the provider's type, e.g. "Art museum"
    description: str | None = None
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int | None = Field(default=None, ge=0)
    price_level: PriceLevel | None = None
    # None: the listing doesn't say. A weekday with no entry is closed that day.
    hours: list[OpeningHours] | None = None
    link: str | None = None  # the place's website, else its map page


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class PlaceSearchError(Exception):
    """A search that couldn't be completed. The message is safe to show the user."""


class PlaceProvider(Protocol):
    async def search(self, query: PlaceQuery) -> list[PlaceOption]:
        """The places for `query`, in the provider's order. Raise `PlaceSearchError` on
        failure; nothing found is an empty list."""
        ...

    async def aclose(self) -> None: ...
