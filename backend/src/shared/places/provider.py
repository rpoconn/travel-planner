"""Place provider construction and the `PlacesDep` dependency."""

import logging
from typing import Annotated

from fastapi import Depends, Request

from shared.config import Settings
from shared.places.fake import FakePlaceProvider
from shared.places.serpapi import SerpApiPlaceProvider
from shared.places.types import PlaceOption, PlaceProvider, PlaceQuery, PlaceSearchError

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "Place search is not configured (set SERPAPI_API_KEY to enable it)."


class UnavailablePlaceProvider:
    """Stands in when no place backend is configured, so the activities stage says why it
    can't search."""

    async def search(self, query: PlaceQuery) -> list[PlaceOption]:
        raise PlaceSearchError(NOT_CONFIGURED)

    async def aclose(self) -> None:
        return None


def create_place_provider(settings: Settings) -> PlaceProvider:
    """Build the app-wide provider for `settings.places_provider`; stored on app.state."""
    if settings.places_provider == "fake":
        return FakePlaceProvider()
    if not settings.serpapi_api_key:
        logger.warning("SERPAPI_API_KEY is not set; place search will report it")
        return UnavailablePlaceProvider()
    return SerpApiPlaceProvider(
        api_key=settings.serpapi_api_key,
        timeout_seconds=settings.places_search_timeout_seconds,
    )


def get_places(request: Request) -> PlaceProvider:
    """The app's provider (tests swap `app.state.places` for a scripted `FakePlaceProvider`)."""
    provider: PlaceProvider = request.app.state.places
    return provider


PlacesDep = Annotated[PlaceProvider, Depends(get_places)]
