"""Smoke tests against a real Ollama server: `uv run pytest -m ollama`.

They use OLLAMA_URL / OLLAMA_MODEL from the environment (or `.env`) and skip when the
server or model isn't available. Assertions are loose on purpose: a small local model's
wording varies from run to run, so only the structure of its behavior is checked.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

from features.chat.guardrail import check_topic
from features.chat.schemas import DoneEvent, ToolResultEvent
from shared.config import Settings, get_settings
from shared.llm.ollama_client import OllamaClient
from shared.search.fake import FakeSearchProvider
from tests.features.chat.conftest import send

pytestmark = pytest.mark.ollama


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # Same test DB/JWT settings, but the real model.
    env = get_settings()
    return settings.model_copy(
        update={
            "llm_provider": "ollama",
            "ollama_url": env.ollama_url,
            "ollama_model": env.ollama_model,
        }
    )


@pytest.fixture(autouse=True)
async def require_ollama(settings: Settings) -> None:
    try:
        async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=5) as http:
            response = await http.get("/api/tags")
    except httpx.HTTPError:
        pytest.skip(f"Ollama is not reachable at {settings.ollama_url}")
    names = {model["name"] for model in response.json().get("models", [])}
    if settings.ollama_model not in names:
        pytest.skip(f"Model {settings.ollama_model} is not pulled")


@pytest.fixture
async def llm(settings: Settings) -> AsyncIterator[OllamaClient]:
    client = OllamaClient(host=settings.ollama_url, model=settings.ollama_model)
    yield client
    await client.aclose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_guardrail_separates_travel_from_off_topic(llm: OllamaClient) -> None:
    travel = await check_topic(llm, [], "Find me a hotel near Shinjuku station for 4 nights")
    off_topic = await check_topic(llm, [], "Write a Python function that reverses a string")

    assert travel.allowed and travel.verdict is not None
    assert not off_topic.allowed


async def test_agent_adds_a_hotel(
    client: AsyncClient, headers: dict[str, str], plan: dict[str, Any]
) -> None:
    events = await send(
        client,
        headers,
        plan["id"],
        "Add Hotel Okura in Tokyo to my plan: check in 2027-05-02 at 15:00, check out "
        "2027-05-06 at 11:00.",
    )

    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert any(e.name == "add_lodging" and e.ok for e in results), results
    assert isinstance(events[-1], DoneEvent)
    assert events[-1].message.content.strip()


async def test_agent_searches_the_web_for_options(
    client: AsyncClient,
    headers: dict[str, str],
    plan: dict[str, Any],
    fake_search: FakeSearchProvider,
) -> None:
    # Placeholder results keep this independent of a Tavily key / network access.
    events = await send(
        client, headers, plan["id"], "What are some good ryokan near Tokyo? Look a few up."
    )

    results = [e for e in events if isinstance(e, ToolResultEvent)]
    assert any(e.name == "web_search" and e.ok for e in results), results
    assert fake_search.searches
    assert isinstance(events[-1], DoneEvent)
