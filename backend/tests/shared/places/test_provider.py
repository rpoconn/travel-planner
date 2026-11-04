"""Place provider construction, the fake provider and the query."""

import pytest
from pydantic import ValidationError

from shared.config import Settings
from shared.places.fake import FakePlaceProvider
from shared.places.provider import UnavailablePlaceProvider, create_place_provider
from shared.places.serpapi import SerpApiPlaceProvider
from shared.places.types import PlaceQuery, PlaceSearchError

QUERY = PlaceQuery(what="museums", near="Lisbon")


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_fake_provider_is_selected(settings: Settings) -> None:
    assert isinstance(create_place_provider(settings), FakePlaceProvider)


async def test_serpapi_without_a_key_is_unavailable(settings: Settings) -> None:
    configured = settings.model_copy(update={"places_provider": "serpapi", "serpapi_api_key": None})

    provider = create_place_provider(configured)

    assert isinstance(provider, UnavailablePlaceProvider)
    with pytest.raises(PlaceSearchError, match="SERPAPI_API_KEY"):
        await provider.search(QUERY)


async def test_serpapi_with_a_key(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"places_provider": "serpapi", "serpapi_api_key": "serp-test"}
    )

    provider = create_place_provider(configured)

    assert isinstance(provider, SerpApiPlaceProvider)
    await provider.aclose()


# ---------------------------------------------------------------------------
# Fake
# ---------------------------------------------------------------------------


async def test_the_fake_makes_up_two_places_per_kind() -> None:
    fake = FakePlaceProvider()

    museums = await fake.search(QUERY)
    other = await fake.search(QUERY.model_copy(update={"what": "vegan restaurants"}))

    assert [p.name for p in museums] == ["Example Art Museum", "Example History Museum"]
    # Museums are closed on Mondays.
    assert museums[0].hours is not None and 0 not in {h.weekday for h in museums[0].hours}
    assert [p.name for p in other] == [
        "Example Vegan restaurants One",
        "Example Vegan restaurants Two",
    ]
    assert fake.searches == [QUERY, QUERY.model_copy(update={"what": "vegan restaurants"})]


async def test_the_fake_replays_queued_replies() -> None:
    fake = FakePlaceProvider([[], PlaceSearchError("down")])

    assert await fake.search(QUERY) == []
    with pytest.raises(PlaceSearchError, match="down"):
        await fake.search(QUERY)
    assert len(await fake.search(QUERY)) == 2


def test_a_query_needs_what_and_where() -> None:
    with pytest.raises(ValidationError):
        PlaceQuery(what="", near="Lisbon")
    with pytest.raises(ValidationError):
        PlaceQuery(what="museums", near="Lisbon", lat=91)
