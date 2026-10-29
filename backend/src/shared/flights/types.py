"""Provider-neutral flight search types: the query, the options found, the
`FlightProvider` protocol and its error.

Every time here is an aware datetime in its airport's own timezone, so a flight's start
and end can be copied onto an itinerary item as they are: nothing downstream (least of all
the model) has to work out a date or convert a timezone.
"""

from datetime import date, datetime
from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.domain import Money

# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------

# The same cabins as an itinerary flight's `cabin`.
FlightCabin = Literal["economy", "premium", "business", "first"]
AirportCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
# A few airports per end, e.g. both of Tokyo's (HND, NRT).
AirportCodes = Annotated[tuple[AirportCode, ...], Field(min_length=1, max_length=4)]


class FlightQuery(BaseModel):
    """What to search for. With a `return_date` it's a round trip: the search lists flights
    out, each priced for the whole trip, and the flights back for one of them are listed by
    searching again with its `departure_token`."""

    model_config = ConfigDict(frozen=True)

    origin: AirportCodes
    destination: AirportCodes
    depart_date: date
    return_date: date | None = None
    cabin: FlightCabin = "economy"
    adults: int = Field(default=1, ge=1, le=9)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")

    @model_validator(mode="after")
    def _returns_after_departing(self) -> Self:
        if self.return_date is not None and self.return_date < self.depart_date:
            raise ValueError("return_date must be on or after depart_date")
        return self


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


class FlightStop(BaseModel):
    """Where and when a flight segment leaves or lands."""

    iata: AirportCode
    name: str
    time: datetime  # aware, in `timezone`
    timezone: str  # IANA zone of the airport


class FlightSegment(BaseModel):
    """One takeoff and landing."""

    departure: FlightStop
    arrival: FlightStop
    airline: str
    flight_number: str | None = None  # "QR 728"
    duration_minutes: int = Field(ge=0)
    aircraft: str | None = None
    overnight: bool = False


class FlightLayover(BaseModel):
    iata: AirportCode
    name: str
    duration_minutes: int = Field(ge=0)
    overnight: bool = False


class FlightOption(BaseModel):
    """One way to fly from the origin to the destination: one or more segments."""

    segments: list[FlightSegment] = Field(min_length=1)
    layovers: list[FlightLayover] = Field(default_factory=list)
    total_duration_minutes: int = Field(ge=0)
    # For everyone searched for; a round trip's price covers both directions.
    price: Money | None = None
    # A round trip's flight out: search again with this to list the flights back.
    departure_token: str | None = None
    # A complete selection: what a booking-options lookup would take.
    booking_token: str | None = None

    @property
    def departure(self) -> FlightStop:
        return self.segments[0].departure

    @property
    def arrival(self) -> FlightStop:
        return self.segments[-1].arrival

    @property
    def airlines(self) -> list[str]:
        """Each airline flown, once, in order."""
        return list(dict.fromkeys(segment.airline for segment in self.segments))


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class FlightSearchError(Exception):
    """A search that couldn't be completed. The message is safe to show the user/model."""


class FlightProvider(Protocol):
    async def search(
        self, query: FlightQuery, *, departure_token: str | None = None
    ) -> list[FlightOption]:
        """The options for `query`, best first; with `departure_token` (from a round
        trip's flight out), the flights back for it. Raise `FlightSearchError` on failure;
        no flights is an empty list."""
        ...

    async def aclose(self) -> None: ...
