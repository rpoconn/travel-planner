"""Hotel provider construction and the `HotelsDep` dependency."""

import logging
from typing import Annotated

from fastapi import Depends, Request

from shared.config import Settings
from shared.hotels.fake import FakeHotelProvider
from shared.hotels.serpapi import SerpApiHotelProvider
from shared.hotels.types import HotelOption, HotelProvider, HotelQuery, HotelSearchError

logger = logging.getLogger(__name__)

NOT_CONFIGURED = "Hotel search is not configured (set SERPAPI_API_KEY to enable it)."


class UnavailableHotelProvider:
    """Stands in when no hotel backend is configured, so the lodging stage says why it
    can't search."""

    async def search(self, query: HotelQuery) -> list[HotelOption]:
        raise HotelSearchError(NOT_CONFIGURED)

    async def aclose(self) -> None:
        return None


def create_hotel_provider(settings: Settings) -> HotelProvider:
    """Build the app-wide provider for `settings.hotel_provider`; stored on app.state."""
    if settings.hotel_provider == "fake":
        return FakeHotelProvider()
    if not settings.serpapi_api_key:
        logger.warning("SERPAPI_API_KEY is not set; hotel search will report it")
        return UnavailableHotelProvider()
    return SerpApiHotelProvider(
        api_key=settings.serpapi_api_key,
        timeout_seconds=settings.hotel_search_timeout_seconds,
    )


def get_hotels(request: Request) -> HotelProvider:
    """The app's provider (tests swap `app.state.hotels` for a scripted `FakeHotelProvider`)."""
    provider: HotelProvider = request.app.state.hotels
    return provider


HotelsDep = Annotated[HotelProvider, Depends(get_hotels)]
