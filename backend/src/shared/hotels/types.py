"""Provider-neutral hotel search types: the query, the places to stay found, the
`HotelProvider` protocol and its error.

Check-in and check-out times are local wall-clock times with no zone: a hotel's times are
in its own city's zone, which the planner already knows from the trip.
"""

from datetime import date, time
from typing import Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.domain import Money

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

# Hotel star classes a search may be limited to.
HotelStars = Literal[2, 3, 4, 5]


class HotelQuery(BaseModel):
    """Where and when to stay. `location` is free text, as typed into a hotel search
    ("hostels in Alfama, Lisbon, Portugal")."""

    model_config = ConfigDict(frozen=True)

    location: str = Field(min_length=1, max_length=300)
    check_in: date
    check_out: date
    adults: int = Field(default=1, ge=1, le=20)
    children: int = Field(default=0, ge=0, le=20)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    # Empty: any class. Ignored for vacation rentals, which have none.
    stars: tuple[HotelStars, ...] = ()
    # The most one night may cost, in `currency`.
    max_nightly: int | None = Field(default=None, gt=0)
    # Search vacation rentals (apartments, houses) instead of hotels.
    vacation_rentals: bool = False

    @model_validator(mode="after")
    def _at_least_a_night(self) -> Self:
        if self.check_out <= self.check_in:
            raise ValueError("check_out must be after check_in")
        return self

    @property
    def nights(self) -> int:
        return (self.check_out - self.check_in).days


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

HotelKind = Literal["hotel", "vacation_rental"]


class HotelOption(BaseModel):
    """One place to stay for the whole query."""

    name: str = Field(min_length=1)
    kind: HotelKind = "hotel"
    description: str | None = None
    address: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lng: float | None = Field(default=None, ge=-180, le=180)
    check_in_time: time | None = None  # local; None when the listing doesn't say
    check_out_time: time | None = None
    # For the whole party; the total covers every night of the query.
    nightly_rate: Money | None = None
    total_rate: Money | None = None
    stars: int | None = Field(default=None, ge=1, le=5)
    rating: float | None = Field(default=None, ge=0, le=5)
    reviews: int | None = Field(default=None, ge=0)
    amenities: list[str] = Field(default_factory=list)
    link: str | None = None  # the property's or a booking page


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class HotelSearchError(Exception):
    """A search that couldn't be completed. The message is safe to show the user."""


class HotelProvider(Protocol):
    async def search(self, query: HotelQuery) -> list[HotelOption]:
        """The places to stay for `query`, in the provider's order. Raise
        `HotelSearchError` on failure; nothing found is an empty list."""
        ...

    async def aclose(self) -> None: ...
