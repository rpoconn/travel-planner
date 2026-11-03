"""Flight provider construction and the `FlightsDep` dependency."""

import logging
from typing import Annotated

from fastapi import Depends, Request

from shared.config import Settings
from shared.flights.fake import FakeFlightProvider
from shared.flights.serpapi import SerpApiFlightProvider
from shared.flights.types import FlightOption, FlightProvider, FlightQuery, FlightSearchError

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "Flight search is not configured (set SERPAPI_API_KEY to enable it)."


class UnavailableFlightProvider:
    """Stands in when no flight backend is configured, so the agent learns why it can't
    search."""

    async def search(
        self, query: FlightQuery, *, departure_token: str | None = None
    ) -> list[FlightOption]:
        raise FlightSearchError(NOT_CONFIGURED)

    async def aclose(self) -> None:
        return None


def create_flight_provider(settings: Settings) -> FlightProvider:
    """Build the app-wide provider for `settings.flight_provider`; stored on app.state."""
    if settings.flight_provider == "fake":
        return FakeFlightProvider()
    if not settings.serpapi_api_key:
        logger.warning("SERPAPI_API_KEY is not set; flight search will report it")
        return UnavailableFlightProvider()
    return SerpApiFlightProvider(
        api_key=settings.serpapi_api_key,
        timeout_seconds=settings.flight_search_timeout_seconds,
    )


def get_flights(request: Request) -> FlightProvider:
    """The app's provider (tests swap `app.state.flights` for a scripted `FakeFlightProvider`)."""
    provider: FlightProvider = request.app.state.flights
    return provider


FlightsDep = Annotated[FlightProvider, Depends(get_flights)]
