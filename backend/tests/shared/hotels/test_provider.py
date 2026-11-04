"""Hotel provider construction, the fake provider and the query."""

from datetime import date

import pytest
from pydantic import ValidationError

from shared.config import Settings
from shared.hotels.fake import FakeHotelProvider
from shared.hotels.provider import UnavailableHotelProvider, create_hotel_provider
from shared.hotels.serpapi import SerpApiHotelProvider
from shared.hotels.types import HotelQuery, HotelSearchError

QUERY = HotelQuery(location="Lisbon", check_in=date(2027, 5, 1), check_out=date(2027, 5, 4))


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_fake_provider_is_selected(settings: Settings) -> None:
    assert isinstance(create_hotel_provider(settings), FakeHotelProvider)


async def test_serpapi_without_a_key_is_unavailable(settings: Settings) -> None:
    configured = settings.model_copy(update={"hotel_provider": "serpapi", "serpapi_api_key": None})

    provider = create_hotel_provider(configured)

    assert isinstance(provider, UnavailableHotelProvider)
    with pytest.raises(HotelSearchError, match="SERPAPI_API_KEY"):
        await provider.search(QUERY)


async def test_serpapi_with_a_key(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"hotel_provider": "serpapi", "serpapi_api_key": "serp-test"}
    )

    provider = create_hotel_provider(configured)

    assert isinstance(provider, SerpApiHotelProvider)
    await provider.aclose()


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------


async def test_fake_hotels_are_two_per_class_asked_for() -> None:
    provider = FakeHotelProvider()

    hotels = await provider.search(QUERY.model_copy(update={"stars": (4,)}))

    assert [(h.name, h.stars) for h in hotels] == [
        ("Example Grand Hotel", 4),
        ("Example Harbour Hotel", 4),
    ]
    grand = hotels[0]
    assert grand.nightly_rate is not None and grand.nightly_rate.amount == 280
    # Three nights.
    assert grand.total_rate is not None and grand.total_rate.amount == 840
    assert provider.searches == [QUERY.model_copy(update={"stars": (4,)})]


async def test_fake_rentals() -> None:
    rentals = await FakeHotelProvider().search(QUERY.model_copy(update={"vacation_rentals": True}))

    assert {rental.kind for rental in rentals} == {"vacation_rental"}


async def test_fake_replies_are_scripted_in_order() -> None:
    provider = FakeHotelProvider([[], HotelSearchError("Hotel search timed out.")])

    assert await provider.search(QUERY) == []
    with pytest.raises(HotelSearchError, match="timed out"):
        await provider.search(QUERY)


# ---------------------------------------------------------------------------
# Query
# ---------------------------------------------------------------------------


def test_a_query_needs_at_least_one_night() -> None:
    with pytest.raises(ValidationError, match="check_out must be after check_in"):
        HotelQuery.model_validate({**QUERY.model_dump(), "check_out": QUERY.check_in})


def test_nights() -> None:
    assert QUERY.nights == 3
