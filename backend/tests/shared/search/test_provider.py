import pytest

from shared.config import Settings
from shared.search.fake import FakeSearchProvider
from shared.search.provider import UnavailableProvider, create_search_provider
from shared.search.tavily import TavilyProvider
from shared.search.types import SearchError


def test_fake_provider_is_selected(settings: Settings) -> None:
    configured = settings.model_copy(update={"search_provider": "fake"})

    assert isinstance(create_search_provider(configured), FakeSearchProvider)


async def test_tavily_without_a_key_is_unavailable(settings: Settings) -> None:
    configured = settings.model_copy(update={"search_provider": "tavily", "tavily_api_key": None})

    provider = create_search_provider(configured)

    assert isinstance(provider, UnavailableProvider)
    with pytest.raises(SearchError, match="TAVILY_API_KEY"):
        await provider.search("hotels in Tokyo", "hotels")


async def test_tavily_with_a_key(settings: Settings) -> None:
    configured = settings.model_copy(
        update={"search_provider": "tavily", "tavily_api_key": "tvly-test"}
    )

    provider = create_search_provider(configured)

    assert isinstance(provider, TavilyProvider)
    await provider.aclose()


async def test_fake_provider_makes_up_placeholder_results() -> None:
    results = await FakeSearchProvider().search("hotels in Tokyo", "hotels", max_results=2)

    assert len(results) == 2
    assert all(result.url.startswith("https://example.com/hotels/") for result in results)
